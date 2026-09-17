"""
HYU Live Result Portal - Application Entrypoint.

This file serves as the production entrypoint for Uvicorn / Docker runners (e.g. `uvicorn app:app`).
The codebase has been refactored into a modular, production-grade architecture located in `src/`.
"""

from src.main import app, create_app

# Backward-compatible convenience imports
from src.core.config import (
    BASE_DIR,
    RDS_HOST,
    RDS_PORT,
    RDS_DATABASE,
    RDS_USER,
    COURSE_MAP,
    UNIFIED_CONFIGS,
    CRAWL_STATUS,
)
from src.core.database import get_db_pool, get_db_conn, init_db, check_rds_connection
from src.services.results import get_local_results_from_db, save_result_to_db
from src.services.catalog import sync_indices_to_rds, load_unified_configs, save_exam_batch_to_rds
from src.services.roll_parser import sanitize_durg_roll_number, parse_roll, classify_roll
from src.services.html_parser import parse_hyu_html, normalize_title
from src.services.scraper import scrape_exam_async, fetch_latest_batches
from src.services.crawler import run_auto_crawler

__all__ = [
    "app",
    "create_app",
    "get_db_pool",
    "get_db_conn",
    "init_db",
    "check_rds_connection",
    "get_local_results_from_db",
    "save_result_to_db",
    "sync_indices_to_rds",
    "load_unified_configs",
    "save_exam_batch_to_rds",
    "sanitize_durg_roll_number",
    "parse_roll",
    "classify_roll",
    "parse_hyu_html",
    "normalize_title",
    "scrape_exam_async",
    "fetch_latest_batches",
    "run_auto_crawler",
    "COURSE_MAP",
    "UNIFIED_CONFIGS",
    "CRAWL_STATUS",
]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=7860, reload=True)