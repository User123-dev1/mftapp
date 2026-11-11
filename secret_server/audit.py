"""
Tamper-evident audit trail.

Each record carries HMAC(prev_hash || record) keyed by a subkey of the master
key, so editing, deleting or reordering rows breaks the chain and cannot be
repaired without the master key. The key id is stored per row so the chain
still verifies after a master key rotation (keep retired keys in the key ring).
Secret values are never written here.
"""

import hashlib
import hmac
import json
from typing import Optional

from .db import now_iso

GENESIS = "0" * 64
_FIELDS = ("ts", "user_id", "username", "action", "target_type", "target_id",
           "details", "ip", "key_id")


def _digest(key: bytes, prev_hash: str, record: dict) -> str:
    payload = prev_hash + json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


class AuditLog:
    def __init__(self, keyring):
        self._keyring = keyring

    def record(
        self,
        conn,
        action: str,
        principal=None,
        target_type: Optional[str] = None,
        target_id=None,
        details: Optional[dict] = None,
        ip: Optional[str] = None,
        username: Optional[str] = None,
    ) -> None:
        last = conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        prev_hash = last["hash"] if last else GENESIS
        record = {
            "ts": now_iso(),
            "user_id": principal.user_id if principal else None,
            "username": principal.username if principal else username,
            "action": action,
            "target_type": target_type,
            "target_id": str(target_id) if target_id is not None else None,
            "details": json.dumps(details or {}, sort_keys=True),
            "ip": ip if ip is not None else (principal.ip if principal else None),
            "key_id": self._keyring.current_id,
        }
        digest = _digest(self._keyring.derive("audit"), prev_hash, record)
        conn.execute(
            f"INSERT INTO audit_log ({', '.join(_FIELDS)}, prev_hash, hash) "
            f"VALUES ({', '.join('?' * (len(_FIELDS) + 2))})",
            (*(record[f] for f in _FIELDS), prev_hash, digest),
        )

    def verify(self, conn) -> dict:
        prev_hash = GENESIS
        count = 0
        keys = {}
        for row in conn.execute("SELECT * FROM audit_log ORDER BY id"):
            record = {f: row[f] for f in _FIELDS}
            kid = row["key_id"]
            if kid not in keys:
                keys[kid] = self._keyring.derive("audit", kid)
            if row["prev_hash"] != prev_hash or not hmac.compare_digest(
                    row["hash"], _digest(keys[kid], prev_hash, record)):
                return {"valid": False, "records_checked": count, "first_bad_id": row["id"]}
            prev_hash = row["hash"]
            count += 1
        return {"valid": True, "records_checked": count, "first_bad_id": None}
