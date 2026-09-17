import os
from contextlib import contextmanager
import psycopg2
from psycopg2 import pool
from src.core.config import (
    RDS_HOST,
    RDS_PORT,
    RDS_DATABASE,
    RDS_USER,
    RDS_PASSWORD,
    CERT_PATH,
)

pg_pool = None

def get_db_pool():
    global pg_pool
    if pg_pool is None or pg_pool.closed:
        ssl_args = {"sslmode": "require"}
        if os.path.exists(CERT_PATH):
            ssl_args = {"sslmode": "verify-full", "sslrootcert": CERT_PATH}
        pg_pool = pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=15,
            host=RDS_HOST,
            port=RDS_PORT,
            database=RDS_DATABASE,
            user=RDS_USER,
            password=RDS_PASSWORD,
            **ssl_args
        )
    return pg_pool

@contextmanager
def get_db_conn():
    p = get_db_pool()
    conn = p.getconn()
    try:
        if conn.closed:
            p.putconn(conn, close=True)
            conn = p.getconn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception:
        try:
            p.putconn(conn, close=True)
        except Exception:
            pass
        conn = p.getconn()
    try:
        yield conn
    except Exception:
        if conn and not conn.closed:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        if conn and not conn.closed:
            try:
                conn.rollback()
            except Exception:
                pass
            p.putconn(conn)

def init_db():
    try:
        with get_db_conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS results (
                        enrollment_no TEXT,
                        roll_number BIGINT,
                        name TEXT,
                        exam_title TEXT,
                        result_status TEXT,
                        sgpa TEXT,
                        html_result TEXT,
                        official_url TEXT,
                        updated_at TEXT,
                        PRIMARY KEY (enrollment_no, roll_number, exam_title)
                    );
                    CREATE INDEX IF NOT EXISTS idx_results_enrollment ON results(enrollment_no);
                    CREATE INDEX IF NOT EXISTS idx_results_roll ON results(roll_number);
                    CREATE INDEX IF NOT EXISTS idx_results_name ON results(LOWER(name));

                    CREATE TABLE IF NOT EXISTS exam_catalog (
                        id SERIAL PRIMARY KEY,
                        description TEXT NOT NULL,
                        link TEXT NOT NULL,
                        publication_date TEXT DEFAULT '',
                        year INT,
                        source TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT uq_exam_desc_date_source UNIQUE (description, publication_date, source)
                    );
                    CREATE INDEX IF NOT EXISTS idx_exam_catalog_year ON exam_catalog(year);
                    CREATE INDEX IF NOT EXISTS idx_exam_catalog_source ON exam_catalog(source);
                    CREATE INDEX IF NOT EXISTS idx_exam_catalog_desc ON exam_catalog(LOWER(description));
                """)
            conn.commit()
            print("Verified Amazon RDS PostgreSQL database schemas (results & exam_catalog).")
    except Exception as e:
        print(f"Error initializing Amazon RDS database schema: {e}")

def check_rds_connection():
    print("Checking connection to Amazon RDS PostgreSQL...")
    try:
        with get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM results")
                r_cnt = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM exam_catalog")
                e_cnt = cur.fetchone()[0]
                print(f"Connected to Amazon RDS PostgreSQL successfully: {r_cnt:,} student results | {e_cnt:,} catalog exams.")
    except Exception as e:
        print(f"Warning: Amazon RDS database connection check failed: {e}")
