import time
from fastapi import APIRouter, BackgroundTasks
from src.core.config import CRAWL_STATUS
from src.services.crawler import run_auto_crawler

router = APIRouter(tags=["Crawler"])

@router.post("/api/crawl/update")
async def trigger_crawl(background_tasks: BackgroundTasks):
    global CRAWL_STATUS
    if CRAWL_STATUS["status"] == "running":
        return {"status": "already running", "progress": CRAWL_STATUS}
        
    CRAWL_STATUS["status"] = "running"
    CRAWL_STATUS["current_exam"] = "Initializing..."
    CRAWL_STATUS["colleges_progress"] = "0/0"
    CRAWL_STATUS["records_added"] = 0
    CRAWL_STATUS["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    CRAWL_STATUS["elapsed_seconds"] = 0
    
    background_tasks.add_task(run_auto_crawler)
    return {"status": "started", "progress": CRAWL_STATUS}

@router.get("/api/crawl/status")
async def get_crawl_status():
    return CRAWL_STATUS
