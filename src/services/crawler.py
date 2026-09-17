import asyncio
import time
import httpx
from bs4 import BeautifulSoup

from src.core.config import CRAWL_STATUS, COLLEGES, UNIFIED_CONFIGS, COURSE_MAP
from src.services.scraper import fetch_latest_batches, scrape_exam_async
from src.services.results import save_result_to_db
from src.services.catalog import save_exam_batch_to_rds, load_unified_configs
from src.services.html_parser import is_strictly_regular_or_private, is_relevant_exam

def resolve_crawler_roll_generator(course_name: str, source: str, year_str: str):
    c_lower = course_name.lower()
    
    if source == "nep":
        detected_code = None
        for code, mapping in COURSE_MAP.items():
            if len(code) == 2:
                if any(kw in c_lower for kw in mapping["keywords"]) and not any(ex in c_lower for ex in mapping.get("exclusions", [])):
                    detected_code = code
                    break
                    
        if not detected_code:
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
            else:
                detected_code = "10"
                
        offset = 0
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
        yy_prefix = f"{yy_int:02d}"
        
        return (lambda c, i: f"{yy_prefix}{c}{detected_code}{i:03d}"), 800, 15
        
    else:
        try:
            yy_year = int(year_str)
        except Exception:
            yy_year = 2024
            
        if yy_year >= 2024:
            last_digit = str(yy_year)[-1]
            return (lambda c, i: f"{last_digit}{c}{i:04d}"), 2500, 15
        else:
            detected_code = None
            for code, mapping in COURSE_MAP.items():
                if len(code) == 3:
                    if any(kw in c_lower for kw in mapping["keywords"]) and not any(ex in c_lower for ex in mapping.get("exclusions", [])):
                        detected_code = code
                        break
            if not detected_code:
                if "bca" in c_lower:
                    detected_code = "013"
                elif "b.com" in c_lower:
                    detected_code = "004"
                elif "b.sc" in c_lower:
                    detected_code = "008"
                else:
                    detected_code = "001"
                    
            if yy_year in [2022, 2023]:
                yy_prefix = str(yy_year)[-2:]
                return (lambda c, i: f"{yy_prefix}{c}{detected_code}{i:04d}"), 2500, 15
            else:
                last_digit = str(yy_year)[-1]
                return (lambda c, i: f"{last_digit}{c}{detected_code}{i:04d}"), 2500, 15

async def run_auto_crawler():
    global CRAWL_STATUS
    t_start = time.time()
    
    try:
        # 1. Fetch latest batches from web indices (downloads full HTML to disk & filters)
        CRAWL_STATUS["current_exam"] = "Downloading index HTML files & cleaning..."
        latest_batches = await fetch_latest_batches()
        
        # 2. Check existing exams by (description, publication_date, source)
        existing_keys = {(c["description"].strip().lower(), c.get("publication_date", "").strip(), c.get("source", "")) for c in UNIFIED_CONFIGS}
        
        # 3. Identify new batches locked strictly to REGULAR / PRIVATE and relevant courses
        new_batches = []
        for b in latest_batches:
            if not is_strictly_regular_or_private(b["description"], b["source"]):
                continue
            if not is_relevant_exam(b["description"], b["source"]):
                continue
            key = (b["description"].strip().lower(), b.get("publication_date", "").strip(), b.get("source", ""))
            if key not in existing_keys:
                new_batches.append(b)
        
        if not new_batches:
            print("No new exams found to crawl.")
            CRAWL_STATUS["status"] = "completed"
            CRAWL_STATUS["current_exam"] = "Finished: No new exams found."
            return
            
        print(f"Found {len(new_batches)} new exams to crawl.")
        
        colleges = COLLEGES
        total_records_added = 0
        semaphore = asyncio.Semaphore(12)
        
        for idx, batch in enumerate(new_batches):
            CRAWL_STATUS["current_exam"] = f"[{idx+1}/{len(new_batches)}] Scrape: {batch['description']}"
            CRAWL_STATUS["colleges_progress"] = f"0/{len(colleges)}"
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
            }
            
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
                            
                        ajax_tasks = [
                            scrape_exam_async(client, course_name, batch["link"], roll, semaphore)
                            for roll in tasks
                        ]
                        results = await asyncio.gather(*ajax_tasks)
                        
                        chunk_done = False
                        for res_idx, res in enumerate(results):
                            if res:
                                found_any = True
                                consecutive_fails = 0
                                saved_in_college += 1
                                
                                roll_val = tasks[res_idx]
                                save_result_to_db(roll_val, res)
                                total_records_added += 1
                                CRAWL_STATUS["records_added"] = total_records_added
                            else:
                                consecutive_fails += 1
                                limit = consecutive_fails_limit if found_any else 10
                                if consecutive_fails >= limit:
                                    chunk_done = True
                                    break
                        if chunk_done:
                            break
                            
                    colleges_done += 1
                    CRAWL_STATUS["colleges_progress"] = f"{colleges_done}/{len(colleges)}"
                    CRAWL_STATUS["elapsed_seconds"] = int(time.time() - t_start)
                    
            # Once a batch is fully crawled, persist it to Amazon RDS exam_catalog & reload config cache
            save_exam_batch_to_rds(batch)
            load_unified_configs(force_reload=True)
                    
        CRAWL_STATUS["status"] = "completed"
        CRAWL_STATUS["current_exam"] = f"Finished. Crawled {len(new_batches)} exams."
        CRAWL_STATUS["elapsed_seconds"] = int(time.time() - t_start)
        
    except Exception as e:
        print(f"Error running auto-crawler: {e}")
        CRAWL_STATUS["status"] = "idle"
        CRAWL_STATUS["current_exam"] = f"Failed: {e}"
