from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.core.database import init_db, check_rds_connection
from src.services.catalog import load_unified_configs
from src.api.routers import results, crawler, logs, health

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup tasks
    init_db()
    check_rds_connection()
    load_unified_configs()
    yield
    # Teardown tasks (if any)

def create_app() -> FastAPI:
    application = FastAPI(
        title="HYU Live Result API",
        version="2.0.0",
        description="Production-grade distributed API for Hemchand Yadav Vishwavidyalaya results and auto-crawler backed by Amazon RDS PostgreSQL.",
        lifespan=lifespan
    )

    # Enable CORS for frontend accessibility
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routers
    application.include_router(health.router)
    application.include_router(results.router)
    application.include_router(crawler.router)
    application.include_router(logs.router)

    return application

app = create_app()
