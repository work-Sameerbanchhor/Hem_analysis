#!/usr/bin/env python3
"""
HYU Live Results - Local SQLite Pilot Crawler.
Saves marksheet records with Gzip BLOB compression directly to local `hyu_results.db`.
Pure local SQLite, zero Turso/cloud dependencies.
"""

import sys
import os
import re
import time
import gzip
import sqlite3
import argparse
import asyncio
import urllib.parse
from datetime import datetime
import httpx
from bs4 import BeautifulSoup

# Ensure project root is in sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.core.config import COLLEGES, LOCAL_DB_PATH
from src.services.crawler import resolve_crawler_roll_generator
from src.services.scraper import scrape_exam_with_payload_async

def get_local_db():
    conn = sqlite3.connect(LOCAL_DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def save_batch_local(items: list) -> int:
    """Saves a batch of scraped marksheet dicts into local SQLite results table with Gzip compression."""
    if not items:
        return 0
    
    rows = []
    for item in items:
        rollno = str(item["rollno"]).strip()
        res = item["result"]
        student_info = res.get("student_info", {})
        
        enrollment_no = (student_info.get("enrollment_no") or f"UNKNOWN_{rollno}").strip()
        try:
            roll_int = int(re.sub(r"\D", "", rollno))
        except Exception:
            roll_int = 0
            
        student_name = student_info.get("name", "N/A").strip()
        exam_title = res.get("exam_title", "Unknown Exam").strip()
        result_status = res.get("result_status", "N/A").strip()
        sgpa = str(res.get("sgpa", "N/A")).strip()
        official_url = res.get("official_url", "").strip()
        
        # Gzip compress marksheet HTML
        raw_html = res.get("html", "")
        if raw_html:
            html_blob = gzip.compress(raw_html.encode("utf-8"))
        else:
            html_blob = None
            
        rows.append((
            enrollment_no, roll_int, student_name, exam_title,
            result_status, sgpa, html_blob, official_url
        ))
        
    conn = get_local_db()
    try:
        cur = conn.cursor()
        cur.executemany("""
            INSERT INTO results (
                enrollment_no, roll_number, name, exam_title,
                result_status, sgpa, html_result, official_url, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(enrollment_no, roll_number, exam_title) DO UPDATE SET
                name = excluded.name,
                result_status = excluded.result_status,
                sgpa = excluded.sgpa,
                html_result = excluded.html_result,
                official_url = excluded.official_url,
                updated_at = datetime('now')
        """, rows)
        conn.commit()
        return len(rows)
    finally:
        conn.close()

async def run_pilot_crawl(exam_id: int, num_colleges: int = 5, concurrency: int = 10):
    t_start = time.time()
    
    # 1. Fetch exam details from local catalog
    conn = get_local_db()
    cur = conn.cursor()
    cur.execute("SELECT id, description, link, publication_date, year, source FROM exam_catalog WHERE id = ?", (exam_id,))
    exam = cur.fetchone()
    conn.close()
    
    if not exam:
        print(f"Error: Exam ID {exam_id} not found in local catalog.")
        return
        
    eid, description, link, pub_date, year, source = exam
    year_str = str(year) if year else "2024"
    
    print("\n" + "="*70)
    print("  HYU PILOT CRAWLER (LOCAL SQLITE)")
    print("="*70)
    print(f"  Target Exam ID   : {eid}")
    print(f"  Description      : {description}")
    print(f"  Publication Date : {pub_date}")
    print(f"  Exam Year / Track: {year_str} ({source.upper()})")
    print(f"  Database Target  : {LOCAL_DB_PATH}")
    print(f"  Colleges to Crawl: {num_colleges if num_colleges > 0 else len(COLLEGES)} of {len(COLLEGES)}")
    print(f"  Concurrency      : {concurrency} parallel workers")
    print("="*70 + "\n")
    
    # 2. Resolve roll generator, candidate starting points, and limits
    roll_generator, candidate_starts, max_serial, fail_limit = resolve_crawler_roll_generator(description, source, year_str)
    print(f"[*] Roll Generator: candidate_starts={candidate_starts}, max_serial={max_serial}, fail_limit={fail_limit}")
    
    # 3. Initialize HTTP session & extract token + form payload
    parsed_url = urllib.parse.urlparse(link)
    domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
    
    print(f"[*] Initializing session on {domain}...")
    async with httpx.AsyncClient(timeout=20.0, limits=httpx.Limits(max_keepalive_connections=concurrency, max_connections=concurrency)) as client:
        r = await client.get(link, headers=headers)
        if r.status_code != 200:
            print(f"[-] Failed to access exam page (HTTP {r.status_code})")
            return
            
        soup = BeautifulSoup(r.text, "html.parser")
        token_elem = soup.find("input", {"name": "_token"})
        if not token_elem:
            print("[-] CSRF token not found in exam page.")
            return
        token = token_elem["value"]
        
        payload = {
            "COURSECD": soup.find("input", {"name": "COURSECD"})["value"] if soup.find("input", {"name": "COURSECD"}) else "",
            "SEMCODE": soup.find("input", {"name": "SEMCODE"})["value"] if soup.find("input", {"name": "SEMCODE"}) else "",
            "RESULTTYPE": soup.find("input", {"name": "RESULTTYPE"})["value"] if soup.find("input", {"name": "RESULTTYPE"}) else "",
            "session": soup.find("input", {"name": "session"})["value"] if soup.find("input", {"name": "session"}) else "",
            "tcc": soup.find("input", {"name": "tcc"})["value"] if soup.find("input", {"name": "tcc"}) else "",
            "p1": "", "all": ""
        }
        print(f"[+] Token acquired: {token[:12]}... Form payload: COURSECD={payload['COURSECD']}, SEMCODE={payload['SEMCODE']}, tcc={payload['tcc']}")
        
        semaphore = asyncio.Semaphore(concurrency)
        target_colleges = COLLEGES[:num_colleges] if num_colleges > 0 else COLLEGES
        
        total_saved_exam = 0
        all_scraped_samples = []
        
        for c_idx, coll in enumerate(target_colleges, 1):
            t_coll_start = time.time()
            coll_saved = 0
            found_any = False
            active_start = candidate_starts[0]
            
            # Step A: Probe candidate start points if multiple candidates exist
            if len(candidate_starts) > 1:
                for probe_start in candidate_starts:
                    probe_rolls = [roll_generator(coll, i) for i in range(probe_start, probe_start + 10)]
                    probe_tasks = [
                        scrape_exam_with_payload_async(client, domain, link, payload, token, r_num, semaphore, description)
                        for r_num in probe_rolls
                    ]
                    probe_res = await asyncio.gather(*probe_tasks)
                    if any(probe_res):
                        active_start = probe_start
                        found_any = True
                        break
                        
                if not found_any:
                    print(f"[{c_idx}/{len(target_colleges)}] College {coll}: No cohort found across {candidate_starts} ({time.time() - t_coll_start:.1f}s)")
                    continue
            else:
                active_start = candidate_starts[0]
                
            # Step B: Backsweep up to 30 serials if offset > 1
            if active_start > 1:
                back_rolls = [roll_generator(coll, i) for i in range(max(1, active_start - 30), active_start)]
                back_tasks = [
                    scrape_exam_with_payload_async(client, domain, link, payload, token, r_num, semaphore, description)
                    for r_num in back_rolls
                ]
                back_res = await asyncio.gather(*back_tasks)
                back_items = []
                for r_num, res in zip(back_rolls, back_res):
                    if res:
                        found_any = True
                        coll_saved += 1
                        total_saved_exam += 1
                        back_items.append({"rollno": r_num, "result": res})
                        all_scraped_samples.append((r_num, res.get("student_info", {}).get("name", "N/A"), res.get("result_status", "N/A")))
                if back_items:
                    save_batch_local(back_items)
                    
            # Step C: Stream forward in chunks of 10 until consecutive_fails limit
            consecutive_fails = 0
            chunk_size = 10
            for i_start in range(active_start, max_serial + 1, chunk_size):
                tasks = [roll_generator(coll, i) for i in range(i_start, min(i_start + chunk_size, max_serial + 1))]
                ajax_tasks = [
                    scrape_exam_with_payload_async(client, domain, link, payload, token, r_num, semaphore, description)
                    for r_num in tasks
                ]
                results = await asyncio.gather(*ajax_tasks)
                
                batch_items = []
                chunk_done = False
                for r_num, res in zip(tasks, results):
                    if res:
                        found_any = True
                        consecutive_fails = 0
                        coll_saved += 1
                        total_saved_exam += 1
                        batch_items.append({"rollno": r_num, "result": res})
                        all_scraped_samples.append((r_num, res.get("student_info", {}).get("name", "N/A"), res.get("result_status", "N/A")))
                    else:
                        consecutive_fails += 1
                        limit = fail_limit if found_any else 10
                        if consecutive_fails >= limit:
                            chunk_done = True
                            break
                            
                if batch_items:
                    save_batch_local(batch_items)
                    
                if chunk_done:
                    break
                    
            coll_elapsed = time.time() - t_coll_start
            speed = coll_saved / coll_elapsed if coll_elapsed > 0 else 0
            print(f"[{c_idx}/{len(target_colleges)}] College {coll}: {coll_saved:4d} students saved | {coll_elapsed:.1f}s ({speed:.1f} rec/s) | Total: {total_saved_exam}")
            
    # 4. Final summary and catalog update
    total_time = time.time() - t_start
    overall_speed = total_saved_exam / total_time if total_time > 0 else 0
    
    # Update local exam_catalog
    conn = get_local_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE exam_catalog 
        SET is_crawled = 1, crawled_records = ?, last_crawled_at = datetime('now')
        WHERE id = ?
    """, (total_saved_exam, eid))
    conn.commit()
    
    # Total database stats
    cur.execute("SELECT COUNT(*) FROM results")
    total_db_records = cur.fetchone()[0]
    conn.close()
    
    db_size_mb = os.path.getsize(LOCAL_DB_PATH) / (1024 * 1024)
    
    print("\n" + "="*70)
    print("  PILOT CRAWL COMPLETE!")
    print("="*70)
    print(f"  Records Added in Run : {total_saved_exam:,}")
    print(f"  Total DB Records     : {total_db_records:,}")
    print(f"  Local DB File Size   : {db_size_mb:.2f} MB")
    print(f"  Elapsed Time         : {total_time:.1f} seconds ({total_time/60:.2f} minutes)")
    print(f"  Average Throughput   : {overall_speed:.1f} records/second")
    print("="*70)
    
    if all_scraped_samples:
        print("\n--- Sample Scraped Students ---")
        for r_num, name, status in all_scraped_samples[:10]:
            print(f"  Roll: {r_num} | Name: {name:<25} | Status: {status}")
        if len(all_scraped_samples) > 10:
            print(f"  ... and {len(all_scraped_samples) - 10} more records.")
    print("")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HYU Pilot Crawler (Local SQLite)")
    parser.add_argument("--exam_id", type=int, default=1168, help="Exam ID from catalog (default: 1168 - B.A. 1st Year Annual 2024)")
    parser.add_argument("--colleges", type=int, default=5, help="Number of colleges to crawl (default: 5, use -1 or 84 for all)")
    parser.add_argument("--concurrency", type=int, default=12, help="Concurrency level (default: 12)")
    args = parser.parse_args()
    
    asyncio.run(run_pilot_crawl(args.exam_id, args.colleges, args.concurrency))
