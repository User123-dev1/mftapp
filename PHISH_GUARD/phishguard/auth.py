"""Console users (security team): admin, analyst, viewer."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from .db import Database

ROLES = ("admin", "analyst", "viewer")
PERMISSIONS = {
    "admin": {"view", "act", "review", "intel", "users"},
    "analyst": {"view", "act", "review", "intel"},
    "viewer": {"view"},
}
ITERATIONS = 310_000


class AuthError(Exception):
    pass


def now() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, it, salt, digest = stored.split("$")
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(it))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate.hex(), digest)


def password_problems(password: str, username: str) -> Optional[str]:
    if len(password) < 12:
        return "Password must be at least 12 characters"
    if not (re.search(r"[A-Z]", password) and re.search(r"[a-z]", password) and re.search(r"\d", password)):
        return "Password needs upper-case, lower-case and a digit"
    if username.lower() in password.lower():
        return "Password must not contain the user name"
    return None


class Users:
    def __init__(self, db: Database, idle_minutes: int, iterations: int = ITERATIONS):
        self.db, self.idle, self.iterations = db, idle_minutes, iterations

    def ensure_admin(self, password: Optional[str]) -> Optional[str]:
        with self.db.tx() as conn:
            if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                return None
            generated = None
            if not password:
                generated = password = "Tmp-" + secrets.token_urlsafe(12)
            conn.execute("INSERT INTO users (username, full_name, role, password_hash, created_at) "
                         "VALUES ('admin', 'Security Administrator', 'admin', ?, ?)",
                         (hash_password(password, self.iterations), now().isoformat()))
            return generated

    def login(self, username: str, password: str) -> dict:
        with self.db.tx() as conn:
            u = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            ok = u is not None and u["active"] and not u["locked"] and verify_password(password, u["password_hash"])
            if u is not None and not ok and not u["locked"]:
                attempts = u["failed_attempts"] + 1
                conn.execute("UPDATE users SET failed_attempts = ?, locked = ? WHERE id = ?",
                             (attempts, int(attempts >= 5), u["id"]))
            if ok:
                token = secrets.token_urlsafe(32)
                conn.execute("UPDATE users SET failed_attempts = 0 WHERE id = ?", (u["id"],))
                conn.execute("INSERT INTO sessions (token_hash, user_id, last_seen) VALUES (?, ?, ?)",
                             (hashlib.sha256(token.encode()).hexdigest(), u["id"], now().isoformat()))
        if not ok:
            raise AuthError("Account locked - ask an administrator" if u is not None and u["locked"]
                            else "Invalid user name or password")
        return {"token": token, "must_change_password": bool(u["must_change_password"])}

    def session(self, token: str) -> dict:
        th = hashlib.sha256((token or "").encode()).hexdigest()
        with self.db.tx() as conn:
            row = conn.execute("SELECT s.last_seen, u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                               "WHERE s.token_hash = ?", (th,)).fetchone()
            if row is None:
                raise AuthError("Not signed in")
            if now() - datetime.fromisoformat(row["last_seen"]) > timedelta(minutes=self.idle) \
                    or not row["active"] or row["locked"]:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (th,))
                raise AuthError("Session expired - sign in again")
            conn.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now().isoformat(), th))
        return {"id": row["id"], "username": row["username"], "full_name": row["full_name"], "role": row["role"],
                "email": row["email"], "must_change_password": bool(row["must_change_password"]),
                "permissions": sorted(PERMISSIONS[row["role"]])}

    def logout(self, token: str) -> None:
        with self.db.tx() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))

    def change_password(self, user: dict, old: str, new: str) -> None:
        problem = password_problems(new, user["username"])
        if problem:
            raise ValueError(problem)
        with self.db.tx() as conn:
            u = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
            if not verify_password(old, u["password_hash"]):
                raise AuthError("Current password is incorrect")
            if verify_password(new, u["password_hash"]):
                raise ValueError("Choose a different password")
            conn.execute("UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
                         (hash_password(new, self.iterations), user["id"]))

    def create(self, username: str, full_name: str, role: str, temp_password: str, email: str = "") -> None:
        if role not in ROLES:
            raise ValueError(f"Role must be one of {', '.join(ROLES)}")
        if not re.match(r"^[A-Za-z0-9._@-]{3,64}$", username):
            raise ValueError("User name: 3-64 letters, digits, . _ @ -")
        problem = password_problems(temp_password, username)
        if problem:
            raise ValueError(problem)
        with self.db.tx() as conn:
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                raise ValueError("User name already exists")
            conn.execute("INSERT INTO users (username, full_name, email, role, password_hash, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (username, full_name, email or None, role,
                                                        hash_password(temp_password, self.iterations), now().isoformat()))

    def list(self):
        with self.db.read() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT id, username, full_name, email, role, active, locked FROM users ORDER BY username")]

    def update(self, user_id: int, *, role=None, active=None, unlock=False, temp_password=None) -> None:
        with self.db.tx() as conn:
            if role:
                if role not in ROLES:
                    raise ValueError("Unknown role")
                conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
            if active is not None:
                conn.execute("UPDATE users SET active = ? WHERE id = ?", (int(active), user_id))
                if not active:
                    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            if unlock:
                conn.execute("UPDATE users SET locked = 0, failed_attempts = 0 WHERE id = ?", (user_id,))
            if temp_password:
                u = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
                problem = password_problems(temp_password, u["username"])
                if problem:
                    raise ValueError(problem)
                conn.execute("UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?",
                             (hash_password(temp_password, self.iterations), user_id))
