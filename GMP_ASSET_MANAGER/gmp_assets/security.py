"""Credentials, roles and permissions.

* Passwords: PBKDF2-HMAC-SHA256 with per-user salt; complexity, expiry and
  re-use (last 5) rules; forced change on first login.
* Account lockout after repeated failures (login *and* e-signature attempts).
* Session tokens: random, stored only as SHA-256 hashes, idle timeout.
* Role-based access with segregation of duties: administrators manage users
  but cannot approve GMP records; QA approves but cannot record its own work.
"""

import hashlib
import hmac
import re
import secrets
from typing import Dict, FrozenSet, List

ROLES = ("admin", "qa", "technician", "operator", "viewer")

ROLE_PERMISSIONS: Dict[str, FrozenSet[str]] = {
    # Admins may register assets and plan/perform maintenance (small sites often have one person doing this),
    # but never approve GMP records: calibration approval and release to service stay with QA.
    "admin": frozenset({
        "user.manage", "location.manage", "company.manage", "asset.view", "audit.view",
        "asset.create", "asset.edit", "asset.move", "maintenance.plan", "maintenance.record",
    }),
    "qa": frozenset({
        "asset.view", "asset.edit", "audit.view", "asset.status.remove", "asset.status.release",
        "asset.qualify", "calibration.approve", "deviation.close", "maintenance.plan",
    }),
    "technician": frozenset({
        "asset.view", "asset.create", "asset.edit", "asset.move", "asset.status.remove",
        "calibration.record", "maintenance.record", "maintenance.plan",
    }),
    "operator": frozenset({"asset.view", "asset.move", "asset.status.remove"}),
    "viewer": frozenset({"asset.view"}),
}

SIGNATURE_MEANINGS = ("performed", "reviewed", "approved", "rejected", "released", "authored")


def hash_password(password: str, iterations: int) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def password_policy_errors(password: str, username: str, min_length: int) -> List[str]:
    errors = []
    if len(password) < min_length:
        errors.append(f"must be at least {min_length} characters")
    if not re.search(r"[A-Z]", password):
        errors.append("must contain an upper-case letter")
    if not re.search(r"[a-z]", password):
        errors.append("must contain a lower-case letter")
    if not re.search(r"\d", password):
        errors.append("must contain a digit")
    if username and username.lower() in password.lower():
        errors.append("must not contain the username")
    return errors


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def has_permission(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())
