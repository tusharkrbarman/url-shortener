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

