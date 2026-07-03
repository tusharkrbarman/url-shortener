import os
import sqlite3
from contextlib import contextmanager


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS links (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    short_code TEXT NOT NULL UNIQUE,
    destination_url TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'failed', 'disabled')),
    usage_count INTEGER NOT NULL DEFAULT 0,
    usage_limit INTEGER,
    expires_at TEXT,
    validation_error TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_links_owner_id ON links(owner_id);
CREATE INDEX IF NOT EXISTS idx_links_status ON links(status);
CREATE INDEX IF NOT EXISTS idx_links_expires_at ON links(expires_at);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_body TEXT,
    status_code INTEGER,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE(owner_id, key)
);

CREATE INDEX IF NOT EXISTS idx_idempotency_expires_at ON idempotency_keys(expires_at);

CREATE TABLE IF NOT EXISTS validation_jobs (
    id TEXT PRIMARY KEY,
    link_id TEXT NOT NULL REFERENCES links(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'succeeded', 'failed', 'retrying', 'dead')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_run_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_validation_jobs_due ON validation_jobs(status, next_run_at);

CREATE TABLE IF NOT EXISTS rate_limits (
    key TEXT PRIMARY KEY,
    window_start INTEGER NOT NULL,
    count INTEGER NOT NULL
);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    short_code TEXT NOT NULL UNIQUE,
    destination_url TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'failed', 'disabled')),
    usage_count INTEGER NOT NULL DEFAULT 0,
    usage_limit INTEGER,
    expires_at TEXT,
    validation_error TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_links_owner_id ON links(owner_id);
CREATE INDEX IF NOT EXISTS idx_links_status ON links(status);
CREATE INDEX IF NOT EXISTS idx_links_expires_at ON links(expires_at);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_body TEXT,
    status_code INTEGER,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE(owner_id, key)
);

CREATE INDEX IF NOT EXISTS idx_idempotency_expires_at ON idempotency_keys(expires_at);

CREATE TABLE IF NOT EXISTS validation_jobs (
    id TEXT PRIMARY KEY,
    link_id TEXT NOT NULL REFERENCES links(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'succeeded', 'failed', 'retrying', 'dead')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_run_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_validation_jobs_due ON validation_jobs(status, next_run_at);

CREATE TABLE IF NOT EXISTS rate_limits (
    key TEXT PRIMARY KEY,
    window_start INTEGER NOT NULL,
    count INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path

    def initialize(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def connect(self):
        conn = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @contextmanager
    def transaction(self, mode="IMMEDIATE"):
        conn = self.connect()
        try:
            conn.execute(f"BEGIN {mode}")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def ready(self) -> bool:
        try:
            with self.connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    def claim_validation_job(self, now: str):
        with self.transaction() as conn:
            job = conn.execute(
                """
                SELECT validation_jobs.*, links.destination_url
                FROM validation_jobs
                JOIN links ON links.id = validation_jobs.link_id
                WHERE validation_jobs.status IN ('queued', 'retrying')
                  AND validation_jobs.next_run_at <= ?
                ORDER BY validation_jobs.created_at
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if not job:
                return None
            conn.execute(
                "UPDATE validation_jobs SET status = 'processing', updated_at = ? WHERE id = ?",
                (now, job["id"]),
            )
            return job

    def is_unique_violation(self, exc: Exception) -> bool:
        return "UNIQUE constraint failed" in str(exc)


class PostgresCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, sql, params=()):
        self.cursor.execute(_pg_sql(sql), params)
        return self

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


class PostgresConnection:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=()):
        cursor = self.conn.cursor()
        cursor.execute(_pg_sql(sql), params)
        return PostgresCursor(cursor)

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def _pg_sql(sql: str) -> str:
    return sql.replace("?", "%s")


class PostgresDatabase:
    def __init__(self, url: str):
        self.url = url

    def _connect_raw(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("PostgreSQL backend requires psycopg. Install production dependencies.") from exc
        return psycopg.connect(self.url, row_factory=dict_row, autocommit=False)

    def initialize(self):
        with self._connect_raw() as conn:
            with conn.cursor() as cursor:
                for statement in _split_sql_statements(POSTGRES_SCHEMA):
                    cursor.execute(statement)
            conn.commit()

    def connect(self):
        return PostgresConnection(self._connect_raw())

    @contextmanager
    def transaction(self, mode="IMMEDIATE"):
        conn = self._connect_raw()
        try:
            yield PostgresConnection(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ready(self) -> bool:
        try:
            with self.connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    def claim_validation_job(self, now: str):
        with self.transaction() as conn:
            job = conn.execute(
                """
                SELECT validation_jobs.*, links.destination_url
                FROM validation_jobs
                JOIN links ON links.id = validation_jobs.link_id
                WHERE validation_jobs.id = (
                    SELECT validation_jobs.id
                    FROM validation_jobs
                    WHERE validation_jobs.status IN ('queued', 'retrying')
                      AND validation_jobs.next_run_at <= ?
                    ORDER BY validation_jobs.created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                """,
                (now,),
            ).fetchone()
            if not job:
                return None
            conn.execute(
                "UPDATE validation_jobs SET status = 'processing', updated_at = ? WHERE id = ?",
                (now, job["id"]),
            )
            return job

    def is_unique_violation(self, exc: Exception) -> bool:
        return getattr(exc, "sqlstate", None) == "23505" or "duplicate key" in str(exc).lower()


def create_database(config):
    if config.database_backend == "postgres":
        if not config.database_url:
            raise RuntimeError("DATABASE_URL is required when DATABASE_BACKEND=postgres.")
        return PostgresDatabase(config.database_url)
    return Database(config.database_path)


def _split_sql_statements(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";") if statement.strip()]
