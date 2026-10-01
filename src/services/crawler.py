import asyncio
import os
import json
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

VERIFIED_BATCH_LOOKUP = None

def get_verified_batch_info(link: str) -> dict:
    """Loads and caches the empirical 1,442 batch verification results."""
    global VERIFIED_BATCH_LOOKUP
    if VERIFIED_BATCH_LOOKUP is None:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "analysis", "checker_results.json")
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    VERIFIED_BATCH_LOOKUP = json.load(f)
            except Exception as e:
                print(f"Warning: Could not load verified batch cache: {e}")
                VERIFIED_BATCH_LOOKUP = {}
        else:
            VERIFIED_BATCH_LOOKUP = {}
    return VERIFIED_BATCH_LOOKUP.get(link)

def resolve_crawler_roll_generator(course_name: str, source: str, year_str: str, link: str = ""):
    """
    Empirically verified roll number generator covering all university examination batches (2019–2026).
    1. If a verified hit roll exists in checker_results.json, it directly reconstructs the exact passing pattern.
    2. Otherwise, applies the comprehensive multi-year rule engine across Semester and Annual tracks.
    """
    # 1. Check verified empirical hit database first
    if link:
        verified = get_verified_batch_info(link)
        if verified and verified.get("status") == "PASS":
            scheme = verified.get("pattern_scheme")
            hit_roll = str(verified.get("hit_roll"))
            
            candidate_starts = [1]
            if len(hit_roll) >= 3:
                try:
                    val = int(hit_roll[-3:])
                    if val > 10 and val not in candidate_starts:
                        candidate_starts.append(val)
                except Exception:
                    pass
            if len(hit_roll) >= 4:
                try:
                    val4 = int(hit_roll[-4:])
                    if val4 > 50 and val4 not in candidate_starts:
                        candidate_starts.append(val4)
                except Exception:
                    pass

            if scheme == "NEP_10D":
                yy, cd = hit_roll[:2], hit_roll[5:7]
                return (lambda c, i, y=yy, code=cd: f"{y}{c}{code}{i:03d}"), candidate_starts, 800, 15
            elif scheme == "ANNUAL_8D":
                ld = hit_roll[0]
                return (lambda c, i, y=ld: f"{y}{c}{i:04d}"), candidate_starts, 8000, 15
            elif scheme == "LEGACY_SEM_11D":
                yy, cd = hit_roll[:2], hit_roll[5:8]
                return (lambda c, i, y=yy, code=cd: f"{y}{c}{code}{i:03d}"), candidate_starts, 800, 15
            elif scheme == "LEGACY_SEM_11D_REV":
                yy, cd = hit_roll[3:5], hit_roll[5:8]
                return (lambda c, i, y=yy, code=cd: f"{c}{y}{code}{i:03d}"), candidate_starts, 800, 15
            elif scheme == "LEGACY_ANNUAL_11D":
                ld, cd = hit_roll[0], hit_roll[4:7]
                return (lambda c, i, y=ld, code=cd: f"{y}{c}{code}{i:04d}"), candidate_starts, 2500, 15
            elif scheme == "LEGACY_12D":
                yy, cd = hit_roll[:2], hit_roll[5:8]
                return (lambda c, i, y=yy, code=cd: f"{y}{c}{code}{i:04d}"), candidate_starts, 2500, 15
            elif scheme == "PRSU_10D":
                cd = hit_roll[5:7]
                return (lambda c, i, code=cd: f"17{c}{code}{i:03d}"), candidate_starts, 800, 15
            elif scheme == "HISTORICAL_SEEDED":
                if len(hit_roll) == 11 and hit_roll.startswith("8"):
                    cd = hit_roll[4:7]
                    return (lambda c, i, code=cd: f"8{c}{code}{i:04d}"), candidate_starts, 2500, 15
                elif len(hit_roll) == 11 and hit_roll.startswith("9"):
                    cd = hit_roll[4:7]
                    return (lambda c, i, code=cd: f"9{c}{code}{i:04d}"), candidate_starts, 2500, 15
                elif len(hit_roll) == 10 and (hit_roll.startswith("17") or hit_roll.startswith("74")):
                    prefix = hit_roll[:7]
                    return (lambda c, i, p=prefix: f"{p}{i:03d}"), candidate_starts, 800, 15

    # 2. General multi-year heuristic fallback
    c_lower = course_name.lower()
    is_sem = 'sem' in c_lower or 'semester' in c_lower or source == 'nep'
    
    try:
        yy_full = int(year_str) if year_str else 2024
    except Exception:
        yy_full = 2024
    yy_prefix = f"{str(yy_full)[-2:]}"
    last_digit = str(yy_full)[-1]
    
    if is_sem:
        # Multi-semester admission cohort offset
        offset = 0
        if any(s in c_lower for s in ["3rd sem", "4th sem", "third sem", "fourth sem", "sem - 3", "sem - 4"]):
            offset = 1
        elif any(s in c_lower for s in ["5th sem", "6th sem", "fifth sem", "sixth sem", "sem - 5", "sem - 6"]):
            offset = 2
        elif any(s in c_lower for s in ["7th sem", "8th sem", "seventh sem", "eighth sem", "sem - 7", "sem - 8"]):
            offset = 3
            
        if source == "nep" and any(s in c_lower for s in ["supply", "atkt"]):
            offset += 1

        calc_yy = f"{(yy_full - offset) % 100:02d}"

        if yy_full >= 2023:
            # Modern 10-digit Semester / NEP: [YY][CCC][CODE_2D][SERIAL_3D]
            detected_code = "10" # Default BA
            if "b.sc" in c_lower or "science" in c_lower: detected_code = "30"
            elif "b.com" in c_lower or "commerce" in c_lower: detected_code = "20"
            elif "bca" in c_lower or "computer application" in c_lower: detected_code = "40"
            elif "bba" in c_lower: detected_code = "45"
            elif "b.ed" in c_lower: detected_code = "46"
            elif "b.p.ed" in c_lower: detected_code = "47"
            elif "ll.b" in c_lower or "laws" in c_lower: detected_code = "48"
            elif "english" in c_lower: detected_code = "55"
            elif "sociology" in c_lower: detected_code = "56"
            elif "economics" in c_lower: detected_code = "57"
            elif "geography" in c_lower: detected_code = "58"
            elif "political" in c_lower: detected_code = "59"
            elif "history" in c_lower: detected_code = "60"
            elif "psychology" in c_lower: detected_code = "61"
            elif "botany" in c_lower: detected_code = "62"
            elif "zoology" in c_lower: detected_code = "63"
            elif "mathematics" in c_lower: detected_code = "64"
            elif "physics" in c_lower: detected_code = "67"
            elif "biotechnology" in c_lower or "bio tech" in c_lower: detected_code = "68"
            elif "library" in c_lower or "m.lib" in c_lower: detected_code = "69"
            elif "ll.m" in c_lower: detected_code = "70"
            elif "pgdca" in c_lower: detected_code = "71"
            elif "yoga" in c_lower or "pgdyep" in c_lower: detected_code = "73"
            elif "social work" in c_lower or "msw" in c_lower: detected_code = "74"
            elif "hindi" in c_lower or "sanskrit" in c_lower: detected_code = "75"
            elif "m.com" in c_lower: detected_code = "76"
            elif "chemistry" in c_lower: detected_code = "77"
            elif "m.ed" in c_lower: detected_code = "79"
            elif "home science" in c_lower: detected_code = "80"
            elif "computer science" in c_lower or "m sc it" in c_lower: detected_code = "81"
            elif "microbiology" in c_lower: detected_code = "82"
            elif "textile" in c_lower: detected_code = "83"
            elif "food" in c_lower or "nutrition" in c_lower: detected_code = "85"

            return (lambda c, i, y=calc_yy, cd=detected_code: f"{y}{c}{cd}{i:03d}"), [1], 800, 15

        elif yy_full == 2019:
            # 2019 Foundation Semester Cohort: [CCC]18[Course_3D][SSS]
            legacy_code = "001"
            if "sociology" in c_lower: legacy_code = "045"
            elif "economics" in c_lower: legacy_code = "049"
            elif "political" in c_lower: legacy_code = "057"
            elif "english" in c_lower: legacy_code = "041"
            elif "hindi" in c_lower: legacy_code = "037"
            elif "history" in c_lower: legacy_code = "065"
            elif "geography" in c_lower: legacy_code = "053"
            elif "psychology" in c_lower: legacy_code = "069"
            elif "chemistry" in c_lower: legacy_code = "093"
            elif "botany" in c_lower: legacy_code = "073"
            elif "zoology" in c_lower: legacy_code = "077"
            elif "mathematics" in c_lower: legacy_code = "081"
            elif "physics" in c_lower: legacy_code = "085"
            elif "biotechnology" in c_lower: legacy_code = "089"
            elif "microbiology" in c_lower: legacy_code = "101"
            elif "computer science" in c_lower: legacy_code = "097"
            elif "m.com" in c_lower: legacy_code = "117"
            elif "b.ed" in c_lower: legacy_code = "029"
            elif "b.p.ed" in c_lower: legacy_code = "033"
            elif "ll.b" in c_lower: legacy_code = "023"
            elif "m.ed" in c_lower: legacy_code = "129"
            elif "m.lib" in c_lower: legacy_code = "121"
            elif "pgdca" in c_lower: legacy_code = "135"
            elif "dca" in c_lower: legacy_code = "133"
            elif "bba" in c_lower: legacy_code = "017"

            return (lambda c, i, cd=legacy_code: f"{c}18{cd}{i:03d}"), [1], 800, 15

        else:
            # 2020-2022 Legacy Semesters: [YY][CCC][Course_3D][SSS]
            legacy_code = "001"
            if "sociology" in c_lower: legacy_code = "045"
            elif "economics" in c_lower: legacy_code = "049"
            elif "political" in c_lower: legacy_code = "057"
            elif "english" in c_lower: legacy_code = "041"
            elif "hindi" in c_lower: legacy_code = "037"
            elif "history" in c_lower: legacy_code = "065"
            elif "geography" in c_lower: legacy_code = "053"
            elif "chemistry" in c_lower: legacy_code = "093"
            elif "botany" in c_lower: legacy_code = "073"
            elif "zoology" in c_lower: legacy_code = "077"
            elif "mathematics" in c_lower: legacy_code = "081"
            elif "physics" in c_lower: legacy_code = "085"
            elif "biotechnology" in c_lower: legacy_code = "089"
            elif "microbiology" in c_lower: legacy_code = "101"
            elif "computer science" in c_lower: legacy_code = "097"
            elif "m.com" in c_lower: legacy_code = "117"
            elif "b.ed" in c_lower: legacy_code = "029"
            elif "b.p.ed" in c_lower: legacy_code = "033"
            elif "ll.b" in c_lower: legacy_code = "023"
            elif "m.ed" in c_lower: legacy_code = "129"
            elif "m.lib" in c_lower: legacy_code = "121"
            elif "social work" in c_lower: legacy_code = "125"
            elif "pgdca" in c_lower: legacy_code = "135"
            elif "dca" in c_lower: legacy_code = "133"
            elif "bba" in c_lower: legacy_code = "017"

            return (lambda c, i, y=calc_yy, cd=legacy_code: f"{y}{c}{cd}{i:03d}"), [1], 800, 15
        
    else:
        # Annual Track: Part I, Part II, Part III, Previous, Final
        is_ug = any(k in c_lower for k in ["b.a", "b.sc", "b.com", "bca", "bba", "bachelor", "part - i", "part - ii", "part - iii", "1st year", "2nd year", "3rd year"])
        is_pg_annual = (any(k in c_lower for k in ["m.a", "m.com", "m.sc", "master"]) or any(k in c_lower for k in ["previous", "final"])) and not is_ug
        
        if yy_full >= 2024:
            # Modern 8-digit format: [LAST_DIGIT][CCC][SERIAL_4D]
            if is_pg_annual:
                candidate_starts = [1000, 1200, 1500, 1800, 2000, 2200, 2500, 2800, 3000, 3100, 3200, 3450, 3500, 4000, 4500, 5000, 5500, 5720, 6000, 6500, 7000]
            elif any(k in c_lower for k in ["b.com", "commerce"]):
                candidate_starts = [1, 500, 1000, 1500, 2000, 2500, 3000, 3400, 4000]
            elif any(k in c_lower for k in ["b.sc", "science"]):
                candidate_starts = [1, 500, 1000, 1500, 2000, 2270, 2500, 3000]
            else:
                candidate_starts = [1, 10, 50, 100, 200, 500, 1000, 1500, 2000, 2500, 3000]
                
            return (lambda c, i, ld=last_digit: f"{ld}{c}{i:04d}"), candidate_starts, 8000, 15
        else:
            # Pre-2024 legacy annual format: 11 digits [LAST_DIGIT][CCC][CODE_3D][SERIAL_4D]
            detected_code = "001"
            if "bca" in c_lower:
                detected_code = "013"
            elif "b.com" in c_lower or "commerce" in c_lower:
                if any(k in c_lower for k in ["part - iii", "final year", "3rd year"]): detected_code = "006"
                elif any(k in c_lower for k in ["part - ii", "second year", "2nd year"]): detected_code = "005"
                else: detected_code = "004"
            elif "b.sc" in c_lower or "science" in c_lower:
                if any(k in c_lower for k in ["part - iii", "final year", "3rd year"]): detected_code = "009"
                elif any(k in c_lower for k in ["part - ii", "second year", "2nd year"]): detected_code = "008"
                else: detected_code = "007"
            elif "b.a" in c_lower or "arts" in c_lower:
                if any(k in c_lower for k in ["part - iii", "final year", "3rd year"]): detected_code = "003"
                elif any(k in c_lower for k in ["part - ii", "second year", "2nd year"]): detected_code = "002"
                else: detected_code = "001"
            elif is_pg_annual:
                if "hindi" in c_lower: detected_code = "037" if "previous" in c_lower else "039"
                elif "english" in c_lower: detected_code = "041" if "previous" in c_lower else "043"
                elif "sociology" in c_lower: detected_code = "045" if "previous" in c_lower else "047"
                elif "economics" in c_lower: detected_code = "049" if "previous" in c_lower else "051"
                elif "political" in c_lower: detected_code = "057" if "previous" in c_lower else "059"
                elif "history" in c_lower: detected_code = "065" if "previous" in c_lower else "067"
                elif "geography" in c_lower: detected_code = "053" if "previous" in c_lower else "055"
                elif "mathematics" in c_lower: detected_code = "081" if "previous" in c_lower else "083"
                elif "m.com" in c_lower: detected_code = "117" if "previous" in c_lower else "119"
                elif "sanskrit" in c_lower: detected_code = "150" if "previous" in c_lower else "151"
                elif "philosophy" in c_lower: detected_code = "158" if "previous" in c_lower else "159"
                elif "public" in c_lower: detected_code = "152" if "previous" in c_lower else "153"
                elif "psychology" in c_lower: detected_code = "069" if "previous" in c_lower else "071"

            candidate_starts = [1]
            if is_pg_annual:
                candidate_starts = [1, 1000, 1200, 1500, 1800, 2000, 2200, 2500, 2800, 3000, 3500, 4000, 5000, 6000]

            return (lambda c, i, ld=last_digit, cd=detected_code: f"{ld}{c}{cd}{i:04d}"), candidate_starts, 2500, 15

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
            
            link = batch.get("link", "")
            verified = get_verified_batch_info(link)

            # Skip verified empty CMS shells (0 enrolled students / draft placeholders)
            if verified and verified.get("status") == "FAIL":
                print(f"Skipping verified empty CMS shell batch [{batch.get('id')}]: {batch.get('description')}")
                batch_id = batch.get("id")
                if batch_id:
                    try:
                        with get_db_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    UPDATE exam_catalog 
                                    SET is_crawled = 1, crawled_records = 0, last_crawled_at = datetime('now') 
                                    WHERE id = ?
                                """, (batch_id,))
                    except Exception as e:
                        print(f"Error marking shell batch {batch_id} as crawled: {e}")
                continue

            # Prioritize verified hit college if known
            colleges_to_crawl = list(colleges)
            if verified and verified.get("status") == "PASS":
                hit_col = verified.get("hit_college")
                if hit_col == "SEEDED":
                    hit_roll = str(verified.get("hit_roll", ""))
                    if len(hit_roll) == 11 and hit_roll[0] in ["8", "9"]:
                        hit_col = hit_roll[1:4]
                    elif hit_roll.startswith("17"):
                        hit_col = hit_roll[2:5]
                    elif hit_roll.startswith("74"):
                        hit_col = "221"
                if hit_col and hit_col in colleges_to_crawl:
                    colleges_to_crawl.remove(hit_col)
                    colleges_to_crawl.insert(0, hit_col)

            CRAWL_STATUS["colleges_progress"] = f"0/{len(colleges_to_crawl)}"
            
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
                
                roll_generator, candidate_starts, max_serial, consecutive_fails_limit = resolve_crawler_roll_generator(
                    course_name, source, year_str, link=batch["link"]
                )
                    
                colleges_done = 0
                for college_idx, coll in enumerate(colleges_to_crawl):
                    consecutive_fails = 0
                    found_any = False
                    saved_in_college = 0
                    
                    # Determine active starting serial for this college
                    active_start = candidate_starts[0]
                    if len(candidate_starts) > 1:
                        for probe_start in candidate_starts:
                            probe_rolls = [roll_generator(coll, i) for i in range(probe_start, probe_start + 10)]
                            probe_tasks = [
                                scrape_exam_with_payload_async(
                                    client, domain, batch["link"], payload, token, r_num, semaphore, course_name
                                )
                                for r_num in probe_rolls
                            ]
                            probe_res = await asyncio.gather(*probe_tasks)
                            if any(probe_res):
                                active_start = probe_start
                                found_any = True
                                break
                                
                        if not found_any:
                            colleges_done += 1
                            continue
                            
                    # If cohort was discovered at an offset > 1, sweep backward up to 30 numbers to catch edge students
                    if active_start > 1:
                        back_rolls = [roll_generator(coll, i) for i in range(max(1, active_start - 30), active_start)]
                        back_tasks = [
                            scrape_exam_with_payload_async(
                                client, domain, batch["link"], payload, token, r_num, semaphore, course_name
                            )
                            for r_num in back_rolls
                        ]
                        back_res = await asyncio.gather(*back_tasks)
                        back_items = []
                        for r_num, res in zip(back_rolls, back_res):
                            if res:
                                found_any = True
                                saved_in_college += 1
                                saved_in_batch += 1
                                back_items.append({"rollno": r_num, "result": res})
                        if back_items:
                            inserted = save_results_batch_to_db(back_items)
                            total_records_added += inserted
                            CRAWL_STATUS["records_added"] = total_records_added

                    # Stream forward from active_start chunk by chunk until consecutive_fails_limit
                    chunk_size = 10
                    consecutive_fails = 0
                    for i_start in range(active_start, max_serial + 1, chunk_size):
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
                    CRAWL_STATUS["colleges_progress"] = f"{colleges_done}/{len(colleges_to_crawl)}"
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
