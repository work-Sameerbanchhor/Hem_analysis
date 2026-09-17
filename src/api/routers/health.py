from fastapi import APIRouter
from src.core.config import UNIFIED_CONFIGS, CRAWL_STATUS
from src.core.database import get_db_conn

router = APIRouter(tags=["Health"])

@router.get("/")
async def root_info():
    return {
        "service": "Hemchand Yadav Vishwavidyalaya Live Result API",
        "version": "2.0.0",
        "status": "operational",
        "catalog_size": len(UNIFIED_CONFIGS),
        "endpoints": [
            "/api/results",
            "/api/crawl/update",
            "/api/crawl/status",
            "/api/log",
            "/health"
        ]
    }

@router.get("/health")
async def health_check():
    db_ok = False
    results_count = 0
    catalog_count = 0
    
    try:
        with get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM results")
                results_count = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM exam_catalog")
                catalog_count = cur.fetchone()[0]
                db_ok = True
    except Exception as e:
        db_error = str(e)
        return {
            "status": "unhealthy",
            "database": "error",
            "error": db_error
        }
        
    return {
        "status": "healthy",
        "database": "connected",
        "stored_student_results": results_count,
        "catalog_exams": catalog_count,
        "crawler_status": CRAWL_STATUS["status"]
    }
