"""
Users and groups (administrator-managed, or provisioned by SSO).
"""

from typing import List, Optional

from .auth import Principal, TokenService
from .crypto import hash_password
from .db import now_iso
from .vault import VaultError

ROLES = ("administrator", "user")
MIN_PASSWORD_LENGTH = 12


def _require_admin(p: Principal) -> None:
    if not p.is_admin:
        raise VaultError("administrator role required", 403)


def check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise VaultError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    classes = sum([any(c.islower() for c in password), any(c.isupper() for c in password),
                   any(c.isdigit() for c in password), any(not c.isalnum() for c in password)])
    if classes < 3:
        raise VaultError("password must mix at least three of: lower, upper, digit, symbol")


def user_out(row) -> dict:
    return {"id": row["id"], "username": row["username"], "display_name": row["display_name"],
            "email": row["email"], "role": row["role"], "enabled": bool(row["enabled"]),
            "auth_source": row["auth_source"], "mfa_enabled": bool(row["mfa_enabled"]),
            "locked_until": row["locked_until"], "last_login": row["last_login"]}


class Directory:
    def __init__(self, audit):
        self._audit = audit

    def create_user(self, conn, p: Optional[Principal], username: str, password: Optional[str],
                    role: str = "user", display_name: Optional[str] = None,
                    email: Optional[str] = None, auth_source: str = "local") -> dict:
        if p is not None:
            _require_admin(p)
        if role not in ROLES:
            raise VaultError(f"role must be one of {ROLES}")
        password_hash = None
        if auth_source == "local":
            if not password:
                raise VaultError("local users need a password")
            check_password_strength(password)
            password_hash = hash_password(password)
        elif conn.execute("SELECT 1 FROM sso_providers WHERE name = ?",
                          (auth_source.split(":", 1)[-1],)).fetchone() is None:
            raise VaultError("auth_source must be 'local' or an SSO provider name")
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise VaultError("username already exists", 409)
        cur = conn.execute(
            "INSERT INTO users (username, display_name, email, password_hash, auth_source, role, "
            "created_at) VALUES (?,?,?,?,?,?,?)",
            (username, display_name, email, password_hash,
             auth_source if auth_source == "local" else auth_source.split(":", 1)[-1],
             role, now_iso()))
        self._audit.record(conn, "USER_CREATE", p, "user", cur.lastrowid,
                           {"username": username, "role": role, "auth_source": auth_source},
                           username=None if p else "system")
        return self.get_user(conn, cur.lastrowid)

    @staticmethod
    def get_user(conn, user_id: int) -> dict:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise VaultError("user not found", 404)
        return user_out(row)

    def list_users(self, conn, p: Principal) -> List[dict]:
        _require_admin(p)
        return [user_out(r) for r in conn.execute("SELECT * FROM users ORDER BY username")]

    def update_user(self, conn, p: Principal, user_id: int, role: Optional[str] = None,
                    enabled: Optional[bool] = None, display_name: Optional[str] = None,
                    email: Optional[str] = None, password: Optional[str] = None,
                    reset_mfa: bool = False) -> dict:
        _require_admin(p)
        user = self.get_user(conn, user_id)
        changes = {}
        if role is not None:
            if role not in ROLES:
                raise VaultError(f"role must be one of {ROLES}")
            if user_id == p.user_id and role != "administrator":
                raise VaultError("cannot remove your own administrator role")
            changes["role"] = role
        if enabled is not None:
            if user_id == p.user_id and not enabled:
                raise VaultError("cannot disable yourself")
            changes["enabled"] = int(enabled)
        if display_name is not None:
            changes["display_name"] = display_name
        if email is not None:
            changes["email"] = email
        if password is not None:
            if user["auth_source"] != "local":
                raise VaultError("SSO users have no local password")
            check_password_strength(password)
            changes["password_hash"] = hash_password(password)
        if reset_mfa:
            changes["mfa_enabled"] = 0
            changes["mfa_secret_enc"] = None
        for column, value in changes.items():
            conn.execute(f"UPDATE users SET {column} = ? WHERE id = ?", (value, user_id))
        if {"enabled", "role", "password_hash", "mfa_enabled"} & set(changes):
            TokenService.revoke_user(conn, user_id)
        self._audit.record(conn, "USER_UPDATE", p, "user", user_id,
                           {"changed": sorted(changes)})
        return self.get_user(conn, user_id)

    def unlock_user(self, conn, p: Principal, user_id: int) -> dict:
        _require_admin(p)
        conn.execute("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = ?",
                     (user_id,))
        self._audit.record(conn, "USER_UNLOCK", p, "user", user_id)
        return self.get_user(conn, user_id)

    # ------------------------------------------------------------------ groups

    def create_group(self, conn, p: Principal, name: str, description: Optional[str] = None,
                     source: str = "local") -> dict:
        _require_admin(p)
        if conn.execute("SELECT 1 FROM groups WHERE name = ?", (name,)).fetchone():
            raise VaultError("group already exists", 409)
        cur = conn.execute("INSERT INTO groups (name, description, source) VALUES (?,?,?)",
                           (name, description, source))
        self._audit.record(conn, "GROUP_CREATE", p, "group", cur.lastrowid, {"name": name})
        return self.get_group(conn, cur.lastrowid)

    @staticmethod
    def get_group(conn, group_id: int) -> dict:
        g = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        if g is None:
            raise VaultError("group not found", 404)
        members = [r["user_id"] for r in conn.execute(
            "SELECT user_id FROM group_members WHERE group_id = ? ORDER BY user_id", (group_id,))]
        return {"id": g["id"], "name": g["name"], "description": g["description"],
                "source": g["source"], "member_ids": members}

    def list_groups(self, conn, p: Principal) -> List[dict]:
        # Non-admins need group ids to share secrets, so names are visible to all users
        rows = conn.execute("SELECT id FROM groups ORDER BY name").fetchall()
        groups = [self.get_group(conn, r["id"]) for r in rows]
        if not p.is_admin:
            for g in groups:
                g.pop("member_ids")
        return groups

    def add_member(self, conn, p: Principal, group_id: int, user_id: int) -> dict:
        _require_admin(p)
        self.get_group(conn, group_id)
        self.get_user(conn, user_id)
        conn.execute("INSERT OR IGNORE INTO group_members VALUES (?, ?)", (group_id, user_id))
        self._audit.record(conn, "GROUP_ADD_MEMBER", p, "group", group_id, {"user_id": user_id})
        return self.get_group(conn, group_id)

    def remove_member(self, conn, p: Principal, group_id: int, user_id: int) -> dict:
        _require_admin(p)
        conn.execute("DELETE FROM group_members WHERE group_id = ? AND user_id = ?",
                     (group_id, user_id))
        self._audit.record(conn, "GROUP_REMOVE_MEMBER", p, "group", group_id, {"user_id": user_id})
        return self.get_group(conn, group_id)
