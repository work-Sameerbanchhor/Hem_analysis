import re
import time
from fastapi import APIRouter, Query, HTTPException

from src.core.config import UNIFIED_CONFIGS
from src.services.roll_parser import sanitize_durg_roll_number
from src.services.html_parser import normalize_title, is_relevant_exam
from src.services.results import get_local_results_from_db
from src.services.catalog import load_unified_configs, save_exam_batch_to_rds
from src.services.scraper import fetch_latest_batches, execute_live_search

router = APIRouter(tags=["Results"])

_LAST_INDEX_CHECK_TIME = 0

@router.get("/api/results")
async def get_results(
    rollno: str = Query(..., min_length=1, max_length=50),
    mode: str = Query("both", description="Search mode: 'db', 'live', or 'both'")
):
    global _LAST_INDEX_CHECK_TIME

    # 1. Sanitize input roll number using official Durg University rules (RollNum.md Section 13)
    cleaned_roll, was_modified = sanitize_durg_roll_number(rollno)
    if cleaned_roll:
        rollno = cleaned_roll
    is_numeric = rollno.strip().isdigit()
    
    restricted_rolls = []
    if is_numeric and rollno.strip() in restricted_rolls:
        mode = "db"
        
    if is_numeric:
        if not re.match(r'^\d{8}$|^\d{10}$|^\d{11}$|^\d{12}$', rollno):
            raise HTTPException(status_code=400, detail="Invalid Roll Number length. Must be 8, 10, 11, or 12 digits.")
    else:
        if len(rollno.strip()) < 3:
            raise HTTPException(status_code=400, detail="Student name query must be at least 3 characters long.")

    # 2. Check live index periodically for new university exams (rate limited to once every 5 minutes)
    if mode in ["live", "both"]:
        current_time = time.time()
        if current_time - _LAST_INDEX_CHECK_TIME > 300:
            _LAST_INDEX_CHECK_TIME = current_time
            try:
                print("Checking live university result index for new exams...")
                latest_batches = await fetch_latest_batches()
                existing_keys = {(c["description"].strip().lower(), c.get("publication_date", "").strip(), c.get("source", "")) for c in UNIFIED_CONFIGS}
                
                new_added = False
                for b in latest_batches:
                    if not is_relevant_exam(b["description"], b["source"]):
                        continue
                    key = (b["description"].strip().lower(), b.get("publication_date", "").strip(), b.get("source", ""))
                    if key not in existing_keys:
                        save_exam_batch_to_rds(b)
                        new_added = True
                        
                if new_added:
                    load_unified_configs(force_reload=True)
            except Exception as e:
                print(f"Error auto-updating exam config during live query: {e}")

    seen_titles = set()
    marksheets = []

    # 3. Amazon RDS DB Lookup
    if mode in ["db", "both"]:
        results = get_local_results_from_db(rollno)
        if results:
            print(f"DB HIT for {rollno} — found {len(results)} results in Amazon RDS database.")
            for res in results:
                roll_val = res.get("student_info", {}).get("roll_no") or rollno
                norm = f"{roll_val}_{normalize_title(res.get('exam_title', ''))}"
                if norm not in seen_titles:
                    seen_titles.add(norm)
                    marksheets.append(res)
                        
    if mode == "db" or not is_numeric:
        return marksheets

    # 4. Live Portal Search (Targeted + Brute Force fallback)
    configs = load_unified_configs()
    marksheets = await execute_live_search(rollno, configs, seen_titles, marksheets)
    return marksheets
