"""
SQLite persistence for Secret Server.

Each API request runs inside one `BEGIN IMMEDIATE` transaction, so a change and
its audit record commit (or roll back) together and the audit hash chain is
appended serially.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

VIEW, EDIT, OWNER = 1, 2, 3
PERMISSION_NAMES = {"view": VIEW, "edit": EDIT, "owner": OWNER}
PERMISSION_LABELS = {v: k for k, v in PERMISSION_NAMES.items()}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def now_iso(delta: Optional[timedelta] = None) -> str:
    return iso(utcnow() + (delta or timedelta()))


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name    TEXT,
    email           TEXT,
    password_hash   TEXT,
    auth_source     TEXT NOT NULL DEFAULT 'local',
    external_id     TEXT,
    role            TEXT NOT NULL DEFAULT 'user',
    enabled         INTEGER NOT NULL DEFAULT 1,
    mfa_secret_enc  TEXT,
    mfa_enabled     INTEGER NOT NULL DEFAULT 0,
    failed_logins   INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT,
    created_at      TEXT NOT NULL,
    last_login      TEXT,
    UNIQUE (auth_source, external_id)
);

CREATE TABLE IF NOT EXISTS groups (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT,
    source      TEXT NOT NULL DEFAULT 'local'
);

CREATE TABLE IF NOT EXISTS group_members (
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, user_id)
);

CREATE TABLE IF NOT EXISTS folders (
    id                  INTEGER PRIMARY KEY,
    name                TEXT NOT NULL,
    parent_id           INTEGER REFERENCES folders(id) ON DELETE RESTRICT,
    inherit_permissions INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS folder_permissions (
    folder_id      INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    principal_type TEXT NOT NULL CHECK (principal_type IN ('user', 'group')),
    principal_id   INTEGER NOT NULL,
    level          INTEGER NOT NULL,
    PRIMARY KEY (folder_id, principal_type, principal_id)
);

CREATE TABLE IF NOT EXISTS templates (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    fields_json     TEXT NOT NULL,
    password_policy TEXT,
    expiry_days     INTEGER
);

CREATE TABLE IF NOT EXISTS secrets (
    id                    INTEGER PRIMARY KEY,
    name                  TEXT NOT NULL,
    folder_id             INTEGER NOT NULL REFERENCES folders(id),
    template_id           INTEGER NOT NULL REFERENCES templates(id),
    dek_wrapped           TEXT NOT NULL,
    inherit_permissions   INTEGER NOT NULL DEFAULT 1,
    require_checkout      INTEGER NOT NULL DEFAULT 0,
    checkout_minutes      INTEGER NOT NULL DEFAULT 60,
    rotate_on_checkin     INTEGER NOT NULL DEFAULT 0,
    require_comment       INTEGER NOT NULL DEFAULT 0,
    require_approval      INTEGER NOT NULL DEFAULT 0,
    approver_group_id     INTEGER REFERENCES groups(id),
    password_changer      TEXT NOT NULL DEFAULT 'none',
    expiry_days           INTEGER,
    password_changed_at   TEXT,
    checked_out_by        INTEGER REFERENCES users(id),
    checked_out_until     TEXT,
    active                INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS secret_fields (
    secret_id INTEGER NOT NULL REFERENCES secrets(id) ON DELETE CASCADE,
    slug      TEXT NOT NULL,
    value_enc TEXT NOT NULL,
    PRIMARY KEY (secret_id, slug)
);

CREATE TABLE IF NOT EXISTS secret_field_history (
    id         INTEGER PRIMARY KEY,
    secret_id  INTEGER NOT NULL REFERENCES secrets(id) ON DELETE CASCADE,
    slug       TEXT NOT NULL,
    value_enc  TEXT NOT NULL,
    changed_by INTEGER,
    changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS secret_permissions (
    secret_id      INTEGER NOT NULL REFERENCES secrets(id) ON DELETE CASCADE,
    principal_type TEXT NOT NULL CHECK (principal_type IN ('user', 'group')),
    principal_id   INTEGER NOT NULL,
    level          INTEGER NOT NULL,
    PRIMARY KEY (secret_id, principal_type, principal_id)
);

CREATE TABLE IF NOT EXISTS access_requests (
    id           INTEGER PRIMARY KEY,
    secret_id    INTEGER NOT NULL REFERENCES secrets(id) ON DELETE CASCADE,
    requester_id INTEGER NOT NULL REFERENCES users(id),
    reason       TEXT NOT NULL,
    minutes      INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    reviewer_id  INTEGER REFERENCES users(id),
    review_note  TEXT,
    reviewed_at  TEXT,
    expires_at   TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    jti         TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    auth_method TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sso_providers (
    id                INTEGER PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE,
    type              TEXT NOT NULL CHECK (type IN ('oidc', 'saml')),
    config_json       TEXT NOT NULL,
    client_secret_enc TEXT,
    enabled           INTEGER NOT NULL DEFAULT 1,
    jit_provisioning  INTEGER NOT NULL DEFAULT 1,
    default_role      TEXT NOT NULL DEFAULT 'user'
);

-- One-time values: SSO login state, SSO->API code exchange, consumed SAML assertion IDs
CREATE TABLE IF NOT EXISTS one_time_values (
    key        TEXT PRIMARY KEY,
    purpose    TEXT NOT NULL,
    data_json  TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    user_id     INTEGER,
    username    TEXT,
    action      TEXT NOT NULL,
    target_type TEXT,
    target_id   TEXT,
    details     TEXT,
    ip          TEXT,
    key_id      TEXT NOT NULL,
    prev_hash   TEXT NOT NULL,
    hash        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_secrets_folder ON secrets(folder_id);
CREATE INDEX IF NOT EXISTS ix_audit_target ON audit_log(target_type, target_id);
CREATE INDEX IF NOT EXISTS ix_access_requests_secret ON access_requests(secret_id, requester_id);
"""

BUILTIN_TEMPLATES = [
    {
        "name": "Active Directory Account",
        "fields": [
            {"slug": "domain", "name": "Domain", "type": "text", "required": True},
            {"slug": "username", "name": "Username", "type": "text", "required": True},
            {"slug": "password", "name": "Password", "type": "password", "required": True},
            {"slug": "notes", "name": "Notes", "type": "notes"},
        ],
        "expiry_days": 90,
    },
    {
        "name": "Windows Account",
        "fields": [
            {"slug": "machine", "name": "Machine", "type": "text", "required": True},
            {"slug": "username", "name": "Username", "type": "text", "required": True},
            {"slug": "password", "name": "Password", "type": "password", "required": True},
            {"slug": "notes", "name": "Notes", "type": "notes"},
        ],
        "expiry_days": 90,
    },
    {
        "name": "Unix Account (SSH)",
        "fields": [
            {"slug": "machine", "name": "Machine", "type": "text", "required": True},
            {"slug": "username", "name": "Username", "type": "text", "required": True},
            {"slug": "password", "name": "Password", "type": "password"},
            {"slug": "private-key", "name": "Private Key", "type": "password"},
            {"slug": "notes", "name": "Notes", "type": "notes"},
        ],
        "expiry_days": 90,
    },
    {
        "name": "Web Password",
        "fields": [
            {"slug": "url", "name": "URL", "type": "url"},
            {"slug": "username", "name": "Username", "type": "text", "required": True},
            {"slug": "password", "name": "Password", "type": "password", "required": True},
            {"slug": "notes", "name": "Notes", "type": "notes"},
        ],
        "expiry_days": None,
    },
    {
        "name": "Database Account",
        "fields": [
            {"slug": "server", "name": "Server", "type": "text", "required": True},
            {"slug": "database", "name": "Database", "type": "text"},
            {"slug": "username", "name": "Username", "type": "text", "required": True},
            {"slug": "password", "name": "Password", "type": "password", "required": True},
            {"slug": "notes", "name": "Notes", "type": "notes"},
        ],
        "expiry_days": 90,
    },
]


class Database:
    def __init__(self, path: str):
        self.path = path
        with self._raw() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            self._seed(conn)

    @contextmanager
    def _raw(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._raw() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            else:
                if conn.in_transaction:
                    conn.execute("COMMIT")

    @staticmethod
    def _seed(conn: sqlite3.Connection) -> None:
        for tpl in BUILTIN_TEMPLATES:
            conn.execute(
                "INSERT OR IGNORE INTO templates (name, fields_json, password_policy, expiry_days) "
                "VALUES (?, ?, NULL, ?)",
                (tpl["name"], json.dumps(tpl["fields"]), tpl["expiry_days"]),
            )


def commit_now(conn: sqlite3.Connection) -> None:
    """Persist work done so far (e.g. a failed-login counter) before raising an error."""
    if conn.in_transaction:
        conn.execute("COMMIT")
    conn.execute("BEGIN IMMEDIATE")


def put_one_time(conn, key: str, purpose: str, data: dict, ttl: timedelta) -> None:
    conn.execute("DELETE FROM one_time_values WHERE expires_at < ?", (now_iso(),))
    conn.execute(
        "INSERT INTO one_time_values (key, purpose, data_json, expires_at) VALUES (?, ?, ?, ?)",
        (key, purpose, json.dumps(data), now_iso(ttl)),
    )


def take_one_time(conn, key: str, purpose: str) -> Optional[dict]:
    """Fetch and delete a one-time value; returns None if missing or expired."""
    row = conn.execute(
        "SELECT data_json, expires_at FROM one_time_values WHERE key = ? AND purpose = ?",
        (key, purpose),
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM one_time_values WHERE key = ?", (key,))
    if row["expires_at"] < now_iso():
        return None
    return json.loads(row["data_json"])
