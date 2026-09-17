import time
from src.core.database import get_db_conn
from src.services.html_parser import parse_hyu_html

def get_local_results_from_db(rollno: str) -> list:
    results = []
    try:
        query_str = rollno.strip()
        if not query_str:
            return []
        
        with get_db_conn() as conn:
            with conn.cursor() as cursor:
                # 1. Direct search by enrollment_no OR roll_number
                if query_str.isdigit():
                    roll_val = int(query_str)
                    cursor.execute("""
                        SELECT enrollment_no, roll_number, name, exam_title, result_status, sgpa, html_result, official_url 
                        FROM results 
                        WHERE roll_number = %s OR UPPER(enrollment_no) = UPPER(%s)
                    """, (roll_val, query_str))
                else:
                    cursor.execute("""
                        SELECT enrollment_no, roll_number, name, exam_title, result_status, sgpa, html_result, official_url 
                        FROM results 
                        WHERE UPPER(enrollment_no) = UPPER(%s)
                    """, (query_str,))
                rows = cursor.fetchall()

                # 2. Extract enrollment numbers to fetch ALL academic years for this student
                enrollment_nos = {r[0] for r in rows if r[0]}
                if enrollment_nos:
                    cursor.execute("""
                        SELECT enrollment_no, roll_number, name, exam_title, result_status, sgpa, html_result, official_url 
                        FROM results 
                        WHERE enrollment_no = ANY(%s)
                    """, (list(enrollment_nos),))
                    rows = cursor.fetchall()
                elif not rows:
                    # 3. Search by Name
                    query_words = query_str.split()
                    if query_words:
                        conditions = " AND ".join(["LOWER(name) LIKE %s" for _ in query_words])
                        params = [f"%{word.lower()}%" for word in query_words]
                        cursor.execute(f"""
                            SELECT enrollment_no, roll_number, name, exam_title, result_status, sgpa, html_result, official_url 
                            FROM results 
                            WHERE {conditions}
                            ORDER BY name ASC, roll_number ASC
                            LIMIT 100
                        """, params)
                        rows = cursor.fetchall()
                        
                        # Multi-semester expansion for matched students with enrollment numbers
                        found_enr = list({r[0] for r in rows if r[0]})
                        if found_enr:
                            cursor.execute("""
                                SELECT enrollment_no, roll_number, name, exam_title, result_status, sgpa, html_result, official_url 
                                FROM results 
                                WHERE enrollment_no = ANY(%s)
                                ORDER BY name ASC, roll_number ASC
                            """, (found_enr,))
                            expanded_rows = cursor.fetchall()
                            rows_without_enr = [r for r in rows if not r[0]]
                            rows = expanded_rows + rows_without_enr

        seen_keys = set()
        for row in rows:
            enroll_val = str(row[0]) if row[0] else ""
            roll_val = str(row[1]) if row[1] else ""
            exam_title = row[3]
            result_status = row[4]
            sgpa = row[5]
            html_content = row[6]
            official_url = row[7]
            
            dedup_key = f"{enroll_val or roll_val}_{exam_title}"
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            
            parsed_details = parse_hyu_html(html_content)
            student_info = parsed_details.get("student_info", {})
            if not student_info.get("name") and row[2]:
                student_info["name"] = row[2]
            if not student_info.get("roll_no") and roll_val:
                student_info["roll_no"] = roll_val
            if not student_info.get("enrollment_no") and enroll_val:
                student_info["enrollment_no"] = enroll_val

            results.append({
                "exam_title": exam_title,
                "student_info": student_info,
                "result_status": result_status,
                "sgpa": sgpa,
                "html": html_content,
                "official_url": official_url
            })
    except Exception as e:
        print(f"Error reading result from Amazon RDS database for {rollno}: {e}")
    return results

def save_result_to_db(rollno: str, result: dict):
    try:
        roll_val = int(rollno) if str(rollno).isdigit() else 0
        if not roll_val:
            return
        
        student_info = result.get("student_info", {})
        name = student_info.get("name", "")
        enrollment_no = student_info.get("enrollment_no", "")
        exam_title = result.get("exam_title", "Pre-Scraped Exam Result")
        result_status = result.get("result_status", "N/A")
        sgpa = result.get("sgpa", "N/A")
        html_result = result.get("html", "")
        official_url = result.get("official_url", "")
        updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        
        insert_query = """
            INSERT INTO results (
                enrollment_no, roll_number, name, exam_title, result_status, sgpa, html_result, official_url, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (enrollment_no, roll_number, exam_title) DO UPDATE SET
                name = EXCLUDED.name,
                result_status = EXCLUDED.result_status,
                sgpa = EXCLUDED.sgpa,
                html_result = EXCLUDED.html_result,
                official_url = EXCLUDED.official_url,
                updated_at = EXCLUDED.updated_at
        """
        with get_db_conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(insert_query, (enrollment_no, roll_val, name, exam_title, result_status, sgpa, html_result, official_url, updated_at))
            conn.commit()
        print(f"Cached live result for {rollno} [{enrollment_no}] ({exam_title}) into Amazon RDS database.")
    except Exception as e:
        print(f"Error caching live result to Amazon RDS for {rollno}: {e}")
