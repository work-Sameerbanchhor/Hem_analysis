import asyncio
import time
import urllib.parse
import httpx
from bs4 import BeautifulSoup

from src.core.config import CRAWL_STATUS, COLLEGES, UNIFIED_CONFIGS, COURSE_MAP
from src.core.database import get_db_conn
from src.services.scraper import fetch_latest_batches, scrape_exam_with_payload_async
from src.services.results import save_results_batch_to_db
from src.services.catalog import save_exam_batch_to_rds, load_unified_configs
from src.services.html_parser import is_strictly_regular_or_private, is_relevant_exam

def resolve_crawler_roll_generator(course_name: str, source: str, year_str: str):
    """
    Empirically verified roll number generator covering all 3,486 university exams.
    - Semester Track (2,349 exams): 10-digit [YY][CCC][CODE_2D][SERIAL_3D]
    - Annual Track (1,137 exams): 8-digit [LAST_DIGIT][CCC][SERIAL_4D] (>=2024) or 11/12-digit (<2024)
    """
    c_lower = course_name.lower()
    
    # 1. Determine if this exam is Semester track or Annual track
    is_sem = 'sem' in c_lower or 'semester' in c_lower or source == 'nep'
    
    try:
        yy_full = int(year_str) if year_str else 2024
    except Exception:
        yy_full = 2024
    yy_prefix = f"{str(yy_full)[-2:]}"
    last_digit = str(yy_full)[-1]
    
    if is_sem:
        # Semester Track: 10-digit format: [YY][CCC][CODE_2D][SERIAL_3D]
        detected_code = None
        for code, mapping in COURSE_MAP.items():
            if len(code) == 2:
                if any(kw in c_lower for kw in mapping["keywords"]) and not any(ex in c_lower for ex in mapping.get("exclusions", [])):
                    detected_code = code
                    break
                    
        if not detected_code:
            # Fallback heuristics for all faculties
            if "b.sc" in c_lower or "science" in c_lower:
                detected_code = "30"
            elif "b.com" in c_lower or "commerce" in c_lower:
                detected_code = "20"
            elif "bca" in c_lower or "computer application" in c_lower:
                detected_code = "40"
            elif "bba" in c_lower or "business administration" in c_lower:
                detected_code = "45"
            elif "ll.b" in c_lower or "laws" in c_lower:
                detected_code = "48"
            elif "b.ed" in c_lower:
                detected_code = "46"
            elif "b.p.ed" in c_lower:
                detected_code = "47"
            elif "yoga" in c_lower or "pgdyep" in c_lower:
                detected_code = "73"
            elif "pgdca" in c_lower:
                detected_code = "71"
            elif "m.com" in c_lower:
                detected_code = "76"
            elif "chemistry" in c_lower:
                detected_code = "77"
            elif "hindi" in c_lower:
                detected_code = "75"
            elif "sociology" in c_lower:
                detected_code = "56"
            elif "economics" in c_lower:
                detected_code = "57"
            elif "political" in c_lower:
                detected_code = "59"
            elif "english" in c_lower:
                detected_code = "55"
            elif "history" in c_lower:
                detected_code = "60"
            elif "psychology" in c_lower:
                detected_code = "61"
            elif "botany" in c_lower:
                detected_code = "62"
            elif "zoology" in c_lower:
                detected_code = "63"
            elif "mathematics" in c_lower:
                detected_code = "64"
            elif "physics" in c_lower:
                detected_code = "67"
            elif "biotechnology" in c_lower or "bio tech" in c_lower:
                detected_code = "68"
            elif "microbiology" in c_lower:
                detected_code = "82"
            elif "social work" in c_lower or "msw" in c_lower:
                detected_code = "74"
            elif "m.ed" in c_lower:
                detected_code = "79"
            elif "library" in c_lower or "m.lib" in c_lower or "b.lib" in c_lower:
                detected_code = "69"
            elif "ll.m" in c_lower:
                detected_code = "70"
            else:
                detected_code = "10" # Default B.A.

        # For NEP admission cohort offset if 3rd/5th semester
        offset = 0
        if source == "nep":
            if "3rd sem" in c_lower or "4th sem" in c_lower:
                offset += 1
            elif "5th sem" in c_lower or "6th sem" in c_lower:
                offset += 2
            if "supply" in c_lower or "atkt" in c_lower:
                offset += 1
            try:
                yy_int = int(year_str[-2:]) - offset
            except Exception:
                yy_int = 24
            calc_yy = f"{yy_int:02d}"
        else:
            calc_yy = yy_prefix
            
        max_serial = 800
        consecutive_fails_limit = 15
        return (lambda c, i: f"{calc_yy}{c}{detected_code}{i:03d}"), max_serial, consecutive_fails_limit
        
    else:
        # Annual Track: Part I, Part II, Part III, Previous, Final
        if yy_full >= 2024:
            # 8-digit format: [LAST_DIGIT][CCC][SERIAL_4D]
            max_serial = 2500
            consecutive_fails_limit = 15
            return (lambda c, i: f"{last_digit}{c}{i:04d}"), max_serial, consecutive_fails_limit
        else:
            # Pre-2024 legacy annual format (11 or 12 digits)
            detected_code = None
            for code, mapping in COURSE_MAP.items():
                if len(code) == 3:
                    if any(kw in c_lower for kw in mapping["keywords"]) and not any(ex in c_lower for ex in mapping.get("exclusions", [])):
                        if mapping.get("required"):
                            if any(req in c_lower for req in mapping["required"]):
                                detected_code = code
                                break
                        else:
                            detected_code = code
                            break
            if not detected_code:
                if "bca" in c_lower:
                    detected_code = "013"
                elif "b.com" in c_lower or "commerce" in c_lower:
                    if any(k in c_lower for k in ["part - iii", "part-iii", "part 3", "final year", "3rd year"]):
                        detected_code = "006"
                    elif any(k in c_lower for k in ["part - ii", "part-ii", "part 2", "second year", "2nd year"]):
                        detected_code = "005"
                    else:
                        detected_code = "004"
                elif "b.sc" in c_lower or "science" in c_lower:
                    if any(k in c_lower for k in ["part - iii", "part-iii", "part 3", "final year", "3rd year"]):
                        detected_code = "010"
                    elif any(k in c_lower for k in ["part - ii", "part-ii", "part 2", "second year", "2nd year"]):
                        detected_code = "009"
                    else:
                        detected_code = "008"
                elif "b.a" in c_lower or "arts" in c_lower:
                    if any(k in c_lower for k in ["part - iii", "part-iii", "part 3", "final year", "3rd year"]):
                        detected_code = "003"
                    elif any(k in c_lower for k in ["part - ii", "part-ii", "part 2", "second year", "2nd year"]):
                        detected_code = "002"
                    else:
                        detected_code = "001"
                else:
                    detected_code = "001"
                    
            if yy_full in [2022, 2023]:
                return (lambda c, i: f"{yy_prefix}{c}{detected_code}{i:04d}"), 2500, 15
            else:
                return (lambda c, i: f"{last_digit}{c}{detected_code}{i:04d}"), 2500, 15

async def run_auto_crawler():
    global CRAWL_STATUS
    t_start = time.time()
    
    try:
        # 1. Fetch latest batches from web indices (downloads full HTML to disk & syncs to catalog)
        CRAWL_STATUS["current_exam"] = "Downloading index HTML files & checking for new batches..."
        latest_batches = await fetch_latest_batches()
        for b in latest_batches:
            if is_strictly_regular_or_private(b["description"], b["source"]) and is_relevant_exam(b["description"], b["source"]):
                save_exam_batch_to_rds(b)
        load_unified_configs(force_reload=True)
        
        # 2. Query Turso exam_catalog for pending uncrawled batches
        # Prioritize Regular/Private batches first, followed by newest years
        new_batches = []
        with get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, description, link, publication_date, year, source
                    FROM exam_catalog
                    WHERE (is_crawled IS NOT 1 OR is_crawled IS NULL)
                    ORDER BY 
                        CASE 
                            WHEN description LIKE '%REGULAR%' THEN 0 
                            WHEN description LIKE '%SUPPLY%' OR description LIKE '%ATKT%' THEN 1 
                            ELSE 2 
                        END ASC,
                        CASE WHEN year IS NULL THEN 1 ELSE 0 END, 
                        year DESC, 
                        id DESC
                """)
                for row in cur.fetchall():
                    desc = row[1]
                    source = row[5]
                    if is_strictly_regular_or_private(desc, source) and is_relevant_exam(desc, source):
                        new_batches.append({
                            "id": row[0],
                            "description": row[1],
                            "link": row[2],
                            "publication_date": row[3],
                            "year": str(row[4]) if row[4] else "",
                            "source": row[5]
                        })
        
        if not new_batches:
            print("No uncrawled exams found in catalog.")
            CRAWL_STATUS["status"] = "completed"
            CRAWL_STATUS["current_exam"] = "Finished: All relevant exams are fully crawled."
            return
            
        print(f"Found {len(new_batches)} pending exams to crawl.")
        
        colleges = COLLEGES
        total_records_added = 0
        semaphore = asyncio.Semaphore(12)
        
        for idx, batch in enumerate(new_batches):
            saved_in_batch = 0
            CRAWL_STATUS["current_exam"] = f"[{idx+1}/{len(new_batches)}] Scrape: {batch['description']}"
            CRAWL_STATUS["colleges_progress"] = f"0/{len(colleges)}"
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
            }
            
            parsed_url = urllib.parse.urlparse(batch["link"])
            domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
            
            async with httpx.AsyncClient(follow_redirects=True, limits=httpx.Limits(max_keepalive_connections=15, max_connections=20), timeout=15.0) as client:
                try:
                    r = await client.get(batch["link"], headers=headers)
                    if r.status_code != 200:
                        continue
                    soup = BeautifulSoup(r.text, 'html.parser')
                    token_elem = soup.find('input', {'name': '_token'})
                    if not token_elem:
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
                except Exception as e:
                    print(f"Failed session initialization for {batch['description']}: {e}")
                    continue
                
                source = batch["source"]
                course_name = batch["description"]
                year_str = batch["year"]
                
                roll_generator, max_serial, consecutive_fails_limit = resolve_crawler_roll_generator(course_name, source, year_str)
                    
                colleges_done = 0
                for college_idx, coll in enumerate(colleges):
                    consecutive_fails = 0
                    found_any = False
                    saved_in_college = 0
                    
                    chunk_size = 10
                    for i_start in range(1, max_serial + 1, chunk_size):
                        tasks = []
                        for i in range(i_start, min(i_start + chunk_size, max_serial + 1)):
                            roll = roll_generator(coll, i)
                            tasks.append(roll)
                            
                        # Use high-performance single-token scraping method
                        ajax_tasks = [
                            scrape_exam_with_payload_async(
                                client, domain, batch["link"], payload, token, roll, semaphore, course_name
                            )
                            for roll in tasks
                        ]
                        results = await asyncio.gather(*ajax_tasks)
                        
                        batch_items = []
                        chunk_done = False
                        for res_idx, res in enumerate(results):
                            if res:
                                found_any = True
                                consecutive_fails = 0
                                saved_in_college += 1
                                saved_in_batch += 1
                                roll_val = tasks[res_idx]
                                batch_items.append({"rollno": roll_val, "result": res})
                            else:
                                consecutive_fails += 1
                                limit = consecutive_fails_limit if found_any else 10
                                if consecutive_fails >= limit:
                                    chunk_done = True
                                    break
                                    
                        if batch_items:
                            inserted = save_results_batch_to_db(batch_items)
                            total_records_added += inserted
                            CRAWL_STATUS["records_added"] = total_records_added
                            
                        if chunk_done:
                            break
                            
                    colleges_done += 1
                    CRAWL_STATUS["colleges_progress"] = f"{colleges_done}/{len(colleges)}"
                    CRAWL_STATUS["elapsed_seconds"] = int(time.time() - t_start)
                    
            # Mark this batch as crawled in Turso exam_catalog
            batch_id = batch.get("id")
            if batch_id:
                try:
                    with get_db_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute("""
                                UPDATE exam_catalog 
                                SET is_crawled = 1, crawled_records = ?, last_crawled_at = datetime('now') 
                                WHERE id = ?
                            """, (saved_in_batch, batch_id))
                except Exception as e:
                    print(f"Error updating crawl status for batch {batch_id}: {e}")
                    
        CRAWL_STATUS["status"] = "completed"
        CRAWL_STATUS["current_exam"] = f"Finished. Crawled {len(new_batches)} exams."
        CRAWL_STATUS["elapsed_seconds"] = int(time.time() - t_start)
        
    except Exception as e:
        print(f"Error running auto-crawler: {e}")
        CRAWL_STATUS["status"] = "idle"
        CRAWL_STATUS["current_exam"] = f"Failed: {e}"
