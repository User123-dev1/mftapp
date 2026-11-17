"""Encryption at rest, password/code hashing and the tamper-evident audit log."""

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ITERATIONS = 310_000


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


class Box:
    """AES-256-GCM for small secrets stored in the database (recovery contacts, bind passwords)."""

    def __init__(self, key_b64: str):
        key = base64.b64decode(key_b64) if key_b64 else b""
        if len(key) != 32:
            raise ValueError("PS_DATA_KEY must be base64 of exactly 32 random bytes")
        self._aes = AESGCM(key)

    def seal(self, plaintext: Optional[str], context: str) -> Optional[str]:
        if plaintext is None:
            return None
        nonce = os.urandom(12)
        ct = self._aes.encrypt(nonce, plaintext.encode(), context.encode())
        return base64.b64encode(nonce + ct).decode()

    def open(self, sealed: Optional[str], context: str) -> Optional[str]:
        if not sealed:
            return None
        raw = base64.b64decode(sealed)
        return self._aes.decrypt(raw[:12], raw[12:], context.encode()).decode()


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, it, salt, digest = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(it))
    return hmac.compare_digest(dk.hex(), digest)


def new_code() -> str:
    return f"{secrets.randbelow(10 ** 6):06d}"


def code_hash(secret_key: str, flow_id: str, code: str) -> str:
    return hmac.new(secret_key.encode(), f"{flow_id}:{code}".encode(), hashlib.sha256).hexdigest()


def mask_email(email: str) -> str:
    user, _, host = email.partition("@")
    return (user[:1] + "•" * max(2, len(user) - 1) + "@" + host) if host else "•••"


def mask_phone(phone: str) -> str:
    digits = [c for c in phone if c.isdigit()]
    return "•" * max(0, len(digits) - 2) + "".join(digits[-2:])


class AuditLog:
    """Append-only, hash-chained event log (each row covers the previous row's hash)."""

    def __init__(self, db):
        self.db = db

    @staticmethod
    def _digest(prev: str, row: dict) -> str:
        payload = json.dumps(row, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256((prev + payload).encode()).hexdigest()

    def record(self, action: str, result: str, *, ip: str = "", domain_id: str = "", username: str = "",
               detail: str = "") -> None:
        with self.db.tx() as conn:
            last = conn.execute("SELECT hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
            prev = last["hash"] if last else "0" * 64
            row = {"at": iso(now()), "ip": ip, "domain_id": domain_id, "username": username.lower(),
                   "action": action, "result": result, "detail": detail[:500]}
            conn.execute("INSERT INTO events (at, ip, domain_id, username, action, result, detail, prev_hash, hash) "
                         "VALUES (:at, :ip, :domain_id, :username, :action, :result, :detail, :prev, :hash)",
                         {**row, "prev": prev, "hash": self._digest(prev, row)})

    def verify(self) -> tuple[bool, int]:
        prev, n = "0" * 64, 0
        for r in self.db.read().execute("SELECT * FROM events ORDER BY id"):
            row = {k: r[k] for k in ("at", "ip", "domain_id", "username", "action", "result", "detail")}
            if r["prev_hash"] != prev or r["hash"] != self._digest(prev, row):
                return False, r["id"]
            prev, n = r["hash"], n + 1
        return True, n


def window_start(minutes: int) -> str:
    return iso(now() - timedelta(minutes=minutes))
