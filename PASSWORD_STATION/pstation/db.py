"""SQLite storage."""

import sqlite3
import threading
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS domains (
    id TEXT PRIMARY KEY,               -- short key, e.g. 'gmp', 'business'
    name TEXT NOT NULL,                -- shown to users
    kind TEXT NOT NULL,                -- 'ad' or 'demo'
    settings TEXT NOT NULL,            -- JSON, non-secret
    secret_enc TEXT,                   -- encrypted bind password
    enabled INTEGER NOT NULL DEFAULT 1,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS enrollments (
    domain_id TEXT NOT NULL,
    username TEXT NOT NULL,            -- lower-case
    email_enc TEXT,
    phone_enc TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (domain_id, username)
);
CREATE TABLE IF NOT EXISTS flows (
    id TEXT PRIMARY KEY,               -- random, held in the browser session
    purpose TEXT NOT NULL,             -- enroll | forgot | unlock
    domain_id TEXT NOT NULL,
    username TEXT NOT NULL,
    channel TEXT,                      -- email | sms
    pending_enc TEXT,                  -- enrolment: contact being verified
    code_hash TEXT,
    code_expires TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    verified INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS throttle (key TEXT NOT NULL, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS throttle_key ON throttle(key, at);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    ip TEXT,
    domain_id TEXT,
    username TEXT,
    action TEXT NOT NULL,
    result TEXT NOT NULL,              -- ok | denied | error
    detail TEXT,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL                 -- sha256 chain: tampering with any row breaks it
);
CREATE TABLE IF NOT EXISTS admins (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    failed INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT
);
CREATE TABLE IF NOT EXISTS outbox (     -- demo mode only
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL, channel TEXT NOT NULL, recipient TEXT NOT NULL, subject TEXT, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS demo_users (
    domain_id TEXT NOT NULL, username TEXT NOT NULL, display_name TEXT, email TEXT,
    password_hash TEXT NOT NULL, history TEXT NOT NULL DEFAULT '[]',
    failed INTEGER NOT NULL DEFAULT 0, locked INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0, protected INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (domain_id, username)
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def read(self):
        return self._conn
