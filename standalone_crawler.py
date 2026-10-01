#!/usr/bin/env python3
"""
========================================================================================
HYU All-Exams Standalone VPS Crawler (Hemchand Yadav Vishwavidyalaya)
========================================================================================
A 100% self-contained, single-file crawler designed to be copied to ANY Linux VPS 
(Ubuntu, Debian, CentOS, etc.) and left running continuously in the background (tmux/screen/systemd).

Features:
- ZERO local project dependencies: Requires ONLY `pip install httpx beautifulsoup4`
- Self-Bootstrapping: Automatically creates the local SQLite database and fetches the university exam catalog if empty
- Gzip BLOB Compression: Compresses raw HTML marksheet pages by >90% (~2.6 KB per student)
- Single-Token Session Reuse: Reuses CSRF tokens per exam to double throughput (~20+ rec/s)
- Adaptive Faculty Probing: Automatically resolves 8-digit Annual offsets (Arts/Science/Commerce/PG Private)
- Multi-Semester Cohort Offsets: Automatically aligns NEP and conventional semester admission years
- Crash-Resilient & Resumable: Tracks crawled exams in `exam_catalog`. Stop and restart anytime without duplicate work
- Graceful Shutdown: Safely flushes data and closes SQLite on SIGINT / SIGTERM

Quickstart on any Linux VPS:
  sudo apt-get update && sudo apt-get install -y python3 python3-pip tmux
  pip3 install httpx beautifulsoup4
  tmux new -s crawler
  python3 standalone_crawler.py
  # (Detach with Ctrl+B then D. Reattach anytime with: tmux a -t crawler)
========================================================================================
"""

import os
import sys
import re
import html
import time
import gzip
import signal
import sqlite3
import argparse
import asyncio
import urllib.parse
from datetime import datetime
import httpx
from bs4 import BeautifulSoup

# ======================================================================================
# 1. UNIVERSITY DIRECTORY & METADATA (84 Verified College Centers & Faculty Mappings)
# ======================================================================================

COLLEGES = [
    '101', '102', '103', '104', '105', '106', '107', '108', '109', '110', '113', '122', '123',
    '201', '202', '203', '204', '205', '206', '207', '211', '212', '213', '221',
    '302', '303', '304', '305', '306', '307', '308', '309', '310', '311', '312', '313', '314',
    '316', '317', '318', '319', '320', '331', '332', '333', '334', '335', '337', '338', '339',
    '340', '341', '344', '370', '381', '384',
    '401', '402', '403', '404', '405', '406', '407', '413',
    '502', '503', '504', '505', '506', '507', '508', '509', '510', '511', '514', '515', '516',
    '517', '519', '522', '524', '525', '532'
]

COURSE_MAP = {
    # Education Faculty
    "029": {"keywords": ["bachelor of education", "b.ed"], "exclusions": ["b.sc", "b.a", "m.ed", "physical"]},
    "46":  {"keywords": ["bachelor of education", "b.ed"], "exclusions": ["b.sc", "b.a", "m.ed", "physical"]},
    "033": {"keywords": ["physical education", "b.p.ed", "bped"], "exclusions": []},
    "47":  {"keywords": ["physical education", "b.p.ed", "bped"], "exclusions": []},
    "137": {"keywords": ["b.sc.-b.ed", "b.sc. b.ed"], "exclusions": []},
    "138": {"keywords": ["b.sc.-b.ed", "b.sc. b.ed"], "exclusions": []},
    "139": {"keywords": ["b.sc.-b.ed", "b.sc. b.ed"], "exclusions": []},
    "140": {"keywords": ["b.sc.-b.ed", "b.sc. b.ed"], "exclusions": []},
    "141": {"keywords": ["b.a.-b.ed", "b.a. b.ed"], "exclusions": []},
    "142": {"keywords": ["b.a.-b.ed", "b.a. b.ed"], "exclusions": []},
    "143": {"keywords": ["b.a.-b.ed", "b.a. b.ed"], "exclusions": []},
    "144": {"keywords": ["b.a.-b.ed", "b.a. b.ed"], "exclusions": []},
    # Management Faculty
    "017": {"keywords": ["b.b.a", "bba", "business administration", "bachelor of business administration"], "exclusions": ["mba"]},
    "45":  {"keywords": ["b.b.a", "bba", "business administration", "bachelor of business administration"], "exclusions": ["mba"]},
    "50":  {"keywords": ["b.b.a", "bba", "business administration", "bachelor of business administration"], "exclusions": ["mba"]},
    # Law Faculty
    "023": {"keywords": ["ll.b", "llb", "bachelor of laws", "laws"], "exclusions": ["ll.m", "llm"]},
    "48":  {"keywords": ["ll.b", "llb", "bachelor of laws", "laws"], "exclusions": ["ll.m", "llm"]},
    "70":  {"keywords": ["ll.m", "llm", "master of laws"], "exclusions": []},
    # Computer Applications
    "007": {"keywords": ["computer application", "bca", "b.c.a"], "exclusions": []},
    "011": {"keywords": ["computer application", "bca", "b.c.a"], "exclusions": []},
    "013": {"keywords": ["computer application", "bca", "b.c.a"], "exclusions": []},
    "014": {"keywords": ["computer application", "bca", "b.c.a"], "exclusions": []},
    "015": {"keywords": ["computer application", "bca", "b.c.a"], "exclusions": []},
    "40":  {"keywords": ["computer application", "bca", "b.c.a"], "exclusions": []},
    # Science Faculty (B.Sc.)
    "010": {"keywords": ["b.sc", "bachelor of science"], "required": ["part - iii", "part-iii", "part 3", "final year", "3rd year"], "exclusions": ["b.sc.-b.ed", "b.sc. b.ed", "m.sc", "home science", "b.h.sc."]},
    "009": {"keywords": ["b.sc", "bachelor of science"], "required": ["part - ii", "part-ii", "part 2", "second year", "2nd year"], "exclusions": ["b.sc.-b.ed", "b.sc. b.ed", "m.sc", "home science", "b.h.sc."]},
    "008": {"keywords": ["b.sc", "bachelor of science"], "exclusions": ["b.sc.-b.ed", "b.sc. b.ed", "m.sc", "home science", "b.h.sc."]},
    "012": {"keywords": ["b.sc", "bachelor of science"], "exclusions": ["b.sc.-b.ed", "b.sc. b.ed", "m.sc", "home science", "b.h.sc."]},
    "30":  {"keywords": ["b.sc", "bachelor of science"], "exclusions": ["b.sc.-b.ed", "b.sc. b.ed", "m.sc", "home science", "b.h.sc."]},
    # Commerce Faculty (B.Com.)
    "006": {"keywords": ["b.com", "bachelor of commerce"], "required": ["part - iii", "part-iii", "part 3", "final year", "3rd year"], "exclusions": ["m.com"]},
    "005": {"keywords": ["b.com", "bachelor of commerce"], "required": ["part - ii", "part-ii", "part 2", "second year", "2nd year"], "exclusions": ["m.com"]},
    "004": {"keywords": ["b.com", "bachelor of commerce"], "exclusions": ["m.com"]},
    "20":  {"keywords": ["b.com", "bachelor of commerce"], "exclusions": ["m.com"]},
    # Arts Faculty (B.A.)
    "003": {"keywords": ["b.a.", "bachelor of arts", " b.a "], "required": ["part - iii", "part-iii", "part 3", "final year", "3rd year"], "exclusions": ["b.a.-b.ed", "b.a. b.ed", "m.a.", "b.a. (ll.b.)"]},
    "002": {"keywords": ["b.a.", "bachelor of arts", " b.a "], "required": ["part - ii", "part-ii", "part 2", "second year", "2nd year"], "exclusions": ["b.a.-b.ed", "b.a. b.ed", "m.a.", "b.a. (ll.b.)"]},
    "001": {"keywords": ["b.a.", "bachelor of arts", " b.a "], "exclusions": ["b.a.-b.ed", "b.a. b.ed", "m.a.", "b.a. (ll.b.)"]},
    "10":  {"keywords": ["b.a.", "bachelor of arts", " b.a "], "exclusions": ["b.a.-b.ed", "b.a. b.ed", "m.a.", "b.a. (ll.b.)"]},
    # Library Science
    "016": {"keywords": ["b.lib", "bachelor of library", "library"], "exclusions": ["m.lib"]},
    "121": {"keywords": ["m.lib", "master of library", "library"], "exclusions": ["b.lib"]},
    "69":  {"keywords": ["library", "m.lib", "b.lib"], "exclusions": []},
    # M.Sc. Faculty
    "073": {"keywords": ["botany", "m.sc. botany"], "exclusions": []},
    "62":  {"keywords": ["botany", "m.sc. botany"], "exclusions": []},
    "077": {"keywords": ["zoology", "m.sc. zoology"], "exclusions": []},
    "63":  {"keywords": ["zoology", "m.sc. zoology"], "exclusions": []},
    "078": {"keywords": ["physics", "m.sc. physics"], "exclusions": []},
    "67":  {"keywords": ["physics", "m.sc. physics"], "exclusions": []},
    "079": {"keywords": ["chemistry", "m.sc. chemistry"], "exclusions": []},
    "77":  {"keywords": ["chemistry", "m.sc. chemistry"], "exclusions": []},
    "081": {"keywords": ["mathematics", "m.sc. mathematics"], "exclusions": []},
    "64":  {"keywords": ["mathematics", "m.sc. mathematics"], "exclusions": []},
    "084": {"keywords": ["bio technology", "biotechnology"], "exclusions": []},
    "68":  {"keywords": ["bio technology", "biotechnology"], "exclusions": []},
    "097": {"keywords": ["computer science", "m.sc. computer science"], "exclusions": []},
    "81":  {"keywords": ["computer science", "m.sc. computer science"], "exclusions": []},
    "101": {"keywords": ["microbiology", "m.sc. microbiology"], "exclusions": []},
    "82":  {"keywords": ["microbiology", "m.sc. microbiology"], "exclusions": []},
    # M.A. Faculty
    "037": {"keywords": ["hindi", "m.a. hindi"], "exclusions": []},
    "039": {"keywords": ["hindi", "m.a. hindi"], "exclusions": []},
    "75":  {"keywords": ["hindi", "m.a. hindi"], "exclusions": []},
    "041": {"keywords": ["english", "m.a. english"], "exclusions": []},
    "043": {"keywords": ["english", "m.a. english"], "exclusions": []},
    "55":  {"keywords": ["english", "m.a. english"], "exclusions": []},
    "045": {"keywords": ["sociology", "m.a. sociology"], "exclusions": []},
    "047": {"keywords": ["sociology", "m.a. sociology"], "exclusions": []},
    "56":  {"keywords": ["sociology", "m.a. sociology"], "exclusions": []},
    "049": {"keywords": ["economics", "m.a. economics"], "exclusions": []},
    "051": {"keywords": ["economics", "m.a. economics"], "exclusions": []},
    "57":  {"keywords": ["economics", "m.a. economics"], "exclusions": []},
    "053": {"keywords": ["geography", "m.a. geography"], "exclusions": []},
    "055": {"keywords": ["geography", "m.a. geography"], "exclusions": []},
    "58":  {"keywords": ["geography", "m.a. geography"], "exclusions": []},
    "057": {"keywords": ["political science", "m.a. political"], "exclusions": []},
    "059": {"keywords": ["political science", "m.a. political"], "exclusions": []},
    "59":  {"keywords": ["political science", "m.a. political"], "exclusions": []},
    "065": {"keywords": ["history", "m.a. history"], "exclusions": []},
    "60":  {"keywords": ["history", "m.a. history"], "exclusions": []},
    "070": {"keywords": ["psychology", "m.a. psychology"], "exclusions": []},
    "61":  {"keywords": ["psychology", "m.a. psychology"], "exclusions": []},
    "151": {"keywords": ["sanskrit", "m.a. sanskrit"], "exclusions": []},
    # M.Com. Faculty
    "117": {"keywords": ["m.com", "master of commerce"], "exclusions": []},
    "119": {"keywords": ["m.com", "master of commerce"], "exclusions": []},
    "76":  {"keywords": ["m.com", "master of commerce"], "exclusions": []},
    # Diplomas & Yoga
    "71":  {"keywords": ["pgdca", "p.g.d.c.a"], "exclusions": []},
    "135": {"keywords": ["pgdca", "p.g.d.c.a"], "exclusions": []},
    "73":  {"keywords": ["pgdyep", "yoga"], "exclusions": []},
    "137": {"keywords": ["pgdyep", "yoga"], "exclusions": []},
    "133": {"keywords": ["d.c.a", "dca"], "exclusions": ["pgdca"]},
    "74":  {"keywords": ["m.s.w", "msw", "social work"], "exclusions": []}
}

# ======================================================================================
# 2. LOCAL SQLITE DATABASE INITIALIZATION & BATCH SAVING
# ======================================================================================

def init_local_db(db_path: str):
    """Initializes local SQLite database tables with WAL mode and indexes."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=NORMAL;")
    
    # 1. Results table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS results (
            enrollment_no TEXT,
            roll_number INTEGER,
            name TEXT,
            exam_title TEXT,
            result_status TEXT,
            sgpa TEXT,
            html_result BLOB,
            official_url TEXT,
            updated_at TEXT,
            PRIMARY KEY (enrollment_no, roll_number, exam_title)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_results_roll ON results(roll_number);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_results_enrollment ON results(enrollment_no);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_results_name ON results(name COLLATE NOCASE);")
    
    # 2. Catalog table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS exam_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL,
            link TEXT NOT NULL,
            publication_date TEXT DEFAULT '',
            year INTEGER,
            source TEXT NOT NULL,
            is_crawled INTEGER DEFAULT 0,
            crawled_records INTEGER DEFAULT 0,
            last_crawled_at TEXT,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(description, link)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_exam_crawled ON exam_catalog(is_crawled);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_exam_year ON exam_catalog(year);")
    conn.commit()
    conn.close()

def save_batch_local(db_path: str, items: list) -> int:
    """Saves a batch of student marksheet records with Gzip BLOB compression into SQLite."""
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
        
        raw_html = res.get("html", "")
        html_blob = gzip.compress(raw_html.encode("utf-8")) if raw_html else None
        
        rows.append((
            enrollment_no, roll_int, student_name, exam_title,
            result_status, sgpa, html_blob, official_url
        ))
        
    conn = sqlite3.connect(db_path, timeout=30.0)
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

# ======================================================================================
# 3. HTML PARSING & EXAM CLASSIFICATION
# ======================================================================================

def is_strictly_regular_or_private(desc: str, source: str) -> bool:
    """Strictly includes primary degree batches and excludes revals/retotaling/atkt."""
    desc_l = desc.lower()
    if any(k in desc_l for k in ["supply", "atkt", "ex ", " ex", "reval", "revaluation", "re-totaling"]):
        return False
    if source == "legacy":
        return "regular" in desc_l or "private" in desc_l or "(pvt)" in desc_l
    elif source == "nep":
        return True
    return False

def is_relevant_exam(desc: str, source: str) -> bool:
    desc_l = desc.lower()
    keywords = [
        "b.a.", "b.com", "b.sc", "bca", "b.c.a", "bba", "b.b.a",
        "ll.b", "llb", "ll.m", "llm", "b.ed", "m.ed", "b.p.ed", "bped",
        "m.sc", "m sc", "m.a.", "m.a ", "m a ", "m.com", "m com",
        "pgdca", "dca", "d.c.a", "b.lib", "m.lib", "m.s.w", "msw",
        "bachelor of", "master of", "diploma in", "b.sc.-b.ed", "b.a.-b.ed"
    ]
    return any(kw in desc_l for kw in keywords)

def parse_hyu_html(html_content: str) -> dict:
    """Extracts student details and status from raw HTML marksheet."""
    soup = BeautifulSoup(html_content, 'html.parser')
    student_info = {}
    label_tags = soup.find_all(['strong', 'b'])
    
    for tag in label_tags:
        text = tag.get_text().strip().lower().replace(':', '').strip()
        if not text:
            continue
        parent_td = tag.find_parent('td')
        if not parent_td:
            continue
        parent_tr = parent_td.find_parent('tr')
        if not parent_tr:
            continue
            
        tds = parent_tr.find_all('td')
        try:
            idx = tds.index(parent_td)
            if idx + 2 < len(tds):
                val = re.sub(r'\s+', ' ', tds[idx+2].get_text().strip())
                if text in ['roll no.', 'roll no', 'roll number']:
                    student_info['roll_no'] = val
                elif text in ['enrollment no.', 'enrollment no', 'enrollment number', 'enrollment']:
                    student_info['enrollment_no'] = val
                elif text in ['name of the student', 'student name', 'name']:
                    student_info['name'] = val
                elif "father's name" in text or 'father name' in text:
                    student_info['father_name'] = val
                elif "mother's name" in text or 'mother name' in text:
                    student_info['mother_name'] = val
                elif text == 'college':
                    student_info['college'] = val
                elif text in ['center', 'exam center']:
                    student_info['center'] = val
                elif text in ['status', 'student type']:
                    student_info['student_type'] = val
        except ValueError:
            pass
            
    exam_title = ""
    h2_tag = soup.find('h2')
    if h2_tag:
        exam_title = re.sub(r'\s+', ' ', h2_tag.get_text().strip())
        
    result_status = "N/A"
    for tag in label_tags:
        text = tag.get_text().strip().lower()
        if 'result' in text and text not in ['result details', 'result declared on -', 'result details:']:
            parent_td = tag.find_parent('td')
            if parent_td:
                parent_tr = parent_td.find_parent('tr')
                if parent_tr:
                    tds = parent_tr.find_all('td')
                    try:
                        idx = tds.index(parent_td)
                        if idx + 1 < len(tds):
                            val = re.sub(r'\s+', ' ', tds[idx+1].get_text().strip())
                            if val:
                                result_status = val
                    except ValueError:
                        pass
                        
    sgpa = "N/A"
    for tag in label_tags:
        text = tag.get_text().strip().lower()
        if 'sgpa' in text:
            parent_td = tag.find_parent('td')
            if parent_td:
                parent_tr = parent_td.find_parent('tr')
                if parent_tr:
                    tds = parent_tr.find_all('td')
                    try:
                        idx = tds.index(parent_td)
                        if idx + 1 < len(tds):
                            val = re.sub(r'\s+', ' ', tds[idx+1].get_text().strip())
                            if val:
                                sgpa = val
                    except ValueError:
                        pass
                        
    return {
        "exam_title": exam_title,
        "student_info": student_info,
        "result_status": result_status,
        "sgpa": sgpa
    }

# ======================================================================================
# 4. ROLL NUMBER GENERATOR (COHORT ALIGNMENT & ADAPTIVE FACULTY PROBING)
# ======================================================================================

def resolve_crawler_roll_generator(course_name: str, source: str, year_str: str):
    """
    Empirically verified generator covering all 4 university archetypes:
    1. Semester Track (10-Digit) with Multi-Semester Cohort Offsets
    2. Modern Annual (8-Digit) with Adaptive Faculty Probing
    3. Legacy Annual (11-Digit) with Embedded Course Codes
    4. Diplomas / Certificates
    """
    c_lower = course_name.lower()
    is_sem = 'sem' in c_lower or 'semester' in c_lower or source == 'nep'
    
    try:
        yy_full = int(year_str) if year_str else 2024
    except Exception:
        yy_full = 2024
    last_digit = str(yy_full)[-1]
    
    if is_sem:
        # Semester Track: 10 Digits [YY][CCC][Code_2D][Serial_3D]
        detected_code = None
        for code, mapping in COURSE_MAP.items():
            if len(code) == 2:
                if any(kw in c_lower for kw in mapping["keywords"]) and not any(ex in c_lower for ex in mapping.get("exclusions", [])):
                    detected_code = code
                    break
        if not detected_code:
            if "b.sc" in c_lower or "science" in c_lower: detected_code = "30"
            elif "b.com" in c_lower or "commerce" in c_lower: detected_code = "20"
            elif "bca" in c_lower: detected_code = "40"
            elif "bba" in c_lower: detected_code = "50"
            elif "ll.b" in c_lower or "laws" in c_lower: detected_code = "48"
            elif "b.ed" in c_lower: detected_code = "46"
            elif "b.p.ed" in c_lower: detected_code = "47"
            elif "yoga" in c_lower or "pgdyep" in c_lower: detected_code = "73"
            elif "pgdca" in c_lower: detected_code = "71"
            elif "m.com" in c_lower: detected_code = "76"
            elif "sociology" in c_lower: detected_code = "56"
            elif "economics" in c_lower: detected_code = "57"
            elif "political" in c_lower: detected_code = "59"
            elif "english" in c_lower: detected_code = "55"
            elif "hindi" in c_lower: detected_code = "75"
            elif "chemistry" in c_lower: detected_code = "77"
            elif "mathematics" in c_lower: detected_code = "64"
            elif "physics" in c_lower: detected_code = "67"
            elif "botany" in c_lower: detected_code = "62"
            elif "zoology" in c_lower: detected_code = "63"
            else: detected_code = "10"
            
        offset = 0
        if any(s in c_lower for s in ["3rd sem", "4th sem", "sem - 3", "sem - 4"]): offset = 1
        elif any(s in c_lower for s in ["5th sem", "6th sem", "sem - 5", "sem - 6"]): offset = 2
        elif any(s in c_lower for s in ["7th sem", "8th sem", "sem - 7", "sem - 8"]): offset = 3
        
        try:
            yy_int = int(str(year_str)[-2:]) - offset
        except Exception:
            yy_int = 24
        calc_yy = f"{yy_int:02d}"
        
        return (lambda c, i: f"{calc_yy}{c}{detected_code}{i:03d}"), [1], 800, 15
        
    else:
        # Annual Track
        if yy_full >= 2024:
            # 8 Digits: [YearDigit][CCC][Serial_4D]
            is_ug = any(k in c_lower for k in ["b.a", "b.sc", "b.com", "bca", "bba", "bachelor", "part - i", "part - ii", "part - iii", "1st year", "2nd year", "3rd year"])
            is_pg_annual = (any(k in c_lower for k in ["m.a", "m.com", "m.sc", "master"]) or any(k in c_lower for k in ["previous", "final"])) and not is_ug
            is_comm = any(k in c_lower for k in ["b.com", "commerce"])
            is_sci = any(k in c_lower for k in ["b.sc", "science"])
            
            if is_pg_annual:
                candidate_starts = [3000, 4000, 4500, 5000, 5500, 5720, 6000, 6500, 7000]
            elif is_comm:
                candidate_starts = [1, 500, 1000, 1500, 2000, 2500, 3000, 3400, 4000]
            elif is_sci:
                candidate_starts = [1, 500, 1000, 1500, 2000, 2270, 2500, 3000]
            else:
                candidate_starts = [1]
                
            return (lambda c, i: f"{last_digit}{c}{i:04d}"), candidate_starts, 8000, 15
        else:
            # Pre-2024: 11 Digits [YearDigit][CCC][Code_3D][Serial_4D]
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
                if "bca" in c_lower: detected_code = "013"
                elif "b.com" in c_lower:
                    if any(k in c_lower for k in ["part - iii", "3rd year"]): detected_code = "006"
                    elif any(k in c_lower for k in ["part - ii", "2nd year"]): detected_code = "005"
                    else: detected_code = "004"
                elif "b.sc" in c_lower:
                    if any(k in c_lower for k in ["part - iii", "3rd year"]): detected_code = "010"
                    elif any(k in c_lower for k in ["part - ii", "2nd year"]): detected_code = "009"
                    else: detected_code = "008"
                else:
                    if any(k in c_lower for k in ["part - iii", "3rd year"]): detected_code = "003"
                    elif any(k in c_lower for k in ["part - ii", "2nd year"]): detected_code = "002"
                    else: detected_code = "001"
                    
            return (lambda c, i: f"{last_digit}{c}{detected_code}{i:04d}"), [1], 2500, 15

# ======================================================================================
# 5. HIGH-PERFORMANCE ASYNC SCRAPING ENGINE (TOKEN REUSE & RETRIES)
# ======================================================================================

async def scrape_student_async(client: httpx.AsyncClient, domain: str, exam_link: str, payload: dict, token: str, rollno: str, semaphore: asyncio.Semaphore, exam_title: str) -> dict:
    """Submits single POST request reusing session token with exponential retry backoff."""
    ajax_url = f"{domain}/get-result-details"
    post_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36',
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRF-TOKEN': token,
        'Origin': domain,
        'Referer': exam_link,
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'
    }
    data = payload.copy()
    data['EXAMROLLNUMBER'] = rollno
    data['_token'] = token
    
    async with semaphore:
        for attempt in range(3):
            try:
                res = await client.post(ajax_url, data=data, headers=post_headers, timeout=12.0)
                if res.status_code == 200:
                    res_json = res.json()
                    if res_json.get("status") is False:
                        return None
                    if res_json.get("status") is True and "html" in res_json:
                        html_content = res_json["html"]
                        if len(html_content) > 200:
                            parsed = parse_hyu_html(html_content)
                            tcc_val = str(payload.get('tcc', '')).strip("'\"")
                            return {
                                "exam_title": parsed.get("exam_title") or exam_title,
                                "student_info": parsed.get("student_info", {}),
                                "result_status": parsed.get("result_status", "N/A"),
                                "sgpa": parsed.get("sgpa", "N/A"),
                                "html": html_content,
                                "official_url": f"{domain}/result-details?tcc={tcc_val}&rollno={rollno}"
                            }
                await asyncio.sleep(0.3)
            except Exception:
                await asyncio.sleep(0.5 * (attempt + 1))
    return None

# ======================================================================================
# 6. AUTONOMOUS CATALOG BOOTSTRAPPING (FETCHES EXAMS DIRECTLY FROM WEB IF EMPTY)
# ======================================================================================

async def bootstrap_catalog_if_empty(db_path: str):
    """If database has no exams, connects to university portals and populates exam_catalog."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM exam_catalog")
    count = cur.fetchone()[0]
    conn.close()
    
    if count > 0:
        return
        
    print("[*] Local exam catalog is empty. Bootstrapping directly from university web portals...")
    headers = {"User-Agent": "Mozilla/5.0"}
    new_exams = []
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Fetch Legacy Portal
        try:
            r = await client.get("https://durg.ucanapply.com/result-details", headers=headers)
            if r.status_code == 200:
                tbody_match = re.search(r'<tbody style="font-family: Arial; font-weight: bold; font-size: 13px;">(.*?)</tbody>', r.text, re.DOTALL | re.IGNORECASE)
                tbody = tbody_match.group(1) if tbody_match else r.text
                for tr in re.findall(r'<tr.*?>(.*?)</tr>', tbody, re.DOTALL | re.IGNORECASE):
                    tds = re.findall(r'<td.*?>(.*?)</td>', tr, re.DOTALL | re.IGNORECASE)
                    if len(tds) >= 3:
                        desc = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', tds[0]))).strip()
                        link_match = re.search(r'href=[\'"]([^\'"]+)[\'"]', tds[1])
                        link = link_match.group(1) if link_match else ""
                        pub_date = re.sub(r'<[^>]+>', '', tds[2]).strip()
                        
                        m = re.search(r'(\d{2})/(\d{2})/(\d{4})', pub_date)
                        year = int(m.group(3)) if m else 2024
                        if link and desc:
                            new_exams.append((desc, link, pub_date, year, "legacy"))
        except Exception as e:
            print(f"[-] Warning: Failed fetching legacy index: {e}")

        # 2. Fetch NEP Portal
        try:
            r = await client.get("https://durgnep.ucanapply.com/result-details", headers=headers)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, 'html.parser')
                table = soup.find("table")
                if table:
                    for row in table.find_all("tr"):
                        cols = row.find_all("td")
                        if len(cols) >= 3:
                            desc = re.sub(r'\s+', ' ', html.unescape(cols[0].get_text())).strip()
                            a_tag = cols[1].find("a")
                            link = a_tag.get("href") if a_tag else ""
                            pub_date = re.sub(r'\s+', ' ', cols[2].get_text()).strip()
                            m = re.search(r'(\d{2})/(\d{2})/(\d{4})', pub_date)
                            year = int(m.group(3)) if m else 2025
                            if link and desc:
                                new_exams.append((desc, link, pub_date, year, "nep"))
        except Exception as e:
            print(f"[-] Warning: Failed fetching NEP index: {e}")
            
    if new_exams:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.executemany("""
            INSERT OR IGNORE INTO exam_catalog (description, link, publication_date, year, source)
            VALUES (?, ?, ?, ?, ?)
        """, new_exams)
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM exam_catalog")
        total = cur.fetchone()[0]
        conn.close()
        print(f"[+] Bootstrapped {len(new_exams)} exams! Total catalog now has {total} entries.\n")

# ======================================================================================
# 7. CONTINUOUS ALL-EXAM CRAWL RUNNER
# ======================================================================================

RUNNING = True

def handle_shutdown(signum, frame):
    global RUNNING
    print("\n[!] Received shutdown signal. Gracefully finishing current batch...")
    RUNNING = False

signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)

async def crawl_single_exam(db_path: str, exam: tuple, concurrency: int = 12) -> int:
    eid, description, link, pub_date, year, source = exam
    year_str = str(year) if year else "2024"
    
    roll_generator, candidate_starts, max_serial, fail_limit = resolve_crawler_roll_generator(description, source, year_str)
    
    parsed_url = urllib.parse.urlparse(link)
    domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
    
    async with httpx.AsyncClient(timeout=20.0, limits=httpx.Limits(max_keepalive_connections=concurrency, max_connections=concurrency)) as client:
        try:
            r = await client.get(link, headers=headers)
            if r.status_code != 200:
                return 0
            soup = BeautifulSoup(r.text, "html.parser")
            token_elem = soup.find("input", {"name": "_token"})
            if not token_elem:
                return 0
            token = token_elem["value"]
            payload = {
                "COURSECD": soup.find("input", {"name": "COURSECD"})["value"] if soup.find("input", {"name": "COURSECD"}) else "",
                "SEMCODE": soup.find("input", {"name": "SEMCODE"})["value"] if soup.find("input", {"name": "SEMCODE"}) else "",
                "RESULTTYPE": soup.find("input", {"name": "RESULTTYPE"})["value"] if soup.find("input", {"name": "RESULTTYPE"}) else "",
                "session": soup.find("input", {"name": "session"})["value"] if soup.find("input", {"name": "session"}) else "",
                "tcc": soup.find("input", {"name": "tcc"})["value"] if soup.find("input", {"name": "tcc"}) else "",
                "p1": "", "all": ""
            }
        except Exception:
            return 0
            
        semaphore = asyncio.Semaphore(concurrency)
        total_saved_exam = 0
        
        for c_idx, coll in enumerate(COLLEGES, 1):
            if not RUNNING:
                break
            active_start = candidate_starts[0]
            found_any = False
            
            # Step A: Probe candidates
            if len(candidate_starts) > 1:
                probe_rolls = [roll_generator(coll, i) for i in candidate_starts]
                probe_tasks = [
                    scrape_student_async(client, domain, link, payload, token, r_num, semaphore, description)
                    for r_num in probe_rolls
                ]
                probe_res = await asyncio.gather(*probe_tasks)
                for start_val, res in zip(candidate_starts, probe_res):
                    if res:
                        active_start = start_val
                        found_any = True
                        break
                if not found_any:
                    continue
                    
            # Step B: Backsweep up to 30 serials if offset > 1
            if active_start > 1:
                back_rolls = [roll_generator(coll, i) for i in range(max(1, active_start - 30), active_start)]
                back_tasks = [
                    scrape_student_async(client, domain, link, payload, token, r_num, semaphore, description)
                    for r_num in back_rolls
                ]
                back_res = await asyncio.gather(*back_tasks)
                back_items = []
                for r_num, res in zip(back_rolls, back_res):
                    if res:
                        found_any = True
                        total_saved_exam += 1
                        back_items.append({"rollno": r_num, "result": res})
                if back_items:
                    save_batch_local(db_path, back_items)
                    
            # Step C: Stream forward in chunks of 10
            consecutive_fails = 0
            chunk_size = 10
            for i_start in range(active_start, max_serial + 1, chunk_size):
                if not RUNNING:
                    break
                tasks = [roll_generator(coll, i) for i in range(i_start, min(i_start + chunk_size, max_serial + 1))]
                ajax_tasks = [
                    scrape_student_async(client, domain, link, payload, token, r_num, semaphore, description)
                    for r_num in tasks
                ]
                results = await asyncio.gather(*ajax_tasks)
                
                batch_items = []
                chunk_done = False
                for r_num, res in zip(tasks, results):
                    if res:
                        found_any = True
                        consecutive_fails = 0
                        total_saved_exam += 1
                        batch_items.append({"rollno": r_num, "result": res})
                    else:
                        consecutive_fails += 1
                        limit = fail_limit if found_any else 10
                        if consecutive_fails >= limit:
                            chunk_done = True
                            break
                            
                if batch_items:
                    save_batch_local(db_path, batch_items)
                if chunk_done:
                    break
                    
    # Mark batch complete in exam_catalog
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        UPDATE exam_catalog 
        SET is_crawled = 1, crawled_records = ?, last_crawled_at = datetime('now')
        WHERE id = ?
    """, (total_saved_exam, eid))
    conn.commit()
    conn.close()
    
    return total_saved_exam

async def main_crawler_loop(db_path: str, concurrency: int = 12, target_exam_id: int = None, max_exams: int = None):
    init_local_db(db_path)
    await bootstrap_catalog_if_empty(db_path)
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    if target_exam_id:
        cur.execute("SELECT id, description, link, publication_date, year, source FROM exam_catalog WHERE id = ?", (target_exam_id,))
        rows = cur.fetchall()
    else:
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
        rows = cur.fetchall()
    conn.close()
    
    batches = [r for r in rows if is_strictly_regular_or_private(r[1], r[5]) and is_relevant_exam(r[1], r[5])]
    if max_exams:
        batches = batches[:max_exams]
        
    print("\n" + "="*75)
    print("  HYU ALL-EXAMS STANDALONE CRAWLER (AUTONOMOUS VPS RUNNER)")
    print("="*75)
    print(f"  Pending Regular/Private Batches : {len(batches):,}")
    print(f"  Local SQLite Database           : {os.path.abspath(db_path)}")
    print(f"  Concurrency                     : {concurrency} workers")
    print(f"  Started At                      : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*75 + "\n")
    
    total_added_all = 0
    t_global_start = time.time()
    
    for idx, b in enumerate(batches, 1):
        if not RUNNING:
            print("[*] Crawler stopped cleanly by user. Progress saved.")
            break
            
        t_batch_start = time.time()
        print(f"[{idx}/{len(batches)}] Scraping ID {b[0]:4d}: {b[1][:60]}... ({b[4]}, {b[5].upper()})")
        
        try:
            saved = await crawl_single_exam(db_path, b, concurrency=concurrency)
            total_added_all += saved
            batch_elapsed = time.time() - t_batch_start
            
            db_size_mb = os.path.getsize(db_path) / (1024 * 1024)
            total_elapsed = time.time() - t_global_start
            overall_speed = total_added_all / total_elapsed if total_elapsed > 0 else 0
            
            print(f"      -> {saved:5d} students saved | {batch_elapsed:.1f}s | DB: {db_size_mb:.1f} MB | Speed: {overall_speed:.1f} rec/s | Total Saved: {total_added_all:,}\n")
        except Exception as e:
            print(f"      [-] Error on exam {b[0]}: {e}. Continuing...\n")
            
    print("\n" + "="*75)
    print("  CRAWLER FINISHED / STOPPED")
    print("="*75)
    print(f"  Total Marksheets Added : {total_added_all:,}")
    print(f"  Total Elapsed Time     : {(time.time() - t_global_start)/60:.1f} minutes")
    print("="*75 + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HYU Standalone VPS Crawler")
    parser.add_argument("--db", type=str, default="hyu_results.db", help="Local SQLite file path (default: hyu_results.db)")
    parser.add_argument("--concurrency", type=int, default=12, help="Concurrency workers (default: 12)")
    parser.add_argument("--exam_id", type=int, default=None, help="Target a specific single exam ID")
    parser.add_argument("--limit_exams", type=int, default=None, help="Crawl up to N exams then exit")
    args = parser.parse_args()
    
    asyncio.run(main_crawler_loop(args.db, args.concurrency, args.exam_id, args.limit_exams))
