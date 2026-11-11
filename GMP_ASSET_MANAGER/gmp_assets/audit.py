"""Hash-chained, append-only audit trail (21 CFR 11.10(e), Annex 11 §9).

Every entry records who, what, when and why, together with the old and new
values. Each entry's SHA-256 hash covers its content plus the previous
entry's hash, so any edit, deletion or re-ordering of history - even one made
directly in the database file - is detected by :func:`verify_chain`.
"""

import hashlib
import json
import sqlite3
from typing import Any, Optional

from . import clock

GENESIS_HASH = "0" * 64


def canonical_json(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _entry_hash(timestamp, username, action, entity_type, entity_id, old_value, new_value, reason, prev_hash) -> str:
    payload = canonical_json([timestamp, username, action, entity_type, entity_id, old_value, new_value, reason, prev_hash])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record(
    conn: sqlite3.Connection,
    *,
    username: str,
    action: str,
    entity_type: str,
    entity_id: Any = None,
    old: Any = None,
    new: Any = None,
    reason: Optional[str] = None,
) -> str:
    """Append an audit entry inside the caller's transaction; returns its hash."""
    row = conn.execute("SELECT hash FROM audit_trail ORDER BY seq DESC LIMIT 1").fetchone()
    prev_hash = row["hash"] if row else GENESIS_HASH
    timestamp = clock.now_iso()
    entity_id = None if entity_id is None else str(entity_id)
    old_value, new_value = canonical_json(old), canonical_json(new)
    entry_hash = _entry_hash(timestamp, username, action, entity_type, entity_id, old_value, new_value, reason, prev_hash)
    conn.execute(
        "INSERT INTO audit_trail (timestamp, username, action, entity_type, entity_id, old_value, new_value, reason, prev_hash, hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (timestamp, username, action, entity_type, entity_id, old_value, new_value, reason, prev_hash, entry_hash),
    )
    return entry_hash


def verify_chain(conn: sqlite3.Connection) -> dict:
    prev_hash = GENESIS_HASH
    expected_seq = None
    count = 0
    for row in conn.execute("SELECT * FROM audit_trail ORDER BY seq"):
        count += 1
        if expected_seq is not None and row["seq"] != expected_seq:
            return {"valid": False, "entries_checked": count, "broken_at_seq": row["seq"], "problem": "gap in sequence (entry removed)"}
        if row["prev_hash"] != prev_hash:
            return {"valid": False, "entries_checked": count, "broken_at_seq": row["seq"], "problem": "chain link mismatch"}
        recomputed = _entry_hash(
            row["timestamp"], row["username"], row["action"], row["entity_type"], row["entity_id"],
            row["old_value"], row["new_value"], row["reason"], row["prev_hash"],
        )
        if recomputed != row["hash"]:
            return {"valid": False, "entries_checked": count, "broken_at_seq": row["seq"], "problem": "entry content altered"}
        prev_hash = row["hash"]
        expected_seq = row["seq"] + 1
    return {"valid": True, "entries_checked": count, "broken_at_seq": None, "problem": None, "head_hash": prev_hash}
