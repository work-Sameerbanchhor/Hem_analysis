import os
import re
import threading
import asyncio
import collections
from contextlib import contextmanager
import libsql_client
from libsql_client.sync import _AsyncExecutor

from src.core.config import (
    TURSO_DATABASE_URL,
    TURSO_AUTH_TOKEN,
)

# Patch _AsyncExecutor to use daemon=True so scripts & threads can exit cleanly
_orig_async_init = _AsyncExecutor.__init__
def _daemon_async_init(self):
    self._thread = threading.Thread(target=self._run, name="libsql_client", daemon=True)
    self._loop = asyncio.new_event_loop()
    self._lock = threading.Lock()
    self._closed = False
    self._queue = collections.deque()
    self._waker = None
    self._thread.start()

_AsyncExecutor.__init__ = _daemon_async_init

_turso_client = None

def get_turso_client() -> libsql_client.ClientSync:
    """
    Returns a thread-safe synchronized libSQL client for Turso / SQLite.
    Automatically connects to Turso Cloud (libsql://...) or local file (file:...).
    """
    global _turso_client
    if _turso_client is None:
        url = TURSO_DATABASE_URL
        auth_token = TURSO_AUTH_TOKEN if (url.startswith("libsql://") or url.startswith("https://")) and TURSO_AUTH_TOKEN else None
        
        # If url is a relative or absolute file path, ensure directory exists
        if url.startswith("file:"):
            db_path = url[5:]
            db_dir = os.path.dirname(db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
                
        _turso_client = libsql_client.create_client_sync(url, auth_token=auth_token)
    return _turso_client

def close_turso_client():
    """Closes the active Turso client connection cleanly."""
    global _turso_client
    if _turso_client is not None:
        try:
            _turso_client.close()
        except Exception:
            pass
        _turso_client = None


class TursoCursor:
    """Cursor wrapper that translates PostgreSQL-style queries and parameters to libSQL / SQLite."""
    def __init__(self, client: libsql_client.ClientSync):
        self.client = client
        self.last_result = None
        self._row_idx = 0

    def execute(self, sql: str, params=None):
        # Convert PostgreSQL parameter placeholders (%s) to SQLite (?)
        clean_sql = re.sub(r'(?<!%)(%s)', '?', sql)
        args = list(params) if params is not None else []
        self.last_result = self.client.execute(clean_sql, args)
        self._row_idx = 0
        return self

    def batch(self, statements_and_args):
        cleaned_stmts = []
        for item in statements_and_args:
            if isinstance(item, tuple) or isinstance(item, list):
                s = re.sub(r'(?<!%)(%s)', '?', item[0])
                args = list(item[1]) if len(item) > 1 and item[1] is not None else []
                cleaned_stmts.append((s, args))
            else:
                s = re.sub(r'(?<!%)(%s)', '?', str(item))
                cleaned_stmts.append((s, []))
        return self.client.batch(cleaned_stmts)

    def fetchall(self):
        if not self.last_result:
            return []
        return self.last_result.rows

    def fetchone(self):
        if not self.last_result or self._row_idx >= len(self.last_result.rows):
            return None
        row = self.last_result.rows[self._row_idx]
        self._row_idx += 1
        return row

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class TursoConnection:
    """Connection wrapper providing DB-API style interface over libSQL ClientSync."""
    def __init__(self, client: libsql_client.ClientSync):
        self.client = client

    def cursor(self):
        return TursoCursor(self.client)

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@contextmanager
def get_db_conn():
    """Context manager yielding a database connection."""
    client = get_turso_client()
    conn = TursoConnection(client)
    try:
        yield conn
    finally:
        pass


def init_db():
    """Initializes and verifies the Turso / SQLite schema with gzip BLOB storage."""
    try:
        with get_db_conn() as conn:
            with conn.cursor() as cursor:
                # 1. Results Table (BLOB for gzip-compressed HTML)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS results (
                        enrollment_no TEXT NOT NULL,
                        roll_number INTEGER NOT NULL,
                        name TEXT,
                        exam_title TEXT NOT NULL,
                        result_status TEXT,
                        sgpa TEXT,
                        html_result BLOB,
                        official_url TEXT,
                        updated_at TEXT,
                        PRIMARY KEY (enrollment_no, roll_number, exam_title)
                    );
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_results_enrollment ON results(enrollment_no);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_results_roll ON results(roll_number);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_results_name ON results(name COLLATE NOCASE);")

                # 2. Exam Catalog Table
                cursor.execute("""
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
                        CONSTRAINT uq_exam_desc_date_source UNIQUE (description, publication_date, source)
                    );
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_exam_catalog_year ON exam_catalog(year);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_exam_catalog_source ON exam_catalog(source);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_exam_catalog_desc ON exam_catalog(description COLLATE NOCASE);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_exam_catalog_crawled ON exam_catalog(is_crawled);")

            print("Verified Turso (libSQL) database schemas (results with gzip BLOB & exam_catalog).")
    except Exception as e:
        print(f"Error initializing Turso database schema: {e}")


def check_rds_connection():
    """Checks database connection and reports table row counts (backward-compatible name)."""
    print("Checking connection to Turso (libSQL) database...")
    try:
        with get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM results")
                r_cnt = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM exam_catalog")
                e_cnt = cur.fetchone()[0]
                print(f"Connected to Turso database successfully: {r_cnt:,} student results | {e_cnt:,} catalog exams.")
    except Exception as e:
        print(f"Warning: Turso database connection check failed: {e}")

# Modern aliases
check_turso_connection = check_rds_connection
get_db_pool = get_turso_client
