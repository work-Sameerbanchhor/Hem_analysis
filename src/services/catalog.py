import os
import re
import html
from datetime import datetime
from bs4 import BeautifulSoup
from psycopg2.extras import execute_values

from src.core.config import BASE_DIR, UNIFIED_CONFIGS
from src.core.database import get_db_conn

def sync_indices_to_rds(legacy_html_path: str = None, nep_html_path: str = None) -> int:
    """
    Parses full university HTML index files (legacy_index.html and nep_index.html),
    extracts clean unique exams, and upserts them directly into RDS exam_catalog table.
    """
    legacy_file = legacy_html_path or os.path.join(BASE_DIR, "legacy_index.html")
    nep_file = nep_html_path or os.path.join(BASE_DIR, "nep_index.html")
    catalog = {}

    # 1. Parse Legacy Index
    if os.path.exists(legacy_file):
        try:
            with open(legacy_file, "r", encoding="utf-8") as f:
                soup = BeautifulSoup(f.read(), "html.parser")
            for tr in soup.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) >= 3:
                    desc = re.sub(r"\s+", " ", html.unescape(tds[0].get_text()).replace("\xa0", " ")).strip()
                    a = tds[1].find("a")
                    link = a["href"].strip() if a and a.get("href") else ""
                    pub_date = re.sub(r"\s+", " ", tds[2].get_text()).strip()
                    year = None
                    try:
                        if pub_date:
                            year = datetime.strptime(pub_date, "%d/%m/%Y").year
                    except:
                        pass
                    if desc and link:
                        key = (desc.lower(), pub_date, "legacy")
                        catalog[key] = (desc, link, pub_date, year, "legacy")
        except Exception as e:
            print(f"Error parsing legacy index HTML: {e}")

    # 2. Parse NEP Index
    if os.path.exists(nep_file):
        try:
            with open(nep_file, "r", encoding="utf-8") as f:
                soup = BeautifulSoup(f.read(), "html.parser")
            table = soup.find("table", id="print_data")
            if table:
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
                for tr in table.find("tbody").find_all("tr"):
                    tds = tr.find_all("td")
                    if len(tds) >= 3:
                        raw_desc = re.sub(r"\s+", " ", html.unescape(tds[0].get_text()).replace("\xa0", " ")).strip()
                        a = tds[1].find("a")
                        link = a["href"].strip() if a and a.get("href") else ""
                        pub_date = re.sub(r"\s+", " ", tds[2].get_text()).strip()
                        
                        course_prefix = next((v for k, v in course_prefixes.items() if k in raw_desc), None)
                        sem_text = next((v for k, v in semester_map.items() if k in raw_desc), None)
                        year = None
                        m = re.search(r"(\d{2})/(\d{2})/(\d{4})", pub_date)
                        if m:
                            day, month, year_num = map(int, m.groups())
                            if course_prefix and sem_text:
                                is_odd = sem_text in ["1st Sem", "3rd Sem", "5th Sem"]
                                session_year_num = year_num - 1 if month == 1 else year_num
                                session_str = f"Dec-Jan {session_year_num - 1}-{str(session_year_num)[-2:]}" if is_odd else f"May-June {session_year_num}"
                                desc = f"{course_prefix} {sem_text} (NEP) {session_str}"
                                year = session_year_num
                            else:
                                desc = raw_desc
                                year = year_num
                        else:
                            desc = raw_desc

                        if desc and link:
                            key = (desc.lower(), pub_date, "nep")
                            catalog[key] = (desc, link, pub_date, year, "nep")
        except Exception as e:
            print(f"Error parsing NEP index HTML: {e}")

    if not catalog:
        return 0

    records = list(catalog.values())
    try:
        with get_db_conn() as conn:
            with conn.cursor() as cur:
                upsert_sql = """
                    INSERT INTO exam_catalog (description, link, publication_date, year, source)
                    VALUES %s
                    ON CONFLICT (description, publication_date, source) DO UPDATE SET
                        link = EXCLUDED.link,
                        year = EXCLUDED.year,
                        updated_at = CURRENT_TIMESTAMP
                """
                execute_values(cur, upsert_sql, records, page_size=1000)
            conn.commit()
        print(f"Synced {len(records):,} clean exam entries into Amazon RDS exam_catalog.")
    except Exception as e:
        print(f"Error syncing indices to RDS exam_catalog: {e}")

    return len(records)

def load_unified_configs(force_reload: bool = False):
    """
    Loads all active exam configurations from Amazon RDS exam_catalog into RAM (UNIFIED_CONFIGS).
    If the RDS table is empty, automatically synchronizes from downloaded HTML index files.
    """
    global UNIFIED_CONFIGS
    if UNIFIED_CONFIGS and not force_reload:
        return UNIFIED_CONFIGS
        
    configs = []
    try:
        with get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT description, link, publication_date, year, source 
                    FROM exam_catalog 
                    ORDER BY year DESC NULLS LAST, id DESC
                """)
                rows = cur.fetchall()
                for r in rows:
                    configs.append({
                        "description": r[0],
                        "link": r[1],
                        "publication_date": r[2],
                        "year": r[3],
                        "source": r[4]
                    })
        UNIFIED_CONFIGS.clear()
        UNIFIED_CONFIGS.extend(configs)
        print(f"Loaded {len(UNIFIED_CONFIGS):,} exam configurations from Amazon RDS exam_catalog.")
    except Exception as e:
        print(f"Error loading exam configurations from RDS: {e}")

    # If RDS table had zero records, perform initial sync from HTML index
    if not UNIFIED_CONFIGS:
        synced = sync_indices_to_rds()
        if synced > 0:
            return load_unified_configs(force_reload=True)

    return UNIFIED_CONFIGS

def save_exam_batch_to_rds(batch: dict):
    desc = batch.get("description", "").strip()
    link = batch.get("link", "").strip()
    pub_date = batch.get("publication_date", "").strip()
    year = batch.get("year")
    source = batch.get("source", "legacy")
    if not desc or not link:
        return
    try:
        year_int = int(year) if year and str(year).isdigit() else None
    except:
        year_int = None
        
    try:
        with get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO exam_catalog (description, link, publication_date, year, source)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (description, publication_date, source) DO UPDATE SET
                        link = EXCLUDED.link,
                        year = EXCLUDED.year,
                        updated_at = CURRENT_TIMESTAMP
                """, (desc, link, pub_date, year_int, source))
            conn.commit()
        print(f"Saved exam catalog entry in Amazon RDS: {desc} ({source})")
    except Exception as e:
        print(f"Error saving exam batch to RDS: {e}")
