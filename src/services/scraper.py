import os
import asyncio
import html
import re
import urllib.parse
from datetime import datetime
import httpx
from bs4 import BeautifulSoup

from src.core.config import BASE_DIR, COURSE_MAP, LEGACY_URL, NEP_URL
from src.services.html_parser import (
    normalize_title,
    parse_hyu_html,
    is_regular_or_private_exam,
    is_strictly_regular_or_private,
)
from src.services.roll_parser import parse_roll, classify_roll
from src.services.results import save_result_to_db

async def scrape_exam_async(client: httpx.AsyncClient, exam_title: str, exam_link: str, rollno: str, semaphore: asyncio.Semaphore):
    parsed_url = urllib.parse.urlparse(exam_link)
    domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    async with semaphore:
        for attempt in range(3):
            try:
                r = await client.get(exam_link, headers=headers, timeout=12.0)
                if r.status_code != 200:
                    await asyncio.sleep(0.5)
                    continue
                    
                soup = BeautifulSoup(r.text, 'html.parser')
                token_elem = soup.find('input', {'name': '_token'})
                if not token_elem:
                    await asyncio.sleep(0.5)
                    continue
                token = token_elem['value']
                
                payload = {
                    'COURSECD':   soup.find('input', {'name': 'COURSECD'})['value'] if soup.find('input', {'name': 'COURSECD'}) else '',
                    'SEMCODE':    soup.find('input', {'name': 'SEMCODE'})['value'] if soup.find('input', {'name': 'SEMCODE'}) else '',
                    'RESULTTYPE': soup.find('input', {'name': 'RESULTTYPE'})['value'] if soup.find('input', {'name': 'RESULTTYPE'}) else '',
                    'session':    soup.find('input', {'name': 'session'})['value'] if soup.find('input', {'name': 'session'}) else '',
                    'tcc':        soup.find('input', {'name': 'tcc'})['value'] if soup.find('input', {'name': 'tcc'}) else '',
                    'p1': '', 'all': ''
                }
                
                ajax_url = f"{domain}/get-result-details"
                post_headers = {
                    'User-Agent': headers['User-Agent'],
                    'X-Requested-With': 'XMLHttpRequest',
                    'X-CSRF-TOKEN': token,
                    'Origin': domain,
                    'Referer': exam_link,
                    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'
                }
                
                data = payload.copy()
                data['EXAMROLLNUMBER'] = rollno
                data['_token'] = token
                
                res = await client.post(ajax_url, data=data, headers=post_headers, timeout=12.0)
                if res.status_code == 200:
                    res_json = res.json()
                    if res_json.get("status") is False:
                        return None
                    if res_json.get("status") is True and "html" in res_json:
                        html_content = res_json["html"]
                        if len(html_content) > 200:
                            parsed_details = parse_hyu_html(html_content)
                            return {
                                "exam_title": parsed_details["exam_title"] or exam_title,
                                "student_info": parsed_details["student_info"],
                                "result_status": parsed_details["result_status"],
                                "sgpa": parsed_details["sgpa"],
                                "html": html_content,
                                "official_url": f"{domain}/result-details?tcc={payload['tcc']}&rollno={rollno}"
                            }
                await asyncio.sleep(0.5)
            except Exception:
                await asyncio.sleep(0.5)
                
        return None

async def fetch_latest_batches() -> list:
    """
    Downloads raw HTML index pages from university portals, saves them to disk,
    and returns parsed candidate batch dictionaries.
    """
    latest_batches = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
    }
    
    # 1. Fetch Legacy Index
    legacy_file = os.path.join(BASE_DIR, "legacy_index.html")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(LEGACY_URL, headers=headers)
            if r.status_code == 200:
                with open(legacy_file, "w", encoding="utf-8") as f:
                    f.write(r.text)
                print(f"Saved raw legacy index HTML ({len(r.text):,} chars) to {legacy_file}")
                
                tbody_match = re.search(r'<tbody style="font-family: Arial; font-weight: bold; font-size: 13px;">(.*?)</tbody>', r.text, re.IGNORECASE | re.DOTALL)
                tbody_text = tbody_match.group(1) if tbody_match else r.text
                
                tr_pattern = re.compile(r'<tr.*?>(.*?)</tr>', re.IGNORECASE | re.DOTALL)
                td_pattern = re.compile(r'<td.*?>(.*?)</td>', re.IGNORECASE | re.DOTALL)
                
                for tr_match in tr_pattern.finditer(tbody_text):
                    tr_content = tr_match.group(1)
                    tds = td_pattern.findall(tr_content)
                    if len(tds) >= 3:
                        desc_raw = tds[0]
                        desc_raw = re.sub(r'<[^>]+>', ' ', desc_raw)
                        desc_raw = html.unescape(desc_raw).replace('\xa0', ' ')
                        desc = re.sub(r'\s+', ' ', desc_raw).strip()
                        
                        if not is_strictly_regular_or_private(desc, "legacy"):
                            continue
                        
                        td1 = tds[1]
                        link_match = re.search(r'href=[\'"]([^\'"]+)[\'"]', td1)
                        link = link_match.group(1) if link_match else ''
                        
                        pub_date_raw = tds[2]
                        pub_date = re.sub(r'<[^>]+>', '', pub_date_raw).strip()
                        
                        year = None
                        try:
                            if pub_date:
                                year = datetime.strptime(pub_date, "%d/%m/%Y").year
                        except Exception:
                            pass
                            
                        if desc and link:
                            latest_batches.append({
                                "description": desc,
                                "link": link,
                                "publication_date": pub_date,
                                "year": str(year) if year else "",
                                "source": "legacy"
                            })
    except Exception as e:
        print(f"Error fetching legacy index: {e}")
        
    # 2. Fetch NEP Index
    nep_file = os.path.join(BASE_DIR, "nep_index.html")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(NEP_URL, headers=headers)
            if r.status_code == 200:
                with open(nep_file, "w", encoding="utf-8") as f:
                    f.write(r.text)
                print(f"Saved raw NEP index HTML ({len(r.text):,} chars) to {nep_file}")
                
                soup = BeautifulSoup(r.text, 'html.parser')
                table = soup.find("table", id="print_data")
                if table:
                    rows = table.find("tbody").find_all("tr")
                    
                    course_prefixes = {
                        "Bachelor of Arts (B.A) (UG)": "B.A.",
                        "Bachelor of Science (B.Sc) (UG)": "B.Sc.",
                        "Bachelor of Commerce (B.Com) (UG)": "B.Com.",
                        "Bachelor of Computer Application (BCA) (UG)": "B.C.A.",
                        "Bachelor of Business Administration (BBA) (UG)": "B.B.A."
                    }
                    semester_map = {
                        "First Semester (Sem - 1)": "1st Sem",
                        "Second Semester (Sem - 2)": "2nd Sem",
                        "Third Semester (Sem - 3)": "3rd Sem",
                        "Fourth Semester (Sem - 4)": "4th Sem",
                        "Fifth Semester (Sem - 5)": "5th Sem",
                        "Sixth Semester (Sem - 6)": "6th Sem"
                    }
                    
                    for row in rows:
                        cols = row.find_all("td")
                        if len(cols) < 3:
                            continue
                        
                        desc_text = re.sub(r'\s+', ' ', html.unescape(cols[0].get_text()).replace('\xa0', ' ')).strip()
                        
                        if not is_strictly_regular_or_private(desc_text, "nep"):
                            continue
                            
                        pub_date_text = re.sub(r'\s+', ' ', cols[2].get_text().replace('\xa0', ' ')).strip()
                        
                        match_date = re.search(r'(\d{2})/(\d{2})/(\d{4})', pub_date_text)
                        if not match_date:
                            continue
                        day, month, year_num = map(int, match_date.groups())
                        
                        course_prefix = None
                        for k, v in course_prefixes.items():
                            if k in desc_text:
                                course_prefix = v
                                break
                        if not course_prefix:
                            continue
                            
                        sem_text = None
                        for k, v in semester_map.items():
                            if k in desc_text:
                                sem_text = v
                                break
                        if not sem_text:
                            continue
                            
                        is_odd = sem_text in ["1st Sem", "3rd Sem", "5th Sem"]
                        session_year_num = year_num - 1 if month == 1 else year_num
                        
                        if is_odd:
                            session_str = f"Dec-Jan {session_year_num - 1}-{str(session_year_num)[-2:]}"
                        else:
                            session_str = f"May-June {session_year_num}"
                            
                        course_name = f"{course_prefix} {sem_text} (NEP) {session_str}"
                        
                        a_tag = cols[1].find("a")
                        if a_tag and a_tag.get("href"):
                            latest_batches.append({
                                "description": course_name,
                                "link": a_tag.get("href"),
                                "publication_date": pub_date_text,
                                "year": str(session_year_num),
                                "source": "nep"
                            })
    except Exception as e:
        print(f"Error fetching NEP index: {e}")
        
    return latest_batches

async def execute_live_search(rollno: str, configs: list, seen_titles: set, marksheets: list):
    """
    Matches roll number to candidate exams, runs concurrent parallel scraping against HYV portal,
    and caches new results into RDS.
    """
    roll_info = parse_roll(rollno)
    year_of_admission = roll_info["year"]
    course_code = roll_info["course_code"]
    
    if not year_of_admission:
        return marksheets
        
    roll_level = classify_roll(rollno, roll_info)
    
    targeted_candidates = []
    if course_code and course_code in COURSE_MAP:
        mapping = COURSE_MAP[course_code]
        keywords = mapping["keywords"]
        exclusions = mapping.get("exclusions", [])
        
        for entry in configs:
            title = entry["description"].lower()
            entry_year = entry["year"]
            
            if roll_info["length"] == 8:
                if entry_year and entry_year != year_of_admission:
                    continue
            else:
                if entry_year and not (entry_year >= year_of_admission and entry_year <= year_of_admission + 5):
                    continue
                
            if not is_regular_or_private_exam(entry["description"], entry["link"]):
                continue
                
            if any(kw in title for kw in keywords) and not any(ex in title for ex in exclusions):
                targeted_candidates.append(entry)
    elif roll_info["length"] == 8:
        # Scheme B: 8-digit annual rolls do not encode course code.
        # Prioritize major undergraduate & integrated annual exams for that specific exam year:
        major_annual_keywords = [
            "b.a.", "b.com", "b.sc", "bca", "b.c.a",
            "bachelor of arts", "bachelor of science", "bachelor of commerce",
            "computer application", "b.sc.-b.ed", "b.a.-b.ed", "b.lib"
        ]
        for entry in configs:
            title = entry["description"].lower()
            entry_year = entry["year"]
            
            if entry_year and entry_year != year_of_admission:
                continue
            if not is_regular_or_private_exam(entry["description"], entry["link"]):
                continue
            if "nep" in title or "durgnep" in entry["link"]:
                continue
                
            if any(kw in title for kw in major_annual_keywords):
                targeted_candidates.append(entry)
                
    broader_candidates = []
    for entry in configs:
        if entry in targeted_candidates:
            continue
            
        title = entry["description"].lower()
        entry_year = entry["year"]
        
        if roll_info["length"] == 8:
            if entry_year and entry_year != year_of_admission:
                continue
        else:
            if entry_year and not (entry_year >= year_of_admission and entry_year <= year_of_admission + 5):
                continue
            
        if not is_regular_or_private_exam(entry["description"], entry["link"]):
            continue
            
        is_nep_exam = "nep" in title or "durgnep" in entry["link"]
        is_pg_exam = any(k in title for k in ["m.a.", "m.sc", "m.com", "mca", "m.c.a", "m.b.a", "mba", "m.ed", "master", "post graduate", "pg"])
        is_ug_exam = not is_pg_exam
        
        if roll_level == "nep":
            if is_nep_exam:
                broader_candidates.append(entry)
        elif roll_level == "pg":
            if is_pg_exam or "b.ed" in title or "education" in title or is_nep_exam:
                broader_candidates.append(entry)
        elif roll_level == "annual":
            if not is_nep_exam:
                broader_candidates.append(entry)
        else:
            if is_ug_exam and not is_nep_exam:
                broader_candidates.append(entry)
                
    def fallback_priority_key(x):
        t = x["description"].lower()
        is_major = any(kw in t for kw in ["b.a.", "b.sc", "b.com", "bca", "bachelor of arts", "bachelor of science", "bachelor of commerce", "computer application"])
        major_val = 0 if is_major else 1
        midpoint = year_of_admission + 1.5
        dist = abs((x["year"] or midpoint) - midpoint)
        return (major_val, dist)
        
    broader_candidates.sort(key=fallback_priority_key)
    
    # Prioritize targeted candidate exams; fall back to broader candidates if no targeted matches
    candidate_exams = targeted_candidates if targeted_candidates else broader_candidates
    
    async with httpx.AsyncClient(follow_redirects=True, limits=httpx.Limits(max_keepalive_connections=5, max_connections=8), timeout=15.0) as client:
        semaphore = asyncio.Semaphore(4)
        
        if candidate_exams:
            tasks = [
                scrape_exam_async(client, exam['description'], exam['link'], rollno, semaphore)
                for exam in candidate_exams
            ]
            results = await asyncio.gather(*tasks)
            for res in results:
                if res:
                    roll_val = res.get("student_info", {}).get("roll_no") or rollno
                    norm = f"{roll_val}_{normalize_title(res.get('exam_title', ''))}"
                    if norm not in seen_titles:
                        seen_titles.add(norm)
                        marksheets.append(res)
                        save_result_to_db(roll_val, res)
                        
        # If targeted candidates found marksheets, we're done. Otherwise, run fallback scans.
        if targeted_candidates and not marksheets:
            print(f"Targeted candidates returned no results for {rollno}. Trying broader candidates...")
            broader_tasks = [
                scrape_exam_async(client, exam['description'], exam['link'], rollno, semaphore)
                for exam in broader_candidates
            ]
            results = await asyncio.gather(*broader_tasks)
            for res in results:
                if res:
                    roll_val = res.get("student_info", {}).get("roll_no") or rollno
                    norm = f"{roll_val}_{normalize_title(res.get('exam_title', ''))}"
                    if norm not in seen_titles:
                        seen_titles.add(norm)
                        marksheets.append(res)
                        save_result_to_db(roll_val, res)

        # Brute Force fallback scan (2022 to 2026) only if still no marksheets were found
        if not marksheets:
            print(f"Running brute force search (2022-2026) for {rollno} to catch any unclassified results...")
            brute_candidates = []
            for entry in configs:
                title = entry["description"].lower()
                entry_year = entry["year"]
                
                if not entry_year or not (entry_year >= 2022 and entry_year <= 2026):
                    continue
                    
                if not is_regular_or_private_exam(entry["description"], entry["link"]):
                    continue
                    
                if entry in candidate_exams or entry in broader_candidates:
                    continue
                    
                is_nep_exam = "nep" in title or "durgnep" in entry["link"]
                is_pg_exam = any(k in title for k in ["m.a.", "m.sc", "m.com", "mca", "m.c.a", "m.b.a", "mba", "m.ed", "master", "post graduate", "pg"])
                is_ug_exam = not is_pg_exam
                
                if roll_level == "nep":
                    if is_nep_exam:
                        brute_candidates.append(entry)
                elif roll_level == "pg":
                    if is_pg_exam or "b.ed" in title or "education" in title:
                        brute_candidates.append(entry)
                else:
                    if is_ug_exam and not is_nep_exam:
                        brute_candidates.append(entry)
                        
            brute_candidates.sort(key=fallback_priority_key)
            
            if brute_candidates:
                tasks = [
                    scrape_exam_async(client, exam['description'], exam['link'], rollno, semaphore)
                    for exam in brute_candidates
                ]
                results = await asyncio.gather(*tasks)
                for res in results:
                    if res:
                        roll_val = res.get("student_info", {}).get("roll_no") or rollno
                        norm = f"{roll_val}_{normalize_title(res.get('exam_title', ''))}"
                        if norm not in seen_titles:
                            seen_titles.add(norm)
                            marksheets.append(res)
                            save_result_to_db(roll_val, res)
                            
    # Multi-Year Transcript Stitching: Retrieve all academic years using enrollment numbers
    if marksheets:
        enrollment_nos = {
            res.get("student_info", {}).get("enrollment_no")
            for res in marksheets
            if res.get("student_info", {}).get("enrollment_no")
        }
        for enroll in enrollment_nos:
            try:
                from src.services.results import get_local_results_from_db
                historical_records = get_local_results_from_db(enroll)
                for h_res in historical_records:
                    h_roll = h_res.get("student_info", {}).get("roll_no") or ""
                    h_norm = f"{h_roll}_{normalize_title(h_res.get('exam_title', ''))}"
                    if h_norm not in seen_titles:
                        seen_titles.add(h_norm)
                        marksheets.append(h_res)
            except Exception as e:
                print(f"Error fetching multi-year history for enrollment {enroll}: {e}")

    return marksheets
