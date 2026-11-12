"""SQLite persistence."""

import sqlite3
from contextlib import contextmanager
from typing import Iterator

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name TEXT NOT NULL,
    email TEXT,
    role TEXT NOT NULL CHECK (role IN ('admin', 'analyst', 'viewer')),
    password_hash TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 1,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    last_seen TEXT NOT NULL
);

-- One row per scanned message (per recipient mailbox)
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,                 -- graph | gateway | api | report
    mailbox TEXT,                         -- recipient mailbox (Graph user / SMTP rcpt)
    graph_id TEXT,                        -- Graph message id in that mailbox
    internet_message_id TEXT,
    subject TEXT,
    from_address TEXT,
    from_display TEXT,
    received_at TEXT,
    scanned_at TEXT NOT NULL,
    score INTEGER NOT NULL,
    verdict TEXT NOT NULL,                -- clean | suspicious | malicious
    category TEXT NOT NULL,               -- clean | phishing | bec | malware | spam | scam
    summary TEXT,
    signals TEXT NOT NULL,                -- JSON list of findings
    indicators TEXT NOT NULL,             -- JSON: domains, urls, hashes, sender (for retro-hunts)
    llm TEXT,                             -- JSON AI assessment
    status TEXT NOT NULL,                 -- delivered | warned | quarantined | released | purged | deleted
    raw_path TEXT                         -- stored copy (quarantined or reported messages)
);
CREATE INDEX IF NOT EXISTS ix_messages_imid ON messages(internet_message_id);
CREATE INDEX IF NOT EXISTS ix_messages_status ON messages(status, verdict);

CREATE TABLE IF NOT EXISTS links (
    token TEXT PRIMARY KEY,
    message_id INTEGER REFERENCES messages(id),
    recipient TEXT,
    url TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clicks (
    id INTEGER PRIMARY KEY,
    token TEXT,
    url TEXT NOT NULL,
    final_url TEXT,
    recipient TEXT,
    clicked_at TEXT NOT NULL,
    verdict TEXT NOT NULL,                -- allowed | warned | blocked
    score INTEGER NOT NULL,
    reasons TEXT NOT NULL,
    client_ip TEXT
);

CREATE TABLE IF NOT EXISTS url_cache (
    url TEXT PRIMARY KEY,
    checked_at TEXT NOT NULL,
    result TEXT NOT NULL
);

-- Threat intelligence: confirmed bad (block) or known good (allow)
CREATE TABLE IF NOT EXISTS indicators (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('domain', 'url', 'sender', 'hash', 'ip')),
    value TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('block', 'allow')),
    source TEXT NOT NULL,
    note TEXT,
    added_by TEXT NOT NULL,
    added_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE (kind, value, action)
);

-- Messages users reported with the "Report phishing" button
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY,
    message_id INTEGER REFERENCES messages(id),
    reporter TEXT NOT NULL,
    channel TEXT NOT NULL,                -- addin | banner | forward | portal
    comment TEXT,
    reported_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('new', 'confirmed', 'false_positive')),
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_note TEXT
);

CREATE TABLE IF NOT EXISTS sandbox_jobs (
    id INTEGER PRIMARY KEY,
    message_id INTEGER REFERENCES messages(id),
    sha256 TEXT NOT NULL,
    filename TEXT,
    task_id TEXT,
    status TEXT NOT NULL,                 -- pending | done | failed
    malscore REAL,
    report TEXT,
    submitted_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS graph_state (
    mailbox TEXT PRIMARY KEY,
    delta_link TEXT,
    quarantine_folder_id TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()
