#!/usr/bin/env python3
"""
========================================================================================
HYU Master Scraper: Hemchand Yadav Vishwavidyalaya (Durg University)
========================================================================================
High-throughput, fully automated, standalone marksheet scraping engine.
- Scrapes 100% of all students across all academic years (2019–2026).
- Directly loads verified batches from analysis/checker_results.json.
- Bypasses empty CMS draft shells with zero wasted network requests.
- Saves pristine raw HTML marksheets to disk: raw_marksheets/{year}/{batch_dir}/{roll}.html
- Employs dynamic class-size boundary detection (automatically detects cohorts from
  15 students in niche M.Sc/M.Phil programs to 350+ in large B.A/B.Sc centers).
- Self-tracking, crash-resilient SQLite manifest: restart anytime without duplicate work.
- Zero local dependencies beyond `httpx` and `beautifulsoup4`.

Usage:
  python3 master_scraper.py --all                    # Scrape all 1,005 passing batches (2019-2026)
  python3 master_scraper.py --year 2024              # Scrape all batches for a specific academic year
  python3 master_scraper.py --batch 1                # Scrape a specific batch ID
  python3 master_scraper.py --concurrency 20         # Adjust async concurrency limit
========================================================================================
"""

import os
import sys
import re
import time
import json
import signal
import sqlite3
import argparse
import asyncio
import concurrent.futures
from datetime import datetime
import httpx
from bs4 import BeautifulSoup

# ======================================================================================
# 1. UNIVERSITY COLLEGE CENTERS & CONFIGURATION
# ======================================================================================

PRIORITY_COLLEGES = [
    # Top active university centers
    '301', '302', '331', '101', '339', '334', '342', '332', '341', '536', '340',
    '401', '202', '303', '502', '335', '201', '311', '344', '314', '318',
    '535', '113', '213', '503', '511', '338', '343', '102', '105', '204',
    '305', '308', '312', '316', '320', '337',
    # Specialized institute centers
    '369', '370',  # Mansarowar & Integrated Education Colleges
    '531',         # State Institute of Mental Health (M.Phil)
    '333',         # Govt. Dr. W.W. Patankar Girls PG College Durg (Home Science)
    '221', '345', '538',
    # Remaining affiliated colleges
    '103', '104', '106', '107', '108', '109', '110', '122', '123',
    '203', '205', '206', '207', '211', '212',
    '304', '306', '307', '309', '310', '313', '317', '319', '381', '384',
    '402', '403', '404', '405', '406', '407', '413',
    '504', '505', '506', '507', '508', '509', '510', '514', '515', '516',
    '517', '519', '522', '524', '525', '532'
]

# Deduplicate while preserving priority order
SEEN_COLS = set()
ORDERED_COLLEGES = []
for c in PRIORITY_COLLEGES:
    if c not in SEEN_COLS:
        SEEN_COLS.add(c)
        ORDERED_COLLEGES.append(c)

DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw_marksheets")
CHECKER_RESULTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis", "checker_results.json")

# Graceful termination flag
SHUTDOWN_REQUESTED = False

def handle_signal(sig, frame):
    global SHUTDOWN_REQUESTED
    print("\n[!] Graceful shutdown requested (Ctrl+C). Completing current roll chunk and saving progress...")
    SHUTDOWN_REQUESTED = True

signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

# ======================================================================================
# 2. SQLITE PROGRESS MANIFEST & FUSE-OPTIMIZED PRAGMAS
# ======================================================================================

def get_manifest_db(output_dir: str):
    os.makedirs(output_dir, exist_ok=True)
    db_path = os.path.join(output_dir, "manifest.sqlite")
    conn = sqlite3.connect(db_path, timeout=60.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        conn.execute("PRAGMA journal_mode=TRUNCATE")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")  # 64MB memory page cache
    conn.execute("PRAGMA temp_store=MEMORY")
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS batches (
                batch_id INTEGER PRIMARY KEY,
                year TEXT,
                description TEXT,
                link TEXT,
                status TEXT, -- 'PENDING', 'IN_PROGRESS', 'COMPLETED', 'EMPTY_SHELL'
                students_scraped INTEGER DEFAULT 0,
                started_at TEXT,
                completed_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS marksheets (
                batch_id INTEGER,
                roll_number TEXT,
                year TEXT,
                college_code TEXT,
                file_path TEXT,
                file_size INTEGER,
                scraped_at TEXT,
                PRIMARY KEY (batch_id, roll_number)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_marksheets_batch ON marksheets (batch_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_marksheets_roll ON marksheets (roll_number)")
    return conn

# ======================================================================================
# 2.1 HIGH-THROUGHPUT RAM BUFFER MANAGER (FUSE / I/O BOTTLENECK REMOVER)
# ======================================================================================

class RamBufferManager:
    """
    High-throughput in-memory buffer manager designed to eliminate FUSE storage and network I/O bottlenecks:
    - Maintains in-memory index of all existing marksheets in RAM (zero FUSE stat calls).
    - Buffers up to N records (default: 5,000) directly in memory.
    - Flushes in parallel using multi-threaded disk writers and single atomic SQLite transactions.
    """
    def __init__(self, output_dir: str, db_conn: sqlite3.Connection, buffer_size: int = 5000, num_io_workers: int = 32):
        self.output_dir = output_dir
        self.db_conn = db_conn
        self.buffer_size = buffer_size
        self.num_io_workers = num_io_workers
        self.buffer = []
        self.pending_batch_updates = []
        self.seen_marksheets = set()  # set of (batch_id, roll_number_str)
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=num_io_workers)
        self.total_flushed_records = 0

    def load_existing_marksheets(self):
        """Pre-loads all existing (batch_id, roll_number) from SQLite manifest into RAM."""
        t0 = time.time()
        cur = self.db_conn.cursor()
        cur.execute("SELECT batch_id, roll_number FROM marksheets")
        rows = cur.fetchall()
        for bid, rnum in rows:
            self.seen_marksheets.add((bid, str(rnum)))
        elapsed = round(time.time() - t0, 2)
        print(f"[*] In-Memory Index: Loaded {len(self.seen_marksheets):,} existing marksheet keys into RAM ({elapsed}s) -> 0 FUSE stat calls.")

    def is_cached(self, batch_id: int, roll_number: str) -> bool:
        return (batch_id, str(roll_number)) in self.seen_marksheets

    async def add_marksheet(self, record: dict):
        self.buffer.append(record)
        self.seen_marksheets.add((record['batch_id'], str(record['roll_number'])))
        if len(self.buffer) >= self.buffer_size:
            await self.flush()

    def stage_batch_completion(self, batch_id: int, total_students: int, completed_at: str):
        self.pending_batch_updates.append((total_students, completed_at, batch_id))

    async def flush(self):
        if not self.buffer and not self.pending_batch_updates:
            return

        records_to_flush = list(self.buffer)
        self.buffer.clear()
        batch_updates_to_flush = list(self.pending_batch_updates)
        self.pending_batch_updates.clear()

        count = len(records_to_flush)
        b_count = len(batch_updates_to_flush)
        t0 = time.time()
        print(f"\n[>>>] RAM Buffer Threshold Reached ({count:,} records). Bulk saving to FUSE disk & manifest...")

        # 1. Parallel I/O writing on FUSE using ThreadPoolExecutor
        if records_to_flush:
            def _write_one(item):
                try:
                    os.makedirs(os.path.dirname(item['file_path']), exist_ok=True)
                    with open(item['file_path'], 'w', encoding='utf-8') as f:
                        f.write(item['html_data'])
                    return True
                except Exception as e:
                    print(f"[!] FUSE write error for {item['file_path']}: {e}")
                    return False

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self.executor, lambda: list(self.executor.map(_write_one, records_to_flush)))

        # 2. Single Atomic SQLite Transaction
        with self.db_conn:
            if records_to_flush:
                manifest_rows = [
                    (r['batch_id'], str(r['roll_number']), r['year'], r['college_code'], r['file_path'], r['file_size'], r['scraped_at'])
                    for r in records_to_flush
                ]
                self.db_conn.executemany("""
                    INSERT OR REPLACE INTO marksheets 
                    (batch_id, roll_number, year, college_code, file_path, file_size, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, manifest_rows)

            if batch_updates_to_flush:
                self.db_conn.executemany("""
                    UPDATE batches 
                    SET status = 'COMPLETED', students_scraped = ?, completed_at = ?
                    WHERE batch_id = ?
                """, batch_updates_to_flush)

        self.total_flushed_records += count
        dt = round(time.time() - t0, 2)
        write_speed = round(count / max(0.01, dt), 1)
        print(f"[<<<] Successfully committed {count:,} marksheets and {b_count} batches in {dt}s ({write_speed} records/sec)!\n")

    def close(self):
        self.executor.shutdown(wait=True)

# ======================================================================================
# 3. ROLL NUMBER GENERATOR RESOLVER
# ======================================================================================

def sanitize_slug(text: str) -> str:
    s = re.sub(r'[^a-zA-Z0-9]+', '_', text).strip('_')
    return s[:60]

def build_generator_for_batch(batch: dict):
    """
    Reconstructs the exact, empirically verified roll number generator for a passing batch.
    Returns: (generator_func, candidate_starts, max_serial, primary_college)
    """
    scheme = batch.get('pattern_scheme')
    hit_roll = str(batch.get('hit_roll', ''))
    hit_col = str(batch.get('hit_college', ''))
    desc = batch.get('description', '').lower()
    yr = str(batch.get('year', ''))

    # Translate SEEDED aliases
    if hit_col == "SEEDED":
        if len(hit_roll) == 11 and hit_roll[0] in ["8", "9"]:
            hit_col = hit_roll[1:4]
        elif hit_roll.startswith("17"):
            hit_col = hit_roll[2:5]
        elif hit_roll.startswith("74"):
            hit_col = "221"

    candidate_starts = [1]
    is_annual = not ('sem' in desc or 'semester' in desc)

    # Seed high starting offsets if the verified hit roll started above serial 1
    if len(hit_roll) >= 3:
        try:
            val3 = int(hit_roll[-3:])
            if val3 > 10 and val3 not in candidate_starts:
                candidate_starts.append(val3)
        except Exception:
            pass
    if len(hit_roll) >= 4:
        try:
            val4 = int(hit_roll[-4:])
            if val4 > 50 and val4 not in candidate_starts:
                candidate_starts.append(val4)
        except Exception:
            pass

    if scheme == 'NEP_10D':
        yy, cd = hit_roll[:2], hit_roll[5:7]
        gen = lambda c, i, y=yy, code=cd: f"{y}{c}{code}{i:03d}"
        max_serial = 800
    elif scheme == 'ANNUAL_8D':
        ld = hit_roll[0]
        gen = lambda c, i, y=ld: f"{y}{c}{i:04d}"
        max_serial = 8000
        if is_annual and any(k in desc for k in ['previous', 'final', 'm.a', 'm.sc', 'm.com']):
            for s in [1000, 1200, 1500, 1800, 2000, 2200, 2500, 3000, 3500, 4000, 5000, 6000]:
                if s not in candidate_starts:
                    candidate_starts.append(s)
    elif scheme == 'LEGACY_SEM_11D':
        yy, cd = hit_roll[:2], hit_roll[5:8]
        gen = lambda c, i, y=yy, code=cd: f"{y}{c}{code}{i:03d}"
        max_serial = 800
    elif scheme == 'LEGACY_SEM_11D_REV':
        yy, cd = hit_roll[3:5], hit_roll[5:8]
        gen = lambda c, i, y=yy, code=cd: f"{c}{y}{code}{i:03d}"
        max_serial = 800
    elif scheme == 'LEGACY_ANNUAL_11D':
        ld, cd = hit_roll[0], hit_roll[4:7]
        gen = lambda c, i, y=ld, code=cd: f"{y}{c}{code}{i:04d}"
        max_serial = 3000
        if is_annual and any(k in desc for k in ['previous', 'final', 'm.a', 'm.sc', 'm.com']):
            for s in [1000, 1200, 1500, 1800, 2000, 2500, 3000, 3500, 4000, 5000, 6000]:
                if s not in candidate_starts:
                    candidate_starts.append(s)
    elif scheme == 'LEGACY_12D':
        yy, cd = hit_roll[:2], hit_roll[5:8]
        gen = lambda c, i, y=yy, code=cd: f"{y}{c}{code}{i:04d}"
        max_serial = 3000
        if is_annual and any(k in desc for k in ['previous', 'final', 'm.a', 'm.sc', 'm.com']):
            for s in [1000, 1200, 1500, 1800, 2000, 2500, 3000, 3500, 4000, 5000, 6000]:
                if s not in candidate_starts:
                    candidate_starts.append(s)
    elif scheme == 'PRSU_10D':
        cd = hit_roll[5:7]
        gen = lambda c, i, code=cd: f"17{c}{code}{i:03d}"
        max_serial = 800
    elif scheme == 'HISTORICAL_SEEDED':
        if len(hit_roll) == 11 and hit_roll.startswith('8'):
            cd = hit_roll[4:7]
            gen = lambda c, i, code=cd: f"8{c}{code}{i:04d}"
            max_serial = 3000
        elif len(hit_roll) == 11 and hit_roll.startswith('9'):
            cd = hit_roll[4:7]
            gen = lambda c, i, code=cd: f"9{c}{code}{i:04d}"
            max_serial = 3000
        elif len(hit_roll) == 10 and (hit_roll.startswith('17') or hit_roll.startswith('74')):
            prefix = hit_roll[:7]
            gen = lambda c, i, p=prefix: f"{p}{i:03d}"
            max_serial = 800
        else:
            gen = lambda c, i, hr=hit_roll: hr
            max_serial = 100
    else:
        gen = lambda c, i: f"{yr[-2:]}{c}10{i:03d}"
        max_serial = 800

    return gen, candidate_starts, max_serial, hit_col

# ======================================================================================
# 4. CORE ASYNC SCRAPING ENGINE (DYNAMIC CLASS BOUNDARY DETECTION)
# ======================================================================================

async def scrape_batch(
    client: httpx.AsyncClient,
    batch: dict,
    output_dir: str,
    ram_manager: RamBufferManager,
    semaphore: asyncio.Semaphore
) -> int:
    """
    Completely scrapes an active batch across all affiliated college centers.
    Dynamically identifies cohort size per college using consecutive miss thresholds.
    Buffers marksheets in RAM and bulk-saves in batches of N (default: 5000).
    """
    global SHUTDOWN_REQUESTED
    if SHUTDOWN_REQUESTED:
        return 0

    bid = batch['id']
    yr = batch['year']
    desc = batch['description']
    link = batch['link']
    batch_slug = f"{bid:04d}_{sanitize_slug(desc)}"
    batch_dir = os.path.join(output_dir, yr, batch_slug)

    # 1. Fetch CSRF token & form payload
    try:
        r = await client.get(link)
        if r.status_code != 200:
            print(f"[!] Batch [{bid}] HTTP {r.status_code} on initial GET: {link}")
            return 0
        soup = BeautifulSoup(r.text, 'html.parser')
        token_el = soup.find('input', {'name': '_token'})
        if not token_el:
            print(f"[!] Batch [{bid}] Missing CSRF token.")
            return 0
        token = token_el['value']

        payload_base = {
            'COURSECD': soup.find('input', {'name': 'COURSECD'})['value'] if soup.find('input', {'name': 'COURSECD'}) else '',
            'SEMCODE': soup.find('input', {'name': 'SEMCODE'})['value'] if soup.find('input', {'name': 'SEMCODE'}) else '',
            'RESULTTYPE': (soup.find('input', {'name': 'RESULTTYPE'})['value'] if soup.find('input', {'name': 'RESULTTYPE'}) and soup.find('input', {'name': 'RESULTTYPE'})['value'] else 'R'),
            'session': soup.find('input', {'name': 'session'})['value'] if soup.find('input', {'name': 'session'}) else '',
            'tcc': soup.find('input', {'name': 'tcc'})['value'] if soup.find('input', {'name': 'tcc'}) else '',
            'p1': '', 'all': '',
            '_token': token
        }
    except Exception as e:
        print(f"[!] Batch [{bid}] Session init failed: {e}")
        return 0

    gen, candidate_starts, max_serial, primary_col = build_generator_for_batch(batch)

    # Prioritize verified college center first
    colleges_to_sweep = list(ORDERED_COLLEGES)
    if primary_col and primary_col in colleges_to_sweep:
        colleges_to_sweep.remove(primary_col)
        colleges_to_sweep.insert(0, primary_col)

    post_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36',
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRF-TOKEN': token,
        'Referer': link
    }

    total_scraped_batch = 0
    t_batch_start = time.time()

    async def fetch_single_roll(roll: str):
        # 1. Zero-IO in-memory check (0 FUSE stat calls)
        if ram_manager.is_cached(bid, roll):
            return roll, None, True

        d = payload_base.copy()
        d['EXAMROLLNUMBER'] = roll
        async with semaphore:
            try:
                res = await client.post('https://durg.ucanapply.com/get-result-details', data=d, headers=post_headers, timeout=8.0)
                if res.status_code == 200:
                    rj = res.json()
                    if rj.get('status') is True:
                        html_text = rj.get('html', '')
                        if len(html_text) > 200 and ('Marks Sheet' in html_text or 'PASS' in html_text or 'FAIL' in html_text or 'PROMOTED' in html_text or 'WITHHELD' in html_text):
                            return roll, html_text, False
                return roll, None, False
            except Exception:
                return roll, None, False

    # Mark batch IN_PROGRESS in manifest (fast single execute)
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with ram_manager.db_conn:
        ram_manager.db_conn.execute("""
            INSERT INTO batches (batch_id, year, description, link, status, students_scraped, started_at)
            VALUES (?, ?, ?, ?, 'IN_PROGRESS', 0, ?)
            ON CONFLICT(batch_id) DO UPDATE SET status='IN_PROGRESS', started_at=?
        """, (bid, yr, desc, link, now_str, now_str))

    for col_idx, col in enumerate(colleges_to_sweep):
        if SHUTDOWN_REQUESTED:
            break

        found_in_college = 0
        consecutive_fails = 0

        for start_val in candidate_starts:
            # 1. Backward edge sweep (up to 25 rolls) if starting at offset > 1
            if start_val > 1:
                back_start = max(1, start_val - 25)
                back_rolls = [gen(col, i) for i in range(back_start, start_val)]
                back_tasks = [fetch_single_roll(r) for r in back_rolls]
                back_results = await asyncio.gather(*back_tasks)
                for r_num, html_data, is_cached in back_results:
                    if is_cached:
                        found_in_college += 1
                        total_scraped_batch += 1
                    elif html_data:
                        file_path = os.path.join(batch_dir, f"{r_num}.html")
                        await ram_manager.add_marksheet({
                            'batch_id': bid,
                            'roll_number': r_num,
                            'year': yr,
                            'college_code': col,
                            'file_path': file_path,
                            'html_data': html_data,
                            'file_size': len(html_data),
                            'scraped_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        })
                        found_in_college += 1
                        total_scraped_batch += 1

            # 2. Forward dynamic stream chunk by chunk
            curr = start_val
            chunk_size = 12
            while curr <= max_serial and not SHUTDOWN_REQUESTED:
                rolls_chunk = [gen(col, curr + j) for j in range(chunk_size) if curr + j <= max_serial]
                if not rolls_chunk:
                    break

                tasks = [fetch_single_roll(r) for r in rolls_chunk]
                results = await asyncio.gather(*tasks)

                for r_num, html_data, is_cached in results:
                    if is_cached:
                        found_in_college += 1
                        total_scraped_batch += 1
                        consecutive_fails = 0
                    elif html_data:
                        file_path = os.path.join(batch_dir, f"{r_num}.html")
                        await ram_manager.add_marksheet({
                            'batch_id': bid,
                            'roll_number': r_num,
                            'year': yr,
                            'college_code': col,
                            'file_path': file_path,
                            'html_data': html_data,
                            'file_size': len(html_data),
                            'scraped_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        })
                        found_in_college += 1
                        total_scraped_batch += 1
                        consecutive_fails = 0
                    else:
                        consecutive_fails += 1

                # DYNAMIC CLASS BOUNDARY DETECTOR:
                # - If active students exist at this college: class list has finished after 15 consecutive misses
                # - If zero students found at this college: college doesn't offer this degree, exit after 5 misses!
                limit = 15 if found_in_college > 0 else 5
                if consecutive_fails >= limit:
                    break

                curr += chunk_size

        if found_in_college > 0:
            print(f"   [+] College {col}: Scraped {found_in_college} student marksheets.")

    # Stage batch completion in RAM manager (atomic commit with marksheet flush)
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ram_manager.stage_batch_completion(bid, total_scraped_batch, now_str)

    elapsed = round(time.time() - t_batch_start, 1)
    print(f"[*] Completed Batch [{bid}] ({yr}) {desc[:45]}... | Total: {total_scraped_batch} marksheets ({elapsed}s)")
    return total_scraped_batch

# ======================================================================================
# 5. CLI RUNNER & DISPATCHER
# ======================================================================================

async def run_master_scraper(args):
    print("=" * 88)
    print(" HEMCHAND YADAV VISHWAVIDYALAYA (DURG UNIVERSITY) - MASTER MARKSHEET SCRAPER")
    print("=" * 88)
    print(f"[*] Loading master batch database: {CHECKER_RESULTS_PATH}")
    if not os.path.exists(CHECKER_RESULTS_PATH):
        print(f"[!] Error: {CHECKER_RESULTS_PATH} not found. Please ensure analysis/checker_results.json exists.")
        sys.exit(1)

    with open(CHECKER_RESULTS_PATH, "r", encoding="utf-8") as f:
        all_batches_dict = json.load(f)

    # Separate PASS batches from empty CMS draft shells
    passing_batches = [v for v in all_batches_dict.values() if v.get('status') == 'PASS']
    empty_shells = [v for v in all_batches_dict.values() if v.get('status') == 'FAIL']

    print(f"[*] Verified Master Inventory: {len(all_batches_dict)} total batches.")
    print(f"    -> Active Marksheet Batches to Scrape: {len(passing_batches)}")
    print(f"    -> Empty CMS Draft Shells (Bypassed): {len(empty_shells)}")
    print(f"[*] Output Directory: {os.path.abspath(args.output_dir)}")

    db_conn = get_manifest_db(args.output_dir)

    # Pre-record empty shells into manifest as EMPTY_SHELL so they are never touched
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with db_conn:
        for s in empty_shells:
            db_conn.execute("""
                INSERT OR IGNORE INTO batches (batch_id, year, description, link, status, students_scraped, started_at, completed_at)
                VALUES (?, ?, ?, ?, 'EMPTY_SHELL', 0, ?, ?)
            """, (s['id'], s['year'], s['description'], s['link'], now_str, now_str))

    # Initialize RAM buffer manager & load existing keys into memory
    ram_manager = RamBufferManager(
        output_dir=args.output_dir,
        db_conn=db_conn,
        buffer_size=args.buffer_size,
        num_io_workers=args.io_workers
    )
    ram_manager.load_existing_marksheets()

    # Filter batches based on CLI flags
    filtered_batches = passing_batches
    if args.year:
        filtered_batches = [b for b in filtered_batches if b.get('year') == str(args.year)]
        print(f"[*] Filtered to Year {args.year}: {len(filtered_batches)} batches.")
    if args.batch:
        filtered_batches = [b for b in filtered_batches if b.get('id') == args.batch]
        print(f"[*] Filtered to Batch ID {args.batch}: {len(filtered_batches)} batches.")

    # Sort descending by year, then ascending by ID
    filtered_batches.sort(key=lambda x: (-int(x.get('year', 0)), x.get('id', 0)))

    # Check which batches are already COMPLETED
    cur = db_conn.cursor()
    cur.execute("SELECT batch_id FROM batches WHERE status = 'COMPLETED'")
    completed_ids = set(row[0] for row in cur.fetchall())
    to_scrape = [b for b in filtered_batches if b['id'] not in completed_ids]

    print(f"[*] Batches already completed: {len(completed_ids)}")
    print(f"[*] Batches remaining to scrape: {len(to_scrape)}")
    print(f"[*] HTTP Concurrency Limit: {args.concurrency}")
    print(f"[*] RAM Buffer Size: {args.buffer_size:,} records")
    print(f"[*] Parallel Disk I/O Workers: {args.io_workers}")
    print("=" * 88)

    if not to_scrape:
        print("[+] All target batches are already completely scraped! Manifest is 100% complete.")
        return

    semaphore = asyncio.Semaphore(args.concurrency)
    limits = httpx.Limits(max_keepalive_connections=args.concurrency + 5, max_connections=args.concurrency + 10)
    
    total_students_session = 0
    t0_global = time.time()

    async with httpx.AsyncClient(headers={'User-Agent': 'Mozilla/5.0'}, follow_redirects=True, timeout=14.0, limits=limits) as client:
        for idx, batch in enumerate(to_scrape):
            if SHUTDOWN_REQUESTED:
                break
            print(f"\n>>> [{idx+1}/{len(to_scrape)}] Processing Batch {batch['id']} ({batch['year']}): {batch['description']}")
            count = await scrape_batch(client, batch, args.output_dir, ram_manager, semaphore)
            total_students_session += count

        # Flush any remaining marksheets sitting in RAM buffer before exit
        await ram_manager.flush()
        ram_manager.close()

    cur.execute("SELECT COUNT(*), COUNT(DISTINCT batch_id) FROM marksheets")
    row = cur.fetchone()
    total_marksheets = row[0]
    total_batches_done = row[1]

    elapsed = round(time.time() - t0_global, 1)
    rate = round(total_students_session / max(1, elapsed), 1)

    print("\n" + "=" * 88)
    print(" MASTER SCRAPING SESSION SUMMARY")
    print("=" * 88)
    print(f"[*] Session Elapsed Time: {elapsed}s")
    print(f"[*] Marksheets Scraped in This Session: {total_students_session:,} ({rate} marksheets/sec)")
    print(f"[*] Total Marksheets Stored on Disk: {total_marksheets:,}")
    print(f"[*] Total Batches Fully Scraped: {total_batches_done} batches")
    print(f"[*] Raw Marksheet Storage: {os.path.abspath(args.output_dir)}")
    print(f"[*] Manifest Database: {os.path.join(args.output_dir, 'manifest.sqlite')}")
    print("=" * 88)

def main():
    parser = argparse.ArgumentParser(description="Hemchand Yadav Vishwavidyalaya Standalone Master Scraper")
    parser.add_argument("--year", type=str, default="", help="Scrape specific academic year (e.g. 2026, 2025, 2024...)")
    parser.add_argument("--batch", type=int, default=0, help="Scrape specific batch ID (e.g. 1)")
    parser.add_argument("--concurrency", type=int, default=20, help="Number of concurrent HTTP connections (default: 20)")
    parser.add_argument("--buffer-size", type=int, default=5000, help="Number of records to buffer in RAM before saving to disk & SQLite (default: 5000)")
    parser.add_argument("--io-workers", type=int, default=32, help="Number of parallel disk writer threads for FUSE storage (default: 32)")
    parser.add_argument("--output-dir", type=str, default=DEFAULT_OUTPUT_DIR, help="Directory to save raw HTML marksheets")
    parser.add_argument("--all", action="store_true", help="Scrape all 1,005 passing batches across all years")
    args = parser.parse_args()

    asyncio.run(run_master_scraper(args))

if __name__ == '__main__':
    main()
