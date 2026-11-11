"""
Authentication: local password login, account lockout, TOTP MFA and session tokens.

Session tokens are short-lived HS256 JWTs whose `jti` is tracked in the
`sessions` table, so logout and admin disable take effect immediately.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import FrozenSet, Optional

import jwt
import pyotp

from .crypto import random_token, verify_password
from .db import commit_now, now_iso, put_one_time, take_one_time, utcnow


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Principal:
    user_id: int
    username: str
    role: str
    group_ids: FrozenSet[int]
    session_id: Optional[str] = None
    ip: Optional[str] = None

    @property
    def is_admin(self) -> bool:
        return self.role == "administrator"


def load_principal(conn, user_id: int, session_id: Optional[str] = None,
                   ip: Optional[str] = None) -> Principal:
    user = conn.execute(
        "SELECT id, username, role, enabled FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if user is None or not user["enabled"]:
        raise AuthError("account disabled")
    groups = frozenset(
        r["group_id"] for r in conn.execute(
            "SELECT group_id FROM group_members WHERE user_id = ?", (user_id,))
    )
    return Principal(user["id"], user["username"], user["role"], groups, session_id, ip)


class TokenService:
    ALGORITHM = "HS256"

    def __init__(self, keyring, settings):
        self._key = keyring.derive("session-token")
        self._settings = settings

    def issue(self, conn, user_id: int, auth_method: str) -> dict:
        jti = random_token(16)
        ttl = timedelta(minutes=self._settings.token_ttl_minutes)
        now = utcnow()
        conn.execute(
            "INSERT INTO sessions (jti, user_id, auth_method, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (jti, user_id, auth_method, now.isoformat(), (now + ttl).isoformat()),
        )
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (now.isoformat(), user_id))
        token = jwt.encode(
            {"sub": str(user_id), "jti": jti, "iat": now, "exp": now + ttl,
             "amr": auth_method, "typ": "session"},
            self._key, algorithm=self.ALGORITHM,
        )
        return {"access_token": token, "token_type": "bearer",
                "expires_in": int(ttl.total_seconds())}

    def authenticate(self, conn, token: str, ip: Optional[str] = None) -> Principal:
        try:
            claims = jwt.decode(token, self._key, algorithms=[self.ALGORITHM],
                                options={"require": ["exp", "sub", "jti"]})
        except jwt.PyJWTError:
            raise AuthError("invalid or expired token")
        if claims.get("typ") != "session":
            raise AuthError("invalid token type")
        session = conn.execute(
            "SELECT revoked, expires_at FROM sessions WHERE jti = ?", (claims["jti"],)
        ).fetchone()
        if session is None or session["revoked"] or session["expires_at"] < now_iso():
            raise AuthError("session revoked or expired")
        return load_principal(conn, int(claims["sub"]), claims["jti"], ip)

    @staticmethod
    def revoke(conn, jti: str) -> None:
        conn.execute("UPDATE sessions SET revoked = 1 WHERE jti = ?", (jti,))

    @staticmethod
    def revoke_user(conn, user_id: int) -> None:
        conn.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ?", (user_id,))


class LocalAuthenticator:
    def __init__(self, keyring, settings, tokens: TokenService, audit):
        self._keyring = keyring
        self._settings = settings
        self._tokens = tokens
        self._audit = audit

    def login(self, conn, username: str, password: str, ip: Optional[str]) -> dict:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        now = now_iso()

        def fail(reason: str, message: str = "invalid username or password", status: int = 401):
            if user is not None and reason == "bad_password":
                failed = user["failed_logins"] + 1
                locked = None
                if failed >= self._settings.max_failed_logins:
                    locked = now_iso(timedelta(minutes=self._settings.lockout_minutes))
                    failed = 0
                conn.execute("UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
                             (failed, locked, user["id"]))
            self._audit.record(conn, "LOGIN_FAILED", username=username, ip=ip,
                               target_type="user", target_id=user["id"] if user else None,
                               details={"reason": reason})
            commit_now(conn)
            raise AuthError(message, status)

        password_ok = verify_password(password, user["password_hash"] if user else None)
        if user is None:
            fail("unknown_user")
        if user["auth_source"] != "local":
            fail("sso_user", "this account must sign in through single sign-on", 403)
        if self._settings.sso_enforced and user["role"] != "administrator":
            fail("sso_enforced", "single sign-on is required", 403)
        if user["locked_until"] and user["locked_until"] > now:
            fail("locked", "account temporarily locked", 423)
        if not password_ok:
            fail("bad_password")
        if not user["enabled"]:
            fail("disabled", "account disabled", 403)

        conn.execute("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = ?",
                     (user["id"],))
        if user["mfa_enabled"]:
            mfa_token = random_token()
            put_one_time(conn, mfa_token, "mfa", {"user_id": user["id"]},
                         timedelta(seconds=self._settings.mfa_token_ttl_seconds))
            return {"mfa_required": True, "mfa_token": mfa_token}

        self._audit.record(conn, "LOGIN", username=user["username"], ip=ip,
                           target_type="user", target_id=user["id"], details={"method": "password"})
        return self._tokens.issue(conn, user["id"], "pwd")

    def verify_mfa(self, conn, mfa_token: str, code: str, ip: Optional[str]) -> dict:
        data = take_one_time(conn, mfa_token, "mfa")
        if data is None:
            raise AuthError("MFA challenge expired")
        user = conn.execute("SELECT * FROM users WHERE id = ?", (data["user_id"],)).fetchone()
        seed = self._keyring.decrypt_value(user["mfa_secret_enc"], f"mfa:{user['id']}")
        if not pyotp.TOTP(seed).verify(code, valid_window=1):
            self._audit.record(conn, "LOGIN_FAILED", username=user["username"], ip=ip,
                               target_type="user", target_id=user["id"],
                               details={"reason": "bad_mfa_code"})
            commit_now(conn)
            raise AuthError("invalid MFA code")
        self._audit.record(conn, "LOGIN", username=user["username"], ip=ip,
                           target_type="user", target_id=user["id"],
                           details={"method": "password+totp"})
        return self._tokens.issue(conn, user["id"], "pwd+otp")

    def begin_mfa_enrollment(self, conn, principal: Principal) -> dict:
        seed = pyotp.random_base32()
        conn.execute(
            "UPDATE users SET mfa_secret_enc = ?, mfa_enabled = 0 WHERE id = ?",
            (self._keyring.encrypt_value(seed, f"mfa:{principal.user_id}"), principal.user_id),
        )
        uri = pyotp.TOTP(seed).provisioning_uri(name=principal.username,
                                                issuer_name="Secret Server")
        return {"secret": seed, "otpauth_uri": uri}

    def confirm_mfa_enrollment(self, conn, principal: Principal, code: str) -> None:
        user = conn.execute("SELECT mfa_secret_enc FROM users WHERE id = ?",
                            (principal.user_id,)).fetchone()
        if not user["mfa_secret_enc"]:
            raise AuthError("no MFA enrollment in progress", 400)
        seed = self._keyring.decrypt_value(user["mfa_secret_enc"], f"mfa:{principal.user_id}")
        if not pyotp.TOTP(seed).verify(code, valid_window=1):
            raise AuthError("invalid MFA code", 400)
        conn.execute("UPDATE users SET mfa_enabled = 1 WHERE id = ?", (principal.user_id,))
        self._audit.record(conn, "MFA_ENABLED", principal, "user", principal.user_id)
