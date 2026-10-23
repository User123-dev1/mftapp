"""SQLite persistence.

Data-integrity controls implemented at the database level:

* The audit trail is append-only: triggers abort any UPDATE or DELETE.
* GMP records (assets, calibrations, maintenance, signatures, deviations)
  cannot be deleted - assets are retired, records are superseded.
* Writes run in ``BEGIN IMMEDIATE`` transactions so the hash-chained audit
  trail is extended by one writer at a time and every change commits
  atomically together with its audit entry.
"""

import sqlite3
from contextlib import contextmanager
from typing import Iterator

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    password_changed_at TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 1,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS password_history (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    area_class TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY,
    asset_tag TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    gmp_impact TEXT NOT NULL CHECK (gmp_impact IN ('direct', 'indirect', 'none')),
    manufacturer TEXT,
    model TEXT,
    serial_number TEXT,
    gtin TEXT,
    department TEXT,
    location_id INTEGER REFERENCES locations(id),
    status TEXT NOT NULL,
    qualification_status TEXT NOT NULL,
    is_reference_standard INTEGER NOT NULL DEFAULT 0,
    calibration_required INTEGER NOT NULL DEFAULT 0,
    calibration_interval_days INTEGER,
    last_calibrated_on TEXT,
    calibration_due_on TEXT,
    pm_required INTEGER NOT NULL DEFAULT 0,
    pm_interval_days INTEGER,
    last_pm_on TEXT,
    pm_due_on TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assets_gtin_serial ON assets(gtin, serial_number);

CREATE TABLE IF NOT EXISTS calibration_records (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    performed_on TEXT NOT NULL,
    performed_by TEXT NOT NULL,
    as_found TEXT NOT NULL CHECK (as_found IN ('pass', 'fail', 'not_applicable')),
    as_left TEXT NOT NULL CHECK (as_left IN ('pass', 'fail')),
    standards_used TEXT NOT NULL,
    certificate_ref TEXT,
    notes TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending_approval', 'approved', 'rejected')),
    recorded_at TEXT NOT NULL,
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_comment TEXT,
    deviation_id INTEGER REFERENCES deviations(id)
);

CREATE TABLE IF NOT EXISTS maintenance_records (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    maintenance_type TEXT NOT NULL CHECK (maintenance_type IN ('preventive', 'corrective')),
    performed_on TEXT NOT NULL,
    performed_by TEXT NOT NULL,
    description TEXT NOT NULL,
    requalification_required INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deviations (
    id INTEGER PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    source TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    opened_at TEXT NOT NULL,
    opened_by TEXT NOT NULL,
    closed_at TEXT,
    closed_by TEXT,
    closure_comment TEXT
);

CREATE TABLE IF NOT EXISTS signatures (
    id INTEGER PRIMARY KEY,
    record_type TEXT NOT NULL,
    record_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    full_name TEXT NOT NULL,
    meaning TEXT NOT NULL,
    signed_at TEXT NOT NULL,
    record_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_signatures_record ON signatures(record_type, record_id);

CREATE TABLE IF NOT EXISTS scan_events (
    id INTEGER PRIMARY KEY,
    scanned_at TEXT NOT NULL,
    username TEXT NOT NULL,
    raw TEXT NOT NULL,
    match_type TEXT NOT NULL,
    entity_id INTEGER
);

CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS company (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL,
    site_name TEXT NOT NULL,
    address TEXT,
    asset_prefix TEXT NOT NULL,
    next_asset_number INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Maintenance plans: time-based (every N days/weeks/months) or event-triggered
CREATE TABLE IF NOT EXISTS pm_schedules (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    title TEXT NOT NULL,
    instructions TEXT,
    work_type TEXT NOT NULL CHECK (work_type IN ('preventive', 'inspection', 'cleaning', 'corrective')),
    trigger_type TEXT NOT NULL CHECK (trigger_type IN ('time', 'event')),
    interval_value INTEGER NOT NULL,
    interval_unit TEXT NOT NULL CHECK (interval_unit IN ('days', 'weeks', 'months')),
    event TEXT,
    lead_days INTEGER NOT NULL DEFAULT 0,
    blocks_use INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1,
    next_due_on TEXT,
    last_completed_on TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_orders (
    id INTEGER PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    schedule_id INTEGER REFERENCES pm_schedules(id),
    work_type TEXT NOT NULL,
    title TEXT NOT NULL,
    instructions TEXT,
    trigger TEXT NOT NULL,
    due_on TEXT NOT NULL,
    blocks_use INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('open', 'completed', 'cancelled')),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    closed_at TEXT,
    closed_by TEXT,
    closure_notes TEXT,
    maintenance_record_id INTEGER REFERENCES maintenance_records(id)
);
CREATE INDEX IF NOT EXISTS ix_work_orders_asset ON work_orders(asset_id, status);

-- QA approval requests: asset induction (release to service) and work order sign-off
CREATE TABLE IF NOT EXISTS approval_requests (
    id INTEGER PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    request_type TEXT NOT NULL CHECK (request_type IN ('asset_induction', 'work_order')),
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    work_order_id INTEGER REFERENCES work_orders(id),
    requested_by INTEGER NOT NULL REFERENCES users(id),
    requested_at TEXT NOT NULL,
    request_comment TEXT,
    approver_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
    decided_by INTEGER REFERENCES users(id),
    decided_at TEXT,
    decision_comment TEXT
);
CREATE INDEX IF NOT EXISTS ix_approvals_approver ON approval_requests(approver_id, status);

-- E-mail outbox: every notification is kept, sent or not
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    email TEXT,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS audit_trail (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    username TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    old_value TEXT,
    new_value TEXT,
    reason TEXT,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS audit_trail_no_update BEFORE UPDATE ON audit_trail
BEGIN SELECT RAISE(ABORT, 'audit trail is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_trail_no_delete BEFORE DELETE ON audit_trail
BEGIN SELECT RAISE(ABORT, 'audit trail is append-only'); END;
"""

_NO_DELETE_TABLES = ("assets", "calibration_records", "maintenance_records", "deviations", "signatures",
                     "pm_schedules", "work_orders", "approval_requests")

# Columns added after the first release; applied to existing databases on start-up
_ADDED_COLUMNS = (
    ("users", "email", "TEXT"),
    ("pm_schedules", "default_assignee", "INTEGER REFERENCES users(id)"),
    ("pm_schedules", "requires_approval", "INTEGER NOT NULL DEFAULT 0"),
    ("work_orders", "assigned_to", "INTEGER REFERENCES users(id)"),
    ("work_orders", "requires_approval", "INTEGER NOT NULL DEFAULT 0"),
    ("work_orders", "approval_state", "TEXT"),
)


class Database:
    def __init__(self, path: str):
        self.path = path
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            for table, column, ddl in _ADDED_COLUMNS:
                if column not in [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            for table in _NO_DELETE_TABLES:
                conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, 'GMP records cannot be deleted'); END;"
                )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
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
