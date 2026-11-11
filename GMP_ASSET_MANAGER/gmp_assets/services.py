"""GMP asset management business logic.

Key GMP rules enforced here (not just in the UI):

* Lifecycle: commissioning -> in_service <-> out_of_service / quarantine -> retired.
  Release to service requires QA e-signature and a clean fitness check
  (qualified, calibration and PM current, no open deviations).
* Calibration: traceable reference standards must themselves be released and
  in calibration on the day of use. Out-of-tolerance (as-found or as-left
  fail) automatically quarantines the asset and opens a deviation.
  Results take effect only after independent QA approval (no self-approval).
* Every change is captured in the hash-chained audit trail with old/new
  values and a reason; e-signatures are bound to the signed record content.
* Nothing is deleted - assets are retired and records retain history.
"""

from __future__ import annotations

import calendar
import csv
import hashlib
import io
import json
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from . import audit, clock, notifications
from .barcode import code128_svg, is_valid_gs1_number, parse_barcode
from .config import Settings
from .db import Database
from .security import (
    ROLES,
    has_permission,
    hash_password,
    new_session_token,
    password_policy_errors,
    token_hash,
    verify_password,
)


class GMPError(Exception):
    status_code = 400


class AuthError(GMPError):
    status_code = 401


class Forbidden(GMPError):
    status_code = 403


class NotFound(GMPError):
    status_code = 404


class Conflict(GMPError):
    status_code = 409


class ValidationFailed(GMPError):
    status_code = 422


STATUSES = ("commissioning", "in_service", "out_of_service", "quarantine", "retired")
STATUS_TRANSITIONS = {
    "commissioning": {"in_service", "retired"},
    "in_service": {"out_of_service", "quarantine", "retired"},
    "out_of_service": {"in_service", "quarantine", "retired"},
    "quarantine": {"out_of_service", "in_service", "retired"},
    "retired": set(),
}
# Target status -> (permission, signature meaning or None if no e-signature needed).
# Activating an asset with no GMP impact is an ordinary edit; see _status_rule.
STATUS_RULES = {
    "in_service": ("asset.status.release", "released"),
    "retired": ("asset.status.release", "approved"),
    "out_of_service": ("asset.status.remove", None),
    "quarantine": ("asset.status.remove", None),
}
QUALIFICATION_STATUSES = (
    "not_required", "pending", "iq_complete", "oq_complete", "qualified", "requalification_required",
)
GMP_IMPACTS = ("direct", "indirect", "none")
TAG_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9\-_.]{1,39}$")
EDITABLE_ASSET_FIELDS = (
    "description", "category", "manufacturer", "model", "serial_number", "gtin", "department", "gmp_impact",
    "is_reference_standard", "calibration_required", "calibration_interval_days",
)
PREFIX_PATTERN = re.compile(r"^[A-Z0-9]{1,10}$")
WORK_TYPES = ("preventive", "inspection", "cleaning", "corrective")
INTERVAL_UNITS = ("days", "weeks", "months")
# Starter lists an administrator can load with one click (existing entries are kept)
STANDARD_DEPARTMENTS = (
    ("QA", "Quality Assurance"), ("QC", "Quality Control"), ("MICRO", "Microbiology"),
    ("MFG", "Manufacturing / Production"), ("PKG", "Packaging"), ("FAC", "Facilities & Engineering"),
    ("MAINT", "Maintenance"), ("METRO", "Metrology / Calibration"), ("VAL", "Validation"),
    ("WH", "Warehouse & Logistics"), ("SC", "Supply Chain & Procurement"), ("RA", "Regulatory Affairs"),
    ("RND", "Research & Development"), ("PD", "Process Development"), ("AD", "Analytical Development"),
    ("EHS", "Environment, Health & Safety"), ("IT", "IT / Computer Systems"), ("HR", "Human Resources"),
    ("FIN", "Finance"),
)
STANDARD_SITES = (
    ("US-MD", "USA - Maryland"), ("US-NJ", "USA - New Jersey"), ("US-MA", "USA - Massachusetts"),
    ("US-NC", "USA - North Carolina (RTP)"), ("PR-PR", "Puerto Rico"),
    ("CN-SH", "China - Shanghai"), ("CN-SZ", "China - Suzhou"),
    ("EU-IE", "Europe - Ireland"), ("EU-DE", "Europe - Germany"), ("EU-CH", "Europe - Switzerland"),
    ("EU-BE", "Europe - Belgium"), ("EU-FR", "Europe - France"), ("EU-IT", "Europe - Italy"),
    ("EU-UK", "Europe - United Kingdom"), ("IN-HYD", "India - Hyderabad"), ("SG-SG", "Singapore"),
    ("JP-TYO", "Japan - Tokyo"), ("GH-ACC", "Ghana - Accra"), ("ZA-JNB", "South Africa - Johannesburg"),
)

# Events that can trigger a work order automatically
PM_EVENTS = {
    "asset_activated": "Asset activated / released to service",
    "asset_deactivated": "Asset deactivated (taken out of service)",
    "asset_moved": "Asset moved to another location",
    "asset_quarantined": "Asset quarantined",
    "calibration_failed": "Calibration out of tolerance",
    "corrective_maintenance": "Corrective maintenance (repair) recorded",
    "deviation_closed": "Deviation closed",
}


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValidationFailed(f"{field} must be an ISO date (YYYY-MM-DD)")


def _plus_days(d: Optional[str], days: Optional[int]) -> Optional[str]:
    if not d or not days:
        return None
    return (date.fromisoformat(d) + timedelta(days=days)).isoformat()


def _add_interval(d: date, value: int, unit: str) -> date:
    if unit == "days":
        return d + timedelta(days=value)
    if unit == "weeks":
        return d + timedelta(weeks=value)
    month_index = d.month - 1 + value
    year, month = d.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _require_reason(reason: Optional[str]) -> str:
    if not reason or len(reason.strip()) < 3:
        raise ValidationFailed("A reason for change is required")
    return reason.strip()


def _row(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row is not None else None


def _content_hash(record: Any) -> str:
    return hashlib.sha256(audit.canonical_json(record).encode("utf-8")).hexdigest()


class GMPAssetService:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings

    # ------------------------------------------------------------------
    # Authentication, sessions and users
    # ------------------------------------------------------------------

    def ensure_admin(self, username: str, password: Optional[str]) -> Optional[str]:
        """Create the first administrator if the user table is empty.

        Returns the generated temporary password when none was supplied.
        The account must change its password at first login.
        """
        with self.db.transaction() as conn:
            if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                return None
            generated = None
            if not password:
                generated = password = "Tmp-" + secrets.token_urlsafe(12)
            now = clock.now_iso()
            cur = conn.execute(
                "INSERT INTO users (username, full_name, role, password_hash, password_changed_at, must_change_password, created_at) "
                "VALUES (?, ?, 'admin', ?, ?, 1, ?)",
                (username, "System Administrator", hash_password(password, self.settings.pbkdf2_iterations), now, now),
            )
            audit.record(conn, username="system", action="USER_CREATED", entity_type="user", entity_id=cur.lastrowid,
                         new={"username": username, "role": "admin"}, reason="Initial system bootstrap")
            return generated

    def login(self, username: str, password: str) -> dict:
        with self.db.transaction() as conn:
            user = _row(conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone())
            if user is None:
                audit.record(conn, username=username or "?", action="LOGIN_FAILED", entity_type="user", reason="Unknown user")
                failure = "Invalid username or password"
            elif not user["active"]:
                audit.record(conn, username=user["username"], action="LOGIN_FAILED", entity_type="user", entity_id=user["id"], reason="Account inactive")
                failure = "Account is inactive"
            elif user["locked"]:
                audit.record(conn, username=user["username"], action="LOGIN_FAILED", entity_type="user", entity_id=user["id"], reason="Account locked")
                failure = "Account is locked - contact your administrator"
            elif not verify_password(password, user["password_hash"]):
                failure = self._register_failed_credential(conn, user, "LOGIN_FAILED")
            else:
                failure = None
            if failure is None:
                conn.execute("UPDATE users SET failed_attempts = 0 WHERE id = ?", (user["id"],))
                token = new_session_token()
                now = clock.now_iso()
                conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, last_seen) VALUES (?, ?, ?, ?)",
                             (token_hash(token), user["id"], now, now))
                audit.record(conn, username=user["username"], action="LOGIN", entity_type="user", entity_id=user["id"])
        if failure:
            raise AuthError(failure)
        return {"token": token, "user": self._public_user(user), "must_change_password": self._must_change(user)}

    def _register_failed_credential(self, conn, user: dict, action: str) -> str:
        attempts = user["failed_attempts"] + 1
        locked = attempts >= self.settings.max_failed_logins
        conn.execute("UPDATE users SET failed_attempts = ?, locked = ? WHERE id = ?", (attempts, int(locked), user["id"]))
        audit.record(conn, username=user["username"], action=action, entity_type="user", entity_id=user["id"],
                     reason=f"Invalid password (attempt {attempts})")
        if locked:
            audit.record(conn, username="system", action="ACCOUNT_LOCKED", entity_type="user", entity_id=user["id"],
                         reason=f"{attempts} consecutive failed credential attempts")
            return "Account is locked - contact your administrator"
        return "Invalid username or password"

    def _must_change(self, user: dict) -> bool:
        changed = datetime.fromisoformat(user["password_changed_at"])
        expired = clock.now() - changed > timedelta(days=self.settings.password_max_age_days)
        return bool(user["must_change_password"]) or expired

    @staticmethod
    def _public_user(user: dict) -> dict:
        return {k: user[k] for k in ("id", "username", "full_name", "email", "role", "active", "locked")}

    def session_user(self, token: str) -> dict:
        if not token:
            raise AuthError("Not authenticated")
        with self.db.transaction() as conn:
            session = _row(conn.execute("SELECT * FROM sessions WHERE token_hash = ?", (token_hash(token),)).fetchone())
            if session is None:
                raise AuthError("Session expired or invalid")
            idle = clock.now() - datetime.fromisoformat(session["last_seen"])
            user = _row(conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone())
            if idle > timedelta(minutes=self.settings.session_idle_minutes) or not user["active"] or user["locked"]:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (session["token_hash"],))
                expired = True
            else:
                conn.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (clock.now_iso(), session["token_hash"]))
                expired = False
        if expired:
            raise AuthError("Session expired - please log in again")
        result = self._public_user(user)
        result["must_change_password"] = self._must_change(user)
        return result

    def logout(self, token: str, user: dict) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))
            audit.record(conn, username=user["username"], action="LOGOUT", entity_type="user", entity_id=user["id"])

    def change_password(self, user: dict, old_password: str, new_password: str) -> None:
        errors = password_policy_errors(new_password, user["username"], self.settings.password_min_length)
        if errors:
            raise ValidationFailed("Password " + "; ".join(errors))
        with self.db.transaction() as conn:
            row = _row(conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone())
            if not verify_password(old_password, row["password_hash"]):
                raise AuthError("Current password is incorrect")
            history = [row["password_hash"]] + [
                r["password_hash"] for r in conn.execute(
                    "SELECT password_hash FROM password_history WHERE user_id = ? ORDER BY id DESC LIMIT 4", (user["id"],))
            ]
            if any(verify_password(new_password, h) for h in history):
                raise ValidationFailed("Password was used recently - choose a different one")
            now = clock.now_iso()
            conn.execute("INSERT INTO password_history (user_id, password_hash, created_at) VALUES (?, ?, ?)",
                         (user["id"], row["password_hash"], now))
            conn.execute(
                "UPDATE users SET password_hash = ?, password_changed_at = ?, must_change_password = 0, failed_attempts = 0 WHERE id = ?",
                (hash_password(new_password, self.settings.pbkdf2_iterations), now, user["id"]),
            )
            audit.record(conn, username=user["username"], action="PASSWORD_CHANGED", entity_type="user", entity_id=user["id"])

    def require(self, user: dict, permission: str) -> None:
        if not has_permission(user["role"], permission):
            raise Forbidden(f"Role '{user['role']}' is not permitted to perform '{permission}'")

    def list_users(self, user: dict) -> List[dict]:
        self.require(user, "user.manage")
        with self.db.read() as conn:
            return [self._public_user(dict(r)) for r in conn.execute("SELECT * FROM users ORDER BY username")]

    @staticmethod
    def _clean_email(email: Optional[str]) -> Optional[str]:
        email = (email or "").strip()
        if not email:
            return None
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or len(email) > 254:
            raise ValidationFailed("E-mail address is not valid")
        return email.lower()

    def create_user(self, admin: dict, username: str, full_name: str, role: str, temporary_password: str,
                    email: Optional[str] = None) -> dict:
        self.require(admin, "user.manage")
        email = self._clean_email(email)
        if role not in ROLES:
            raise ValidationFailed(f"Role must be one of {', '.join(ROLES)}")
        if not re.match(r"^[A-Za-z0-9._-]{3,40}$", username or ""):
            raise ValidationFailed("Username must be 3-40 characters: letters, digits, . _ -")
        if not full_name or not full_name.strip():
            raise ValidationFailed("Full name is required (it appears on electronic signatures)")
        errors = password_policy_errors(temporary_password, username, self.settings.password_min_length)
        if errors:
            raise ValidationFailed("Temporary password " + "; ".join(errors))
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                # User IDs are never re-used (Part 11 §11.300(a)), even for inactive accounts
                raise Conflict("Username already exists (user IDs cannot be re-used)")
            now = clock.now_iso()
            cur = conn.execute(
                "INSERT INTO users (username, full_name, email, role, password_hash, password_changed_at, must_change_password, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (username, full_name.strip(), email, role, hash_password(temporary_password, self.settings.pbkdf2_iterations),
                 now, now),
            )
            audit.record(conn, username=admin["username"], action="USER_CREATED", entity_type="user", entity_id=cur.lastrowid,
                         new={"username": username, "full_name": full_name.strip(), "email": email, "role": role})
            return self._public_user(dict(conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()))

    def update_user(self, admin: dict, user_id: int, *, role: Optional[str] = None, active: Optional[bool] = None,
                    unlock: bool = False, temporary_password: Optional[str] = None, reason: Optional[str] = None,
                    email: Optional[str] = None, full_name: Optional[str] = None) -> dict:
        self.require(admin, "user.manage")
        reason = _require_reason(reason)
        with self.db.transaction() as conn:
            target = _row(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
            if target is None:
                raise NotFound("User not found")
            if target["id"] == admin["id"] and (active is False or (role and role != "admin")):
                raise ValidationFailed("Administrators cannot deactivate or demote themselves")
            old = self._public_user(target)
            updates: Dict[str, Any] = {}
            if role is not None:
                if role not in ROLES:
                    raise ValidationFailed(f"Role must be one of {', '.join(ROLES)}")
                updates["role"] = role
            if active is not None:
                updates["active"] = int(active)
            if email is not None and self._clean_email(email) != target["email"]:
                updates["email"] = self._clean_email(email)
            if full_name is not None and full_name.strip() and full_name.strip() != target["full_name"]:
                updates["full_name"] = full_name.strip()
            if unlock:
                updates.update(locked=0, failed_attempts=0)
            if temporary_password:
                errors = password_policy_errors(temporary_password, target["username"], self.settings.password_min_length)
                if errors:
                    raise ValidationFailed("Temporary password " + "; ".join(errors))
                updates.update(password_hash=hash_password(temporary_password, self.settings.pbkdf2_iterations),
                               password_changed_at=clock.now_iso(), must_change_password=1)
            if not updates:
                raise ValidationFailed("No changes requested")
            conn.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k in updates)} WHERE id = ?", (*updates.values(), user_id))
            if updates.get("active") == 0 or "role" in updates:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            new = self._public_user(dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()))
            audit_new = dict(new, password_reset=bool(temporary_password))
            audit.record(conn, username=admin["username"], action="USER_MODIFIED", entity_type="user", entity_id=user_id,
                         old=old, new=audit_new, reason=reason)
            return new

    # ------------------------------------------------------------------
    # Company setup
    # ------------------------------------------------------------------

    def get_company(self) -> Optional[dict]:
        with self.db.read() as conn:
            return _row(conn.execute("SELECT * FROM company WHERE id = 1").fetchone())

    def save_company(self, user: dict, *, name: str, site_name: str, address: Optional[str], asset_prefix: str,
                     reason: Optional[str] = None) -> dict:
        """First-time company setup, or a later change (which needs a reason)."""
        self.require(user, "company.manage")
        name, site_name = (name or "").strip(), (site_name or "").strip()
        prefix = (asset_prefix or "").strip().upper()
        if not name or not site_name:
            raise ValidationFailed("Company name and site name are required")
        if not PREFIX_PATTERN.match(prefix):
            raise ValidationFailed("Asset ID prefix must be 1-10 letters or digits, e.g. ACME")
        now = clock.now_iso()
        with self.db.transaction() as conn:
            old = _row(conn.execute("SELECT * FROM company WHERE id = 1").fetchone())
            new = {"name": name, "site_name": site_name, "address": (address or "").strip() or None, "asset_prefix": prefix}
            if old is None:
                conn.execute("INSERT INTO company (id, name, site_name, address, asset_prefix, created_at, updated_at) "
                             "VALUES (1, :name, :site_name, :address, :asset_prefix, :now, :now)", dict(new, now=now))
                audit.record(conn, username=user["username"], action="COMPANY_SETUP", entity_type="company", entity_id=1,
                             new=new, reason=(reason or "").strip() or "Initial company setup")
            else:
                reason = _require_reason(reason)
                changed = {k: v for k, v in new.items() if old[k] != v}
                if not changed:
                    raise ValidationFailed("No changes")
                if "asset_prefix" in changed:
                    # existing asset IDs are never renumbered; restart the counter for the new prefix
                    changed["next_asset_number"] = 1
                conn.execute(f"UPDATE company SET {', '.join(f'{k} = ?' for k in changed)}, updated_at = ? WHERE id = 1",
                             (*changed.values(), now))
                audit.record(conn, username=user["username"], action="COMPANY_MODIFIED", entity_type="company", entity_id=1,
                             old={k: old[k] for k in changed}, new=changed, reason=reason)
            return dict(conn.execute("SELECT * FROM company WHERE id = 1").fetchone())

    # ------------------------------------------------------------------
    # Electronic signatures (21 CFR 11.50 / 11.70 / 11.200)
    # ------------------------------------------------------------------

    def _verify_signer(self, user: dict, password: Optional[str], meaning: str) -> None:
        """Re-authenticate the signer. Failures count towards account lockout."""
        if not password:
            raise AuthError(f"Electronic signature ({meaning}) required: re-enter your password")
        with self.db.transaction() as conn:
            row = _row(conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone())
            if row["locked"] or not row["active"]:
                raise AuthError("Account is locked or inactive")
            if verify_password(password, row["password_hash"]):
                conn.execute("UPDATE users SET failed_attempts = 0 WHERE id = ?", (user["id"],))
                return
            message = self._register_failed_credential(conn, row, "SIGNATURE_FAILED")
        raise AuthError(f"Electronic signature failed: {message}")

    def _apply_signature(self, conn, user: dict, record_type: str, record_id: int, meaning: str, record: Any) -> dict:
        signature = {
            "record_type": record_type, "record_id": record_id, "username": user["username"],
            "full_name": user["full_name"], "meaning": meaning, "signed_at": clock.now_iso(),
            "record_hash": _content_hash(record),
        }
        cur = conn.execute(
            "INSERT INTO signatures (record_type, record_id, username, full_name, meaning, signed_at, record_hash) "
            "VALUES (:record_type, :record_id, :username, :full_name, :meaning, :signed_at, :record_hash)", signature)
        signature["id"] = cur.lastrowid
        audit.record(conn, username=user["username"], action="E_SIGNATURE", entity_type=record_type, entity_id=record_id,
                     new=signature)
        return signature

    @staticmethod
    def _signatures(conn, record_type: str, record_id: int) -> List[dict]:
        return [dict(r) for r in conn.execute(
            "SELECT username, full_name, meaning, signed_at, record_hash FROM signatures "
            "WHERE record_type = ? AND record_id = ? ORDER BY id", (record_type, record_id))]

    # ------------------------------------------------------------------
    # Locations
    # ------------------------------------------------------------------

    def list_locations(self, user: dict) -> List[dict]:
        self.require(user, "asset.view")
        with self.db.read() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM locations ORDER BY code")]

    def create_location(self, user: dict, code: str, name: str, area_class: Optional[str] = None) -> dict:
        self.require(user, "location.manage")
        code = (code or "").strip().upper()
        if not TAG_PATTERN.match(code):
            raise ValidationFailed("Location code must be 2-40 characters: A-Z, 0-9, - _ .")
        if not name or not name.strip():
            raise ValidationFailed("Location name is required")
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM locations WHERE code = ?", (code,)).fetchone() or \
                    conn.execute("SELECT 1 FROM assets WHERE asset_tag = ?", (code,)).fetchone():
                raise Conflict("Code already used by a location or asset")
            cur = conn.execute("INSERT INTO locations (code, name, area_class, created_at) VALUES (?, ?, ?, ?)",
                               (code, name.strip(), area_class, clock.now_iso()))
            loc = dict(conn.execute("SELECT * FROM locations WHERE id = ?", (cur.lastrowid,)).fetchone())
            audit.record(conn, username=user["username"], action="LOCATION_CREATED", entity_type="location",
                         entity_id=loc["id"], new=loc)
            return loc

    def load_standard_locations(self, user: dict) -> dict:
        """Add the standard site list (USA - Maryland, China - Shanghai, Europe - ...); existing codes are skipped."""
        self.require(user, "location.manage")
        added = []
        with self.db.transaction() as conn:
            for code, name in STANDARD_SITES:
                if conn.execute("SELECT 1 FROM locations WHERE code = ? UNION SELECT 1 FROM assets WHERE asset_tag = ?",
                                (code, code)).fetchone():
                    continue
                conn.execute("INSERT INTO locations (code, name, area_class, created_at) VALUES (?, ?, 'Site', ?)",
                             (code, name, clock.now_iso()))
                added.append(code)
            if added:
                audit.record(conn, username=user["username"], action="LOCATIONS_LOADED", entity_type="location",
                             new={"added": added}, reason="Loaded standard site list")
        return {"added": len(added), "skipped": len(STANDARD_SITES) - len(added)}

    # ------------------------------------------------------------------
    # Departments
    # ------------------------------------------------------------------

    def list_departments(self, user: dict) -> List[dict]:
        self.require(user, "asset.view")
        with self.db.read() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM departments ORDER BY name")]

    def create_department(self, user: dict, code: str, name: str) -> dict:
        self.require(user, "location.manage")
        code, name = (code or "").strip().upper(), (name or "").strip()
        if not re.match(r"^[A-Z0-9][A-Z0-9_-]{0,19}$", code):
            raise ValidationFailed("Department code must be 1-20 characters: A-Z, 0-9, - _")
        if len(name) < 2:
            raise ValidationFailed("Department name is required")
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM departments WHERE code = ? OR name = ?", (code, name)).fetchone():
                raise Conflict("A department with this code or name already exists")
            cur = conn.execute("INSERT INTO departments (code, name, created_at) VALUES (?, ?, ?)", (code, name, clock.now_iso()))
            dept = dict(conn.execute("SELECT * FROM departments WHERE id = ?", (cur.lastrowid,)).fetchone())
            audit.record(conn, username=user["username"], action="DEPARTMENT_CREATED", entity_type="department",
                         entity_id=dept["id"], new=dept)
            return dept

    def load_standard_departments(self, user: dict) -> dict:
        self.require(user, "location.manage")
        added = []
        with self.db.transaction() as conn:
            for code, name in STANDARD_DEPARTMENTS:
                if conn.execute("SELECT 1 FROM departments WHERE code = ? OR name = ?", (code, name)).fetchone():
                    continue
                conn.execute("INSERT INTO departments (code, name, created_at) VALUES (?, ?, ?)", (code, name, clock.now_iso()))
                added.append(code)
            if added:
                audit.record(conn, username=user["username"], action="DEPARTMENTS_LOADED", entity_type="department",
                             new={"added": added}, reason="Loaded standard pharmaceutical department list")
        return {"added": len(added), "skipped": len(STANDARD_DEPARTMENTS) - len(added)}

    @staticmethod
    def _canonical_department(conn, value: Optional[str]) -> Optional[str]:
        """Departments must come from the managed list; returns the stored spelling."""
        value = (value or "").strip()
        if not value:
            return None
        row = conn.execute("SELECT name FROM departments WHERE active = 1 AND (name = ? OR code = ?)",
                           (value, value.upper())).fetchone()
        if row is None:
            raise ValidationFailed(f"Department '{value}' is not in the department list (Administration > Departments)")
        return row["name"]

    # ------------------------------------------------------------------
    # Assets
    # ------------------------------------------------------------------

    _ASSET_SELECT = (
        "SELECT a.*, l.code AS location_code, l.name AS location_name, l.area_class AS location_area_class "
        "FROM assets a LEFT JOIN locations l ON l.id = a.location_id "
    )

    def _asset(self, conn, tag: str) -> dict:
        row = conn.execute(self._ASSET_SELECT + "WHERE a.asset_tag = ?", ((tag or "").strip().upper(),)).fetchone()
        if row is None:
            raise NotFound(f"Asset {tag} not found")
        return dict(row)

    @staticmethod
    def _asset_core(asset: dict) -> dict:
        """Asset fields without join columns - used for audit old/new values."""
        return {k: v for k, v in asset.items() if not k.startswith("location_") or k == "location_id"}

    @staticmethod
    def _normalise_gtin(gtin: Optional[str]) -> Optional[str]:
        if not gtin:
            return None
        gtin = gtin.strip()
        if not gtin.isdigit() or len(gtin) not in (8, 12, 13, 14) or not is_valid_gs1_number(gtin):
            raise ValidationFailed("GTIN must be 8/12/13/14 digits with a valid check digit")
        return gtin.zfill(14)

    def _validate_asset_fields(self, data: dict) -> None:
        if not (data.get("description") or "").strip():
            raise ValidationFailed("Description is required")
        if not (data.get("category") or "").strip():
            raise ValidationFailed("Category is required")
        if data.get("gmp_impact") not in GMP_IMPACTS:
            raise ValidationFailed(f"gmp_impact must be one of {', '.join(GMP_IMPACTS)}")
        if data.get("calibration_required"):
            value = data.get("calibration_interval_days")
            if not isinstance(value, int) or not 1 <= value <= 3650:
                raise ValidationFailed("calibration_interval_days (1-3650) is required when calibration is required")
        if data.get("is_reference_standard") and not data.get("calibration_required"):
            raise ValidationFailed("A reference standard must itself be under calibration control")

    def list_assets(self, user: dict, *, status: Optional[str] = None, location: Optional[str] = None,
                    search: Optional[str] = None) -> List[dict]:
        self.require(user, "asset.view")
        sql, args = self._ASSET_SELECT + "WHERE 1=1 ", []
        if status:
            sql += "AND a.status = ? "
            args.append(status)
        if location:
            sql += "AND l.code = ? "
            args.append(location.upper())
        if search:
            sql += "AND (a.asset_tag LIKE ? OR a.description LIKE ? OR a.serial_number LIKE ? OR a.model LIKE ?) "
            args.extend([f"%{search}%"] * 4)
        with self.db.read() as conn:
            assets = [dict(r) for r in conn.execute(sql + "ORDER BY a.asset_tag", args)]
            for a in assets:
                a["fitness"] = self._fitness(conn, a)
            return assets

    def get_asset(self, user: dict, tag: str) -> dict:
        self.require(user, "asset.view")
        with self.db.read() as conn:
            asset = self._asset(conn, tag)
            asset["fitness"] = self._fitness(conn, asset)
            asset["calibrations"] = self._calibrations(conn, asset["id"])
            asset["maintenance"] = [dict(r) for r in conn.execute(
                "SELECT * FROM maintenance_records WHERE asset_id = ? ORDER BY performed_on DESC, id DESC", (asset["id"],))]
            for m in asset["maintenance"]:
                m["signatures"] = self._signatures(conn, "maintenance", m["id"])
            asset["deviations"] = [dict(r) for r in conn.execute(
                "SELECT * FROM deviations WHERE asset_id = ? ORDER BY id DESC", (asset["id"],))]
            asset["signatures"] = self._signatures(conn, "asset", asset["id"])
            asset["pm_schedules"] = [dict(r) for r in conn.execute(
                "SELECT * FROM pm_schedules WHERE asset_id = ? ORDER BY active DESC, id", (asset["id"],))]
            asset["approvals"] = [dict(r) for r in conn.execute(
                self._APPROVAL_SELECT + "WHERE r.asset_id = ? ORDER BY r.id DESC", (asset["id"],))]
            asset["work_orders"] = [self._decorate_wo(conn, dict(r)) for r in conn.execute(
                self._WO_SELECT + "WHERE w.asset_id = ? ORDER BY w.status = 'open' DESC, w.due_on DESC, w.id DESC", (asset["id"],))]
            return asset

    def create_asset(self, user: dict, data: dict) -> dict:
        """Register an asset. The asset ID is generated from the company prefix
        unless an existing tag is supplied (e.g. equipment already labelled)."""
        self.require(user, "asset.create")
        data = dict(data)
        supplied_tag = (data.get("asset_tag") or "").strip().upper()
        if supplied_tag and not TAG_PATTERN.match(supplied_tag):
            raise ValidationFailed("Asset tag must be 2-40 characters: A-Z, 0-9, - _ .")
        self._validate_asset_fields(data)
        if data.get("pm_required"):
            value = data.get("pm_interval_days")
            if not isinstance(value, int) or not 1 <= value <= 3650:
                raise ValidationFailed("pm_interval_days (1-3650) is required when PM is required")
        initial_state = data.get("initial_state") or "inactive"
        if initial_state not in ("active", "inactive"):
            raise ValidationFailed("initial_state must be active or inactive")
        if initial_state == "active":
            if data["gmp_impact"] != "none":
                raise ValidationFailed(
                    "GMP-impacting assets must be qualified, calibrated and released by QA before use - "
                    "register as Inactive and activate after release")
            if data.get("calibration_required"):
                raise ValidationFailed("Assets requiring calibration must be calibrated before activation - register as Inactive")
        gtin = self._normalise_gtin(data.get("gtin"))
        now = clock.now_iso()
        with self.db.transaction() as conn:
            company = conn.execute("SELECT * FROM company WHERE id = 1").fetchone()
            if company is None:
                raise Conflict("Company setup must be completed before assets can be registered")
            data["department"] = self._canonical_department(conn, data.get("department"))
            tag = supplied_tag or self._next_asset_tag(conn, company)
            if conn.execute("SELECT 1 FROM assets WHERE asset_tag = ?", (tag,)).fetchone() or \
                    conn.execute("SELECT 1 FROM locations WHERE code = ?", (tag,)).fetchone():
                raise Conflict(f"Tag {tag} is already used by an asset or location")
            if gtin and data.get("serial_number") and conn.execute(
                    "SELECT 1 FROM assets WHERE gtin = ? AND serial_number = ?", (gtin, data["serial_number"])).fetchone():
                raise Conflict("Another asset already has this GTIN + serial number")
            location_id = None
            if data.get("location_code"):
                loc = conn.execute("SELECT id FROM locations WHERE code = ? AND active = 1",
                                   (data["location_code"].upper(),)).fetchone()
                if loc is None:
                    raise ValidationFailed(f"Location {data['location_code']} not found")
                location_id = loc["id"]
            status = "in_service" if initial_state == "active" else "commissioning"
            cur = conn.execute(
                "INSERT INTO assets (asset_tag, description, category, gmp_impact, manufacturer, model, serial_number, gtin, "
                "department, location_id, status, qualification_status, is_reference_standard, calibration_required, "
                "calibration_interval_days, created_at, created_by, updated_at, updated_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tag, data["description"].strip(), data["category"].strip(), data["gmp_impact"],
                    data.get("manufacturer"), data.get("model"), data.get("serial_number"), gtin, data.get("department"),
                    location_id, status, "not_required" if data["gmp_impact"] == "none" else "pending",
                    int(bool(data.get("is_reference_standard"))), int(bool(data.get("calibration_required"))),
                    data.get("calibration_interval_days") if data.get("calibration_required") else None,
                    now, user["username"], now, user["username"],
                ),
            )
            asset = self._asset_core(dict(conn.execute("SELECT * FROM assets WHERE id = ?", (cur.lastrowid,)).fetchone()))
            audit.record(conn, username=user["username"], action="ASSET_CREATED", entity_type="asset", entity_id=tag,
                         new=asset, reason=data.get("reason") or "New asset registration")
            if data.get("pm_required"):
                self._insert_schedule(conn, user, self._asset(conn, tag), {
                    "title": "Preventive maintenance", "work_type": "preventive", "trigger_type": "time",
                    "interval_value": data["pm_interval_days"], "interval_unit": "days",
                })
        return self.get_asset(user, tag)

    @staticmethod
    def _next_asset_tag(conn, company) -> str:
        number = company["next_asset_number"]
        while True:
            tag = f"{company['asset_prefix']}-{number:06d}"
            number += 1
            if not conn.execute("SELECT 1 FROM assets WHERE asset_tag = ? UNION SELECT 1 FROM locations WHERE code = ?",
                                (tag, tag)).fetchone():
                break
        conn.execute("UPDATE company SET next_asset_number = ? WHERE id = 1", (number,))
        return tag

    def update_asset(self, user: dict, tag: str, changes: dict, reason: str, expected_version: Optional[int] = None) -> dict:
        self.require(user, "asset.edit")
        reason = _require_reason(reason)
        unknown = set(changes) - set(EDITABLE_ASSET_FIELDS)
        if unknown:
            raise ValidationFailed(
                f"Fields not editable here: {', '.join(sorted(unknown))} (use the status, move, qualification or calibration workflows)")
        with self.db.transaction() as conn:
            asset = self._asset(conn, tag)
            if asset["status"] == "retired":
                raise Conflict("Retired assets cannot be modified")
            if expected_version is not None and expected_version != asset["version"]:
                raise Conflict("Asset was modified by someone else - reload and try again")
            old = self._asset_core(asset)
            merged = dict(old)
            merged.update(changes)
            if "gtin" in changes:
                merged["gtin"] = self._normalise_gtin(changes["gtin"])
            for flag in ("is_reference_standard", "calibration_required"):
                merged[flag] = int(bool(merged[flag]))
            if "department" in changes:
                merged["department"] = self._canonical_department(conn, changes["department"])
            self._validate_asset_fields(merged)
            if not merged["calibration_required"]:
                merged["calibration_interval_days"] = None
                merged["calibration_due_on"] = None
            else:
                merged["calibration_due_on"] = _plus_days(merged["last_calibrated_on"], merged["calibration_interval_days"])
            if merged["gmp_impact"] != old["gmp_impact"]:
                if merged["gmp_impact"] == "none":
                    merged["qualification_status"] = "not_required"
                elif old["gmp_impact"] == "none":
                    merged["qualification_status"] = "pending"
            diff_keys = [k for k in merged if merged[k] != old[k]]
            if not diff_keys:
                raise ValidationFailed("No changes")
            merged.update(version=old["version"] + 1, updated_at=clock.now_iso(), updated_by=user["username"])
            diff_keys += ["version", "updated_at", "updated_by"]
            conn.execute(f"UPDATE assets SET {', '.join(f'{k} = ?' for k in diff_keys)} WHERE id = ?",
                         (*[merged[k] for k in diff_keys], asset["id"]))
            audit.record(conn, username=user["username"], action="ASSET_MODIFIED", entity_type="asset", entity_id=asset["asset_tag"],
                         old={k: old[k] for k in diff_keys}, new={k: merged[k] for k in diff_keys}, reason=reason)
        return self.get_asset(user, tag)

    def _set_asset_fields(self, conn, user: dict, asset: dict, fields: dict, action: str, reason: str) -> dict:
        old = {k: asset[k] for k in fields}
        fields = dict(fields, version=asset["version"] + 1, updated_at=clock.now_iso(), updated_by=user["username"])
        conn.execute(f"UPDATE assets SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), asset["id"]))
        audit.record(conn, username=user["username"], action=action, entity_type="asset", entity_id=asset["asset_tag"],
                     old=old, new=fields, reason=reason)
        updated = dict(asset)
        updated.update(fields)
        return updated

    def move_asset(self, user: dict, tag: str, location_code: str, reason: Optional[str] = None) -> dict:
        self.require(user, "asset.move")
        with self.db.transaction() as conn:
            asset = self._asset(conn, tag)
            if asset["status"] == "retired":
                raise Conflict("Retired assets cannot be moved")
            loc = conn.execute("SELECT * FROM locations WHERE code = ? AND active = 1",
                               ((location_code or "").strip().upper(),)).fetchone()
            if loc is None:
                raise NotFound(f"Location {location_code} not found")
            if asset["location_id"] == loc["id"]:
                raise ValidationFailed(f"Asset is already at {loc['code']}")
            self._set_asset_fields(conn, user, asset, {"location_id": loc["id"]}, "ASSET_MOVED",
                                   (reason or "").strip() or f"Relocated to {loc['code']} by barcode scan")
            self._fire_event(conn, user, asset, "asset_moved", f"moved to {loc['code']}")
        return self.get_asset(user, tag)

    @staticmethod
    def _status_rule(asset: dict, new_status: str):
        if new_status == "in_service" and asset["gmp_impact"] == "none":
            return "asset.edit", None  # non-GMP equipment: simple activation, no QA release
        return STATUS_RULES[new_status]

    def change_status(self, user: dict, tag: str, new_status: str, reason: str, password: Optional[str] = None) -> dict:
        if new_status not in STATUS_RULES:
            raise ValidationFailed(f"Status must be one of {', '.join(STATUS_RULES)}")
        with self.db.read() as conn:
            asset = self._asset(conn, tag)
        permission, meaning = self._status_rule(asset, new_status)
        self.require(user, permission)
        reason = _require_reason(reason)
        with self.db.read() as conn:
            self._check_transition(conn, asset, new_status)
        if meaning:
            self._verify_signer(user, password, meaning)
        with self.db.transaction() as conn:
            asset = self._asset(conn, tag)
            self._check_transition(conn, asset, new_status)
            updated = self._set_asset_fields(conn, user, asset, {"status": new_status}, "STATUS_CHANGED", reason)
            if meaning:
                self._apply_signature(conn, user, "asset", asset["id"], meaning, self._asset_core(updated))
            event = {"in_service": "asset_activated", "out_of_service": "asset_deactivated",
                     "quarantine": "asset_quarantined"}.get(new_status)
            if event:
                self._fire_event(conn, user, asset, event, reason)
        return self.get_asset(user, tag)

    def _check_transition(self, conn, asset: dict, new_status: str) -> None:
        if new_status not in STATUS_TRANSITIONS[asset["status"]]:
            raise Conflict(f"Cannot change status from {asset['status']} to {new_status}")
        if new_status == "in_service" and asset["status"] == "commissioning" and asset["gmp_impact"] != "none":
            raise Conflict("New GMP equipment is activated through QA induction approval - use 'Submit for QA approval'")
        if new_status == "in_service":
            blockers = self._release_blockers(conn, asset)
            if blockers:
                raise Conflict("Cannot release to service: " + "; ".join(blockers))

    def set_qualification(self, user: dict, tag: str, qualification_status: str, reason: str, password: Optional[str]) -> dict:
        self.require(user, "asset.qualify")
        reason = _require_reason(reason)
        if qualification_status not in QUALIFICATION_STATUSES:
            raise ValidationFailed(f"Qualification status must be one of {', '.join(QUALIFICATION_STATUSES)}")
        with self.db.read() as conn:
            asset = self._asset(conn, tag)
        if asset["gmp_impact"] == "none" and qualification_status != "not_required":
            raise ValidationFailed("Asset has no GMP impact - qualification is not required")
        if asset["gmp_impact"] != "none" and qualification_status == "not_required":
            raise ValidationFailed("GMP-impacting assets require qualification")
        if asset["qualification_status"] == qualification_status:
            raise ValidationFailed("Qualification status unchanged")
        self._verify_signer(user, password, "approved")
        with self.db.transaction() as conn:
            asset = self._asset(conn, tag)
            updated = self._set_asset_fields(conn, user, asset, {"qualification_status": qualification_status},
                                             "QUALIFICATION_CHANGED", reason)
            self._apply_signature(conn, user, "asset", asset["id"], "approved", self._asset_core(updated))
        return self.get_asset(user, tag)

    # ------------------------------------------------------------------
    # Fitness for use
    # ------------------------------------------------------------------

    def _release_blockers(self, conn, asset: dict, on: Optional[date] = None) -> List[str]:
        on = on or clock.today()
        blockers = []
        if asset["gmp_impact"] != "none" and asset["qualification_status"] != "qualified":
            blockers.append(f"Qualification status is {asset['qualification_status']}")
        if asset["calibration_required"]:
            if not asset["calibration_due_on"]:
                blockers.append("No approved calibration on record")
            elif date.fromisoformat(asset["calibration_due_on"]) < on:
                blockers.append(f"Calibration overdue since {asset['calibration_due_on']}")
        for sch in conn.execute(
                "SELECT title, next_due_on FROM pm_schedules WHERE asset_id = ? AND active = 1 AND trigger_type = 'time' "
                "AND blocks_use = 1 AND next_due_on < ? ORDER BY next_due_on", (asset["id"], on.isoformat())):
            blockers.append(f"{sch['title']} overdue since {sch['next_due_on']}")
        for wo in conn.execute(
                "SELECT number, title FROM work_orders WHERE asset_id = ? AND blocks_use = 1 AND trigger != 'schedule' "
                "AND (status = 'open' OR approval_state = 'pending') ORDER BY id", (asset["id"],)):
            blockers.append(f"Work order {wo['number']} ({wo['title']}) must be completed before use")
        open_devs = [r["number"] for r in conn.execute(
            "SELECT number FROM deviations WHERE asset_id = ? AND status = 'open'", (asset["id"],))]
        if open_devs:
            blockers.append(f"Open deviation(s): {', '.join(open_devs)}")
        return blockers

    def _fitness(self, conn, asset: dict) -> dict:
        today = clock.today()
        reasons = []
        if asset["status"] != "in_service":
            reasons.append(f"Status is {asset['status'].replace('_', ' ')}")
        reasons += self._release_blockers(conn, asset, today)
        warnings = []
        for label, field in (("Calibration", "calibration_due_on"), ("Preventive maintenance", "pm_due_on")):
            due = asset.get(field)
            if due and today <= date.fromisoformat(due) <= today + timedelta(days=14):
                warnings.append(f"{label} due on {due}")
        return {"fit_for_use": not reasons, "reasons": reasons, "warnings": warnings, "checked_at": clock.now_iso()}

    def fitness(self, user: dict, tag: str) -> dict:
        self.require(user, "asset.view")
        with self.db.read() as conn:
            asset = self._asset(conn, tag)
            return dict(self._fitness(conn, asset), asset_tag=asset["asset_tag"])

    # ------------------------------------------------------------------
    # Barcode scanning
    # ------------------------------------------------------------------

    def scan(self, user: dict, raw: str) -> dict:
        self.require(user, "asset.view")
        parsed = parse_barcode(raw, self.settings.gs1_separators)
        result: Dict[str, Any] = {"parsed": parsed.to_dict(), "match_type": "unknown"}
        entity_id = None
        with self.db.read() as conn:
            asset = location = None
            if parsed.valid and parsed.is_gs1:
                if parsed.giai:
                    asset = conn.execute(self._ASSET_SELECT + "WHERE a.asset_tag = ?", (parsed.giai.upper(),)).fetchone()
                if asset is None and parsed.gtin and parsed.serial:
                    asset = conn.execute(self._ASSET_SELECT + "WHERE a.gtin = ? AND a.serial_number = ?",
                                         (parsed.gtin, parsed.serial)).fetchone()
            elif parsed.valid:
                key = parsed.data.strip().upper()
                asset = conn.execute(self._ASSET_SELECT + "WHERE a.asset_tag = ?", (key,)).fetchone()
                if asset is None:
                    location = conn.execute("SELECT * FROM locations WHERE code = ?", (key,)).fetchone()
            if asset is not None:
                asset = dict(asset)
                result.update(match_type="asset", asset=asset, fitness=self._fitness(conn, asset))
                entity_id = asset["id"]
            elif location is not None:
                location = dict(location)
                count = conn.execute("SELECT COUNT(*) FROM assets WHERE location_id = ? AND status != 'retired'",
                                     (location["id"],)).fetchone()[0]
                result.update(match_type="location", location=location, asset_count=count)
                entity_id = location["id"]
            elif parsed.valid and parsed.is_gs1:
                result["match_type"] = "gs1_item"
                if parsed.expiry:
                    result["expired"] = parsed.expiry < clock.today()
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO scan_events (scanned_at, username, raw, match_type, entity_id) VALUES (?, ?, ?, ?, ?)",
                         (clock.now_iso(), user["username"], (raw or "")[:200], result["match_type"], entity_id))
        return result

    def asset_label_svg(self, user: dict, tag: str) -> str:
        self.require(user, "asset.view")
        with self.db.read() as conn:
            asset = self._asset(conn, tag)
        company = self.get_company()
        lines = [asset["description"][:48]]
        if company:
            lines.append(f"{company['name']} - {company['site_name']}"[:48])
        if asset["calibration_required"]:
            lines.append(f"Cal due: {asset['calibration_due_on'] or 'NOT CALIBRATED'}")
        return code128_svg(asset["asset_tag"], extra_lines=lines)

    def location_label_svg(self, user: dict, code: str) -> str:
        self.require(user, "asset.view")
        with self.db.read() as conn:
            loc = conn.execute("SELECT * FROM locations WHERE code = ?", ((code or "").upper(),)).fetchone()
        if loc is None:
            raise NotFound(f"Location {code} not found")
        return code128_svg(loc["code"], extra_lines=[f"LOCATION: {loc['name'][:40]}"])

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------

    def _calibrations(self, conn, asset_id: int) -> List[dict]:
        records = [dict(r) for r in conn.execute(
            "SELECT * FROM calibration_records WHERE asset_id = ? ORDER BY performed_on DESC, id DESC", (asset_id,))]
        for rec in records:
            rec["standards_used"] = json.loads(rec["standards_used"])
            rec["signatures"] = self._signatures(conn, "calibration", rec["id"])
        return records

    def _open_deviation(self, conn, user: dict, asset: dict, source: str, description: str) -> dict:
        year = clock.today().year
        n = conn.execute("SELECT COUNT(*) FROM deviations WHERE number LIKE ?", (f"DEV-{year}-%",)).fetchone()[0] + 1
        dev = {"number": f"DEV-{year}-{n:04d}", "asset_id": asset["id"], "source": source, "description": description,
               "status": "open", "opened_at": clock.now_iso(), "opened_by": user["username"]}
        cur = conn.execute(
            "INSERT INTO deviations (number, asset_id, source, description, status, opened_at, opened_by) "
            "VALUES (:number, :asset_id, :source, :description, :status, :opened_at, :opened_by)", dev)
        dev["id"] = cur.lastrowid
        audit.record(conn, username=user["username"], action="DEVIATION_OPENED", entity_type="deviation",
                     entity_id=dev["number"], new=dev, reason=source)
        return dev

    def record_calibration(self, user: dict, tag: str, *, performed_on: str, as_found: str, as_left: str,
                           standards: Iterable[str] = (), certificate_ref: Optional[str] = None,
                           notes: Optional[str] = None, password: Optional[str] = None) -> dict:
        self.require(user, "calibration.record")
        performed = _parse_date(performed_on, "performed_on")
        if performed > clock.today():
            raise ValidationFailed("Calibration date cannot be in the future")
        if as_found not in ("pass", "fail", "not_applicable") or as_left not in ("pass", "fail"):
            raise ValidationFailed("as_found must be pass/fail/not_applicable and as_left must be pass/fail")
        standards = [s.strip().upper() for s in standards if s and s.strip()]
        if not standards and not (certificate_ref or "").strip():
            raise ValidationFailed("Provide the reference standard(s) used, or an external calibration certificate reference")

        with self.db.read() as conn:
            asset = self._asset(conn, tag)
            self._validate_calibration(conn, asset, performed, standards)
        self._verify_signer(user, password, "performed")

        with self.db.transaction() as conn:
            asset = self._asset(conn, tag)
            self._validate_calibration(conn, asset, performed, standards)
            record = {
                "asset_id": asset["id"], "performed_on": performed.isoformat(), "performed_by": user["username"],
                "as_found": as_found, "as_left": as_left, "standards_used": json.dumps(standards),
                "certificate_ref": certificate_ref, "notes": notes, "status": "pending_approval",
                "recorded_at": clock.now_iso(),
            }
            cur = conn.execute(
                "INSERT INTO calibration_records (asset_id, performed_on, performed_by, as_found, as_left, standards_used, "
                "certificate_ref, notes, status, recorded_at) VALUES (:asset_id, :performed_on, :performed_by, :as_found, "
                ":as_left, :standards_used, :certificate_ref, :notes, :status, :recorded_at)", record)
            record["id"] = cal_id = cur.lastrowid
            audit.record(conn, username=user["username"], action="CALIBRATION_RECORDED", entity_type="calibration",
                         entity_id=cal_id, new=dict(record, asset_tag=asset["asset_tag"]))
            self._apply_signature(conn, user, "calibration", cal_id, "performed", record)

            if "fail" in (as_found, as_left):
                which = "as-found" if as_found == "fail" else "as-left"
                description = (
                    f"Out-of-tolerance ({which}) calibration result for {asset['asset_tag']} on {performed.isoformat()}. "
                    f"Assess impact on product/results since last successful calibration "
                    f"({asset['last_calibrated_on'] or 'none on record'})."
                )
                dev = self._open_deviation(conn, user, asset, "Calibration out of tolerance", description)
                conn.execute("UPDATE calibration_records SET deviation_id = ? WHERE id = ?", (dev["id"], cal_id))
                if asset["status"] not in ("quarantine", "retired"):
                    self._set_asset_fields(conn, user, asset, {"status": "quarantine"}, "STATUS_CHANGED",
                                           f"Automatic quarantine: {dev['number']} out-of-tolerance calibration")
                    self._fire_event(conn, user, asset, "asset_quarantined", dev["number"])
                self._fire_event(conn, user, asset, "calibration_failed", dev["number"])
        return self.get_calibration(user, cal_id)

    def _validate_calibration(self, conn, asset: dict, performed: date, standards: List[str]) -> None:
        if not asset["calibration_required"]:
            raise ValidationFailed(f"Asset {asset['asset_tag']} is not under calibration control")
        if asset["status"] == "retired":
            raise Conflict("Retired assets cannot be calibrated")
        for std_tag in standards:
            if std_tag == asset["asset_tag"]:
                raise ValidationFailed("An asset cannot be its own reference standard")
            std = conn.execute("SELECT * FROM assets WHERE asset_tag = ?", (std_tag,)).fetchone()
            if std is None:
                raise ValidationFailed(f"Reference standard {std_tag} not found")
            if not std["is_reference_standard"]:
                raise ValidationFailed(f"{std_tag} is not registered as a reference standard")
            if std["status"] != "in_service":
                raise ValidationFailed(f"Reference standard {std_tag} is not in service ({std['status']})")
            if not std["calibration_due_on"] or date.fromisoformat(std["calibration_due_on"]) < performed:
                raise ValidationFailed(f"Reference standard {std_tag} was not within calibration on {performed.isoformat()}")

    def get_calibration(self, user: dict, cal_id: int) -> dict:
        self.require(user, "asset.view")
        with self.db.read() as conn:
            rec = conn.execute(
                "SELECT c.*, a.asset_tag, a.description AS asset_description, d.number AS deviation_number "
                "FROM calibration_records c JOIN assets a ON a.id = c.asset_id "
                "LEFT JOIN deviations d ON d.id = c.deviation_id WHERE c.id = ?", (cal_id,)).fetchone()
            if rec is None:
                raise NotFound("Calibration record not found")
            rec = dict(rec)
            rec["standards_used"] = json.loads(rec["standards_used"])
            rec["signatures"] = self._signatures(conn, "calibration", cal_id)
            return rec

    def list_calibrations(self, user: dict, status: Optional[str] = None) -> List[dict]:
        self.require(user, "asset.view")
        with self.db.read() as conn:
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM calibration_records WHERE (? IS NULL OR status = ?) ORDER BY id DESC", (status, status))]
        return [self.get_calibration(user, i) for i in ids]

    def review_calibration(self, user: dict, cal_id: int, decision: str, comment: Optional[str], password: Optional[str]) -> dict:
        self.require(user, "calibration.approve")
        if decision not in ("approve", "reject"):
            raise ValidationFailed("decision must be approve or reject")
        if decision == "reject":
            _require_reason(comment)
        rec = self.get_calibration(user, cal_id)
        if rec["status"] != "pending_approval":
            raise Conflict(f"Calibration record is already {rec['status']}")
        if rec["performed_by"].lower() == user["username"].lower():
            raise Forbidden("Segregation of duties: you cannot approve a calibration you performed")
        meaning = "approved" if decision == "approve" else "rejected"
        self._verify_signer(user, password, meaning)
        with self.db.transaction() as conn:
            current = conn.execute("SELECT status FROM calibration_records WHERE id = ?", (cal_id,)).fetchone()
            if current["status"] != "pending_approval":
                raise Conflict(f"Calibration record is already {current['status']}")
            new_status = "approved" if decision == "approve" else "rejected"
            now = clock.now_iso()
            conn.execute("UPDATE calibration_records SET status = ?, reviewed_by = ?, reviewed_at = ?, review_comment = ? WHERE id = ?",
                         (new_status, user["username"], now, comment, cal_id))
            audit.record(conn, username=user["username"], action=f"CALIBRATION_{new_status.upper()}", entity_type="calibration",
                         entity_id=cal_id, old={"status": "pending_approval"},
                         new={"status": new_status, "reviewed_by": user["username"], "reviewed_at": now}, reason=comment)
            signed = dict(conn.execute("SELECT * FROM calibration_records WHERE id = ?", (cal_id,)).fetchone())
            self._apply_signature(conn, user, "calibration", cal_id, meaning, signed)
            if new_status == "approved" and rec["as_left"] == "pass":
                asset = self._asset(conn, rec["asset_tag"])
                if not asset["last_calibrated_on"] or rec["performed_on"] >= asset["last_calibrated_on"]:
                    self._set_asset_fields(conn, user, asset, {
                        "last_calibrated_on": rec["performed_on"],
                        "calibration_due_on": _plus_days(rec["performed_on"], asset["calibration_interval_days"]),
                    }, "CALIBRATION_STATUS_UPDATED", f"Approved calibration record #{cal_id}")
        return self.get_calibration(user, cal_id)

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def record_maintenance(self, user: dict, tag: str, *, maintenance_type: str, performed_on: str, description: str,
                           requalification_required: bool = False, password: Optional[str] = None) -> dict:
        """Record ad-hoc maintenance. Planned work is completed through work orders."""
        self.require(user, "maintenance.record")
        if maintenance_type not in ("preventive", "corrective"):
            raise ValidationFailed("maintenance_type must be preventive or corrective")
        performed = self._validate_maintenance_input(performed_on, description)
        with self.db.read() as conn:
            if self._asset(conn, tag)["status"] == "retired":
                raise Conflict("Retired assets cannot be maintained")
        self._verify_signer(user, password, "performed")
        with self.db.transaction() as conn:
            asset = self._asset(conn, tag)
            self._insert_maintenance(conn, user, asset, maintenance_type, performed, description.strip(), requalification_required)
        return self.get_asset(user, tag)

    @staticmethod
    def _validate_maintenance_input(performed_on: str, description: str) -> date:
        performed = _parse_date(performed_on, "performed_on")
        if performed > clock.today():
            raise ValidationFailed("Maintenance date cannot be in the future")
        if not description or len(description.strip()) < 5:
            raise ValidationFailed("A description of the work performed is required")
        return performed

    def _insert_maintenance(self, conn, user: dict, asset: dict, maintenance_type: str, performed: date,
                            description: str, requalification_required: bool) -> int:
        record = {
            "asset_id": asset["id"], "maintenance_type": maintenance_type, "performed_on": performed.isoformat(),
            "performed_by": user["username"], "description": description,
            "requalification_required": int(bool(requalification_required)), "recorded_at": clock.now_iso(),
        }
        cur = conn.execute(
            "INSERT INTO maintenance_records (asset_id, maintenance_type, performed_on, performed_by, description, "
            "requalification_required, recorded_at) VALUES (:asset_id, :maintenance_type, :performed_on, :performed_by, "
            ":description, :requalification_required, :recorded_at)", record)
        record["id"] = cur.lastrowid
        audit.record(conn, username=user["username"], action="MAINTENANCE_RECORDED", entity_type="maintenance",
                     entity_id=record["id"], new=dict(record, asset_tag=asset["asset_tag"]))
        self._apply_signature(conn, user, "maintenance", record["id"], "performed", record)
        fields: Dict[str, Any] = {}
        if maintenance_type == "preventive" and (not asset["last_pm_on"] or record["performed_on"] >= asset["last_pm_on"]):
            fields["last_pm_on"] = record["performed_on"]
        if requalification_required and asset["gmp_impact"] != "none":
            fields["qualification_status"] = "requalification_required"
        if fields:
            asset = self._set_asset_fields(conn, user, asset, fields, "MAINTENANCE_STATUS_UPDATED",
                                           f"Maintenance record #{record['id']}")
        if maintenance_type == "corrective":
            self._fire_event(conn, user, asset, "corrective_maintenance", f"maintenance record #{record['id']}")
        return record["id"]

    # ------------------------------------------------------------------
    # PM schedules, event triggers and work orders
    # ------------------------------------------------------------------

    def _validate_schedule(self, data: dict) -> dict:
        clean = {
            "title": (data.get("title") or "").strip(),
            "instructions": (data.get("instructions") or "").strip() or None,
            "work_type": data.get("work_type") or "preventive",
            "trigger_type": data.get("trigger_type") or "time",
            "interval_value": data.get("interval_value"),
            "interval_unit": data.get("interval_unit") or "days",
            "event": data.get("event") or None,
            "lead_days": data.get("lead_days") or 0,
            "blocks_use": int(bool(data.get("blocks_use", True))),
            "default_assignee": data.get("default_assignee") or None,
            "requires_approval": int(bool(data.get("requires_approval", False))),
        }
        if len(clean["title"]) < 3:
            raise ValidationFailed("A title is required (e.g. 'Quarterly PM', 'Post-move verification')")
        if clean["work_type"] not in WORK_TYPES:
            raise ValidationFailed(f"work_type must be one of {', '.join(WORK_TYPES)}")
        if clean["trigger_type"] not in ("time", "event"):
            raise ValidationFailed("trigger_type must be time or event")
        if clean["interval_unit"] not in INTERVAL_UNITS:
            raise ValidationFailed(f"interval_unit must be one of {', '.join(INTERVAL_UNITS)}")
        if not isinstance(clean["interval_value"], int) or not 1 <= clean["interval_value"] <= 3650:
            raise ValidationFailed("interval_value must be a whole number from 1 to 3650")
        if not isinstance(clean["lead_days"], int) or not 0 <= clean["lead_days"] <= 365:
            raise ValidationFailed("lead_days must be 0-365")
        if clean["trigger_type"] == "event":
            if clean["event"] not in PM_EVENTS:
                raise ValidationFailed(f"event must be one of {', '.join(PM_EVENTS)}")
        else:
            clean["event"] = None
        return clean

    def _insert_schedule(self, conn, user: dict, asset: dict, data: dict, first_due_on: Optional[str] = None) -> dict:
        sch = self._validate_schedule(data)
        if sch["default_assignee"]:
            self._check_assignee(conn, sch["default_assignee"])
        if sch["trigger_type"] == "time":
            first = _parse_date(first_due_on, "first_due_on") if first_due_on else \
                _add_interval(clock.today(), sch["interval_value"], sch["interval_unit"])
            sch["next_due_on"] = first.isoformat()
        else:
            sch["next_due_on"] = None
        sch.update(asset_id=asset["id"], created_at=clock.now_iso(), created_by=user["username"])
        cur = conn.execute(
            "INSERT INTO pm_schedules (asset_id, title, instructions, work_type, trigger_type, interval_value, interval_unit, "
            "event, lead_days, blocks_use, default_assignee, requires_approval, next_due_on, created_at, created_by) "
            "VALUES (:asset_id, :title, :instructions, :work_type, :trigger_type, :interval_value, :interval_unit, :event, "
            ":lead_days, :blocks_use, :default_assignee, :requires_approval, :next_due_on, :created_at, :created_by)", sch)
        sch["id"] = cur.lastrowid
        audit.record(conn, username=user["username"], action="PM_SCHEDULE_CREATED", entity_type="pm_schedule",
                     entity_id=sch["id"], new=dict(sch, asset_tag=asset["asset_tag"]))
        self._sync_pm_due(conn, user, asset["id"])
        return sch

    def _sync_pm_due(self, conn, user: dict, asset_id: int) -> None:
        """Keep the asset's PM summary (pm_required / pm_due_on) in line with its time-based schedules."""
        row = conn.execute("SELECT COUNT(*) AS n, MIN(next_due_on) AS due FROM pm_schedules "
                           "WHERE asset_id = ? AND active = 1 AND trigger_type = 'time'", (asset_id,)).fetchone()
        asset = dict(conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone())
        fields = {"pm_required": int(row["n"] > 0), "pm_due_on": row["due"]}
        changed = {k: v for k, v in fields.items() if asset[k] != v}
        if changed:
            self._set_asset_fields(conn, user, asset, changed, "PM_SUMMARY_UPDATED", "PM schedule change")

    def create_pm_schedule(self, user: dict, tag: str, data: dict) -> dict:
        self.require(user, "maintenance.plan")
        with self.db.transaction() as conn:
            asset = self._asset(conn, tag)
            if asset["status"] == "retired":
                raise Conflict("Retired assets cannot have maintenance plans")
            sch = self._insert_schedule(conn, user, asset, data, data.get("first_due_on"))
        self.generate_due_work_orders()
        return self._schedule(sch["id"])

    def update_pm_schedule(self, user: dict, schedule_id: int, changes: dict, reason: str) -> dict:
        self.require(user, "maintenance.plan")
        reason = _require_reason(reason)
        allowed = {"title", "instructions", "interval_value", "interval_unit", "lead_days", "blocks_use", "active", "next_due_on",
                   "default_assignee", "requires_approval"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationFailed(f"Fields not editable: {', '.join(sorted(unknown))}")
        with self.db.transaction() as conn:
            old = _row(conn.execute("SELECT * FROM pm_schedules WHERE id = ?", (schedule_id,)).fetchone())
            if old is None:
                raise NotFound("PM schedule not found")
            merged = dict(old, **changes)
            self._validate_schedule(merged)
            merged["active"] = int(bool(merged["active"]))
            merged["blocks_use"] = int(bool(merged["blocks_use"]))
            merged["requires_approval"] = int(bool(merged["requires_approval"]))
            merged["default_assignee"] = merged["default_assignee"] or None
            if merged["default_assignee"] and "default_assignee" in changes:
                self._check_assignee(conn, merged["default_assignee"])
            if merged["trigger_type"] == "time":
                if "next_due_on" in changes:
                    merged["next_due_on"] = _parse_date(changes["next_due_on"], "next_due_on").isoformat()
                elif {"interval_value", "interval_unit"} & set(changes) and old["last_completed_on"]:
                    merged["next_due_on"] = _add_interval(date.fromisoformat(old["last_completed_on"]),
                                                          merged["interval_value"], merged["interval_unit"]).isoformat()
            diff = {k: merged[k] for k in allowed if merged[k] != old[k]}
            if not diff:
                raise ValidationFailed("No changes")
            conn.execute(f"UPDATE pm_schedules SET {', '.join(f'{k} = ?' for k in diff)} WHERE id = ?",
                         (*diff.values(), schedule_id))
            audit.record(conn, username=user["username"], action="PM_SCHEDULE_MODIFIED", entity_type="pm_schedule",
                         entity_id=schedule_id, old={k: old[k] for k in diff}, new=diff, reason=reason)
            self._sync_pm_due(conn, user, old["asset_id"])
        return self._schedule(schedule_id)

    def _schedule(self, schedule_id: int) -> dict:
        with self.db.read() as conn:
            row = conn.execute("SELECT s.*, a.asset_tag FROM pm_schedules s JOIN assets a ON a.id = s.asset_id "
                               "WHERE s.id = ?", (schedule_id,)).fetchone()
        return dict(row)

    def _create_work_order(self, conn, user: dict, asset: dict, *, work_type: str, title: str, instructions: Optional[str],
                           due_on: str, trigger: str, blocks_use: bool, schedule_id: Optional[int] = None,
                           reason: Optional[str] = None, assigned_to: Optional[int] = None,
                           requires_approval: bool = False) -> dict:
        year = clock.today().year
        n = conn.execute("SELECT COUNT(*) FROM work_orders WHERE number LIKE ?", (f"WO-{year}-%",)).fetchone()[0] + 1
        wo = {
            "number": f"WO-{year}-{n:04d}", "asset_id": asset["id"], "schedule_id": schedule_id, "work_type": work_type,
            "title": title, "instructions": instructions, "trigger": trigger, "due_on": due_on,
            "blocks_use": int(bool(blocks_use)), "status": "open", "created_at": clock.now_iso(),
            "created_by": user["username"], "assigned_to": assigned_to, "requires_approval": int(bool(requires_approval)),
        }
        cur = conn.execute(
            "INSERT INTO work_orders (number, asset_id, schedule_id, work_type, title, instructions, trigger, due_on, "
            "blocks_use, status, created_at, created_by, assigned_to, requires_approval) VALUES (:number, :asset_id, "
            ":schedule_id, :work_type, :title, :instructions, :trigger, :due_on, :blocks_use, :status, :created_at, "
            ":created_by, :assigned_to, :requires_approval)", wo)
        wo["id"] = cur.lastrowid
        audit.record(conn, username=user["username"], action="WORK_ORDER_CREATED", entity_type="work_order",
                     entity_id=wo["number"], new=dict(wo, asset_tag=asset["asset_tag"]), reason=reason)
        if assigned_to:
            self._notify_assignment(conn, wo, asset)
        return wo

    def _notify_assignment(self, conn, wo: dict, asset: dict) -> None:
        self._notify(conn, wo["assigned_to"], f"[GMP] Work order {wo['number']} assigned to you: {wo['title']}",
                     f"Work order {wo['number']} has been assigned to you.\n\n"
                     f"Asset: {asset['asset_tag']} - {asset['description']}\nTask: {wo['title']} ({wo['work_type']})\n"
                     f"Due: {wo['due_on']}\n{wo.get('instructions') or ''}\n\nOpen: {self.settings.base_url}/")

    def _fire_event(self, conn, user: dict, asset: dict, event: str, detail: str = "") -> List[dict]:
        """Create work orders for every active event-triggered schedule listening for ``event``."""
        created = []
        for sch in conn.execute("SELECT * FROM pm_schedules WHERE asset_id = ? AND active = 1 AND trigger_type = 'event' "
                                "AND event = ?", (asset["id"], event)).fetchall():
            due = _add_interval(clock.today(), sch["interval_value"], sch["interval_unit"]).isoformat()
            created.append(self._create_work_order(
                conn, user, asset, work_type=sch["work_type"], title=sch["title"], instructions=sch["instructions"],
                due_on=due, trigger=f"event:{event}", blocks_use=sch["blocks_use"], schedule_id=sch["id"],
                reason=f"Triggered by event '{PM_EVENTS[event]}'" + (f": {detail}" if detail else ""),
                assigned_to=sch["default_assignee"], requires_approval=sch["requires_approval"]))
        return created

    def generate_due_work_orders(self) -> List[str]:
        """Raise work orders for time-based schedules entering their lead window.
        Safe to call repeatedly: one open work order per schedule at a time."""
        today = clock.today()
        system = {"username": "system", "full_name": "System"}
        created = []
        with self.db.transaction() as conn:
            for sch in conn.execute(
                    "SELECT s.* FROM pm_schedules s JOIN assets a ON a.id = s.asset_id "
                    "WHERE s.active = 1 AND s.trigger_type = 'time' AND a.status != 'retired' "
                    "AND NOT EXISTS (SELECT 1 FROM work_orders w WHERE w.schedule_id = s.id AND w.status = 'open')").fetchall():
                if date.fromisoformat(sch["next_due_on"]) - timedelta(days=sch["lead_days"]) > today:
                    continue
                asset = dict(conn.execute("SELECT * FROM assets WHERE id = ?", (sch["asset_id"],)).fetchone())
                wo = self._create_work_order(
                    conn, system, asset, work_type=sch["work_type"], title=sch["title"], instructions=sch["instructions"],
                    due_on=sch["next_due_on"], trigger="schedule", blocks_use=sch["blocks_use"], schedule_id=sch["id"],
                    reason=f"Scheduled every {sch['interval_value']} {sch['interval_unit']}",
                    assigned_to=sch["default_assignee"], requires_approval=sch["requires_approval"])
                created.append(wo["number"])
        if created:
            notifications.send_in_background(self.db, self.settings)
        return created

    def create_work_order(self, user: dict, tag: str, *, work_type: str, title: str, instructions: Optional[str],
                          due_on: str, blocks_use: bool = False, assigned_to: Optional[int] = None,
                          requires_approval: bool = False) -> dict:
        self.require(user, "maintenance.plan")
        if work_type not in WORK_TYPES:
            raise ValidationFailed(f"work_type must be one of {', '.join(WORK_TYPES)}")
        if not title or len(title.strip()) < 3:
            raise ValidationFailed("A title is required")
        due = _parse_date(due_on, "due_on")
        with self.db.transaction() as conn:
            asset = self._asset(conn, tag)
            if asset["status"] == "retired":
                raise Conflict("Retired assets cannot have work orders")
            if assigned_to:
                self._check_assignee(conn, assigned_to)
            wo = self._create_work_order(conn, user, asset, work_type=work_type, title=title.strip(),
                                         instructions=(instructions or "").strip() or None, due_on=due.isoformat(),
                                         trigger="manual", blocks_use=blocks_use, assigned_to=assigned_to or None,
                                         requires_approval=requires_approval)
        notifications.send_in_background(self.db, self.settings)
        return self.get_work_order(user, wo["number"])

    def assign_work_order(self, user: dict, number: str, assignee_id: Optional[int], reason: Optional[str] = None) -> dict:
        self.require(user, "maintenance.plan")
        with self.db.transaction() as conn:
            wo = _row(conn.execute("SELECT * FROM work_orders WHERE number = ?", (number,)).fetchone())
            if wo is None:
                raise NotFound(f"Work order {number} not found")
            if wo["status"] != "open":
                raise Conflict(f"Work order is already {wo['status']}")
            if assignee_id:
                self._check_assignee(conn, assignee_id)
            if wo["assigned_to"] == (assignee_id or None):
                raise ValidationFailed("Work order is already assigned to this person")
            conn.execute("UPDATE work_orders SET assigned_to = ? WHERE id = ?", (assignee_id or None, wo["id"]))
            audit.record(conn, username=user["username"], action="WORK_ORDER_ASSIGNED", entity_type="work_order",
                         entity_id=number, old={"assigned_to": wo["assigned_to"]}, new={"assigned_to": assignee_id or None},
                         reason=(reason or "").strip() or None)
            if assignee_id:
                asset = dict(conn.execute("SELECT * FROM assets WHERE id = ?", (wo["asset_id"],)).fetchone())
                self._notify_assignment(conn, dict(wo, assigned_to=assignee_id), asset)
        notifications.send_in_background(self.db, self.settings)
        return self.get_work_order(user, number)

    _WO_SELECT = ("SELECT w.*, a.asset_tag, a.description AS asset_description, l.code AS location_code, "
                  "u.full_name AS assignee_name, u.username AS assignee_username "
                  "FROM work_orders w JOIN assets a ON a.id = w.asset_id LEFT JOIN locations l ON l.id = a.location_id "
                  "LEFT JOIN users u ON u.id = w.assigned_to ")

    def _decorate_wo(self, conn, wo: dict) -> dict:
        wo["overdue"] = wo["status"] == "open" and wo["due_on"] < clock.today().isoformat()
        wo["signatures"] = self._signatures(conn, "maintenance", wo["maintenance_record_id"]) if wo["maintenance_record_id"] else []
        approval = conn.execute(
            "SELECT r.number, r.status, r.decision_comment, u.full_name AS approver_name FROM approval_requests r "
            "JOIN users u ON u.id = r.approver_id WHERE r.work_order_id = ? ORDER BY r.id DESC LIMIT 1", (wo["id"],)).fetchone()
        wo["approval"] = dict(approval) if approval else None
        return wo

    def get_work_order(self, user: dict, number: str) -> dict:
        self.require(user, "asset.view")
        with self.db.read() as conn:
            row = conn.execute(self._WO_SELECT + "WHERE w.number = ?", (number,)).fetchone()
            if row is None:
                raise NotFound(f"Work order {number} not found")
            return self._decorate_wo(conn, dict(row))

    def list_work_orders(self, user: dict, status: Optional[str] = None, tag: Optional[str] = None,
                         assigned_to: Optional[int] = None) -> List[dict]:
        self.require(user, "asset.view")
        self.generate_due_work_orders()
        sql, args = self._WO_SELECT + "WHERE 1=1 ", []
        if assigned_to:
            sql += "AND w.assigned_to = ? "
            args.append(assigned_to)
        if status:
            sql += "AND w.status = ? "
            args.append(status)
        if tag:
            sql += "AND a.asset_tag = ? "
            args.append(tag.upper())
        with self.db.read() as conn:
            return [self._decorate_wo(conn, dict(r)) for r in conn.execute(sql + "ORDER BY w.status = 'open' DESC, w.due_on, w.id", args)]

    def complete_work_order(self, user: dict, number: str, *, performed_on: str, notes: str,
                            requalification_required: bool = False, password: Optional[str] = None,
                            approver_id: Optional[int] = None) -> dict:
        self.require(user, "maintenance.record")
        performed = self._validate_maintenance_input(performed_on, notes)
        wo = self.get_work_order(user, number)
        if wo["status"] != "open":
            raise Conflict(f"Work order is already {wo['status']}")
        if wo["assigned_to"] and wo["assigned_to"] != user["id"]:
            raise Forbidden(f"Work order is assigned to {wo['assignee_name']} - ask a planner to reassign it")
        if wo["requires_approval"]:
            if not approver_id:
                raise ValidationFailed("This work order needs QA approval - choose an approver")
            with self.db.read() as conn:
                self._check_approver(conn, approver_id, user["id"])
        self._verify_signer(user, password, "performed")
        with self.db.transaction() as conn:
            current = conn.execute("SELECT status FROM work_orders WHERE number = ?", (number,)).fetchone()
            if current["status"] != "open":
                raise Conflict(f"Work order is already {current['status']}")
            asset = self._asset(conn, wo["asset_tag"])
            maintenance_type = "corrective" if wo["work_type"] == "corrective" else "preventive"
            record_id = self._insert_maintenance(conn, user, asset, maintenance_type, performed,
                                                 f"[{number} {wo['work_type']}] {wo['title']}: {notes.strip()}",
                                                 requalification_required)
            now = clock.now_iso()
            conn.execute("UPDATE work_orders SET status = 'completed', closed_at = ?, closed_by = ?, closure_notes = ?, "
                         "maintenance_record_id = ? WHERE number = ?", (now, user["username"], notes.strip(), record_id, number))
            audit.record(conn, username=user["username"], action="WORK_ORDER_COMPLETED", entity_type="work_order",
                         entity_id=number, old={"status": "open"},
                         new={"status": "completed", "closed_at": now, "maintenance_record_id": record_id})
            if wo["schedule_id"] and wo["trigger"] == "schedule":
                self._advance_schedule(conn, user, wo["schedule_id"], performed, f"Completed {number}", completed=True)
            if wo["requires_approval"]:
                conn.execute("UPDATE work_orders SET approval_state = 'pending' WHERE id = ?", (wo["id"],))
                approver = self._check_approver(conn, approver_id, user["id"])
                self._new_approval(conn, user, request_type="work_order", asset=asset, approver=approver,
                                   comment=notes.strip(), work_order_id=wo["id"],
                                   subject=f"{number} {wo['title']} completed - QA approval required")
        notifications.send_in_background(self.db, self.settings)
        return self.get_work_order(user, number)

    def _advance_schedule(self, conn, user: dict, schedule_id: int, base: date, reason: str, completed: bool) -> None:
        sch = dict(conn.execute("SELECT * FROM pm_schedules WHERE id = ?", (schedule_id,)).fetchone())
        next_due = _add_interval(base, sch["interval_value"], sch["interval_unit"]).isoformat()
        fields = {"next_due_on": next_due}
        if completed:
            fields["last_completed_on"] = base.isoformat()
        conn.execute(f"UPDATE pm_schedules SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), schedule_id))
        audit.record(conn, username=user["username"], action="PM_SCHEDULE_ADVANCED", entity_type="pm_schedule",
                     entity_id=schedule_id, old={k: sch[k] for k in fields}, new=fields, reason=reason)
        self._sync_pm_due(conn, user, sch["asset_id"])

    def cancel_work_order(self, user: dict, number: str, reason: str) -> dict:
        self.require(user, "maintenance.plan")
        reason = _require_reason(reason)
        with self.db.transaction() as conn:
            wo = _row(conn.execute("SELECT * FROM work_orders WHERE number = ?", (number,)).fetchone())
            if wo is None:
                raise NotFound(f"Work order {number} not found")
            if wo["status"] != "open":
                raise Conflict(f"Work order is already {wo['status']}")
            now = clock.now_iso()
            conn.execute("UPDATE work_orders SET status = 'cancelled', closed_at = ?, closed_by = ?, closure_notes = ? "
                         "WHERE number = ?", (now, user["username"], reason, number))
            audit.record(conn, username=user["username"], action="WORK_ORDER_CANCELLED", entity_type="work_order",
                         entity_id=number, old={"status": "open"}, new={"status": "cancelled"}, reason=reason)
            if wo["schedule_id"] and wo["trigger"] == "schedule":
                # skipping this occurrence: the next one is one interval after the skipped due date
                self._advance_schedule(conn, user, wo["schedule_id"], date.fromisoformat(wo["due_on"]), f"Skipped {number}: {reason}", completed=False)
        return self.get_work_order(user, number)

    # ------------------------------------------------------------------
    # People, approvals and notifications
    # ------------------------------------------------------------------

    def _notify(self, conn, user_id: int, subject: str, body: str) -> None:
        u = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.execute("INSERT INTO notifications (user_id, email, subject, body, created_at) VALUES (?, ?, ?, ?, ?)",
                     (user_id, u["email"] if u else None, subject, body, clock.now_iso()))

    @staticmethod
    def _active_user(conn, user_id: int) -> dict:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None or not row["active"] or row["locked"]:
            raise ValidationFailed("Selected person is not an active user")
        return dict(row)

    def _check_assignee(self, conn, user_id: int) -> dict:
        person = self._active_user(conn, user_id)
        if not has_permission(person["role"], "maintenance.record"):
            raise ValidationFailed(f"{person['full_name']} ({person['role']}) cannot perform maintenance work")
        return person

    def _check_approver(self, conn, user_id: int, requester_id: int) -> dict:
        person = self._active_user(conn, user_id)
        if not has_permission(person["role"], "asset.status.release"):
            raise ValidationFailed(f"{person['full_name']} is not a QA approver")
        if person["id"] == requester_id:
            raise ValidationFailed("You cannot be the approver of your own request")
        return person

    def list_people(self, user: dict, kind: str) -> List[dict]:
        """Active approvers (QA) or assignees (can perform maintenance), for pick lists."""
        self.require(user, "asset.view")
        permission = {"approvers": "asset.status.release", "assignees": "maintenance.record"}[kind]
        with self.db.read() as conn:
            rows = conn.execute("SELECT id, username, full_name, email, role FROM users WHERE active = 1 AND locked = 0 "
                                "ORDER BY full_name").fetchall()
        return [dict(r) for r in rows if has_permission(r["role"], permission)]

    _APPROVAL_SELECT = (
        "SELECT r.*, a.asset_tag, a.description AS asset_description, a.status AS asset_status, w.number AS work_order_number, "
        "w.title AS work_order_title, rq.full_name AS requested_by_name, ap.full_name AS approver_name, "
        "ap.email AS approver_email, dc.full_name AS decided_by_name "
        "FROM approval_requests r JOIN assets a ON a.id = r.asset_id LEFT JOIN work_orders w ON w.id = r.work_order_id "
        "JOIN users rq ON rq.id = r.requested_by JOIN users ap ON ap.id = r.approver_id "
        "LEFT JOIN users dc ON dc.id = r.decided_by ")

    def _new_approval(self, conn, user: dict, *, request_type: str, asset: dict, approver: dict, comment: Optional[str],
                      subject: str, work_order_id: Optional[int] = None) -> dict:
        year = clock.today().year
        n = conn.execute("SELECT COUNT(*) FROM approval_requests WHERE number LIKE ?", (f"APR-{year}-%",)).fetchone()[0] + 1
        req = {"number": f"APR-{year}-{n:04d}", "request_type": request_type, "asset_id": asset["id"],
               "work_order_id": work_order_id, "requested_by": user["id"], "requested_at": clock.now_iso(),
               "request_comment": (comment or "").strip() or None, "approver_id": approver["id"], "status": "pending"}
        cur = conn.execute(
            "INSERT INTO approval_requests (number, request_type, asset_id, work_order_id, requested_by, requested_at, "
            "request_comment, approver_id, status) VALUES (:number, :request_type, :asset_id, :work_order_id, :requested_by, "
            ":requested_at, :request_comment, :approver_id, :status)", req)
        req["id"] = cur.lastrowid
        audit.record(conn, username=user["username"], action="APPROVAL_REQUESTED", entity_type="approval",
                     entity_id=req["number"], new=dict(req, asset_tag=asset["asset_tag"], approver=approver["username"]))
        self._notify(conn, approver["id"], f"[GMP] Approval required ({req['number']}): {subject}",
                     f"{user['full_name']} has asked you to approve {req['number']}.\n\n"
                     f"Asset: {asset['asset_tag']} - {asset['description']}\n"
                     f"Comment: {req['request_comment'] or '-'}\n\n"
                     f"Review it under 'My tasks': {self.settings.base_url}/")
        return req

    def submit_induction(self, user: dict, tag: str, approver_id: int, comment: Optional[str]) -> dict:
        """Ask a QA approver to approve a new asset for use (induction)."""
        if not (has_permission(user["role"], "asset.create") or has_permission(user["role"], "asset.edit")):
            raise Forbidden("You are not permitted to submit assets for approval")
        with self.db.transaction() as conn:
            asset = self._asset(conn, tag)
            if asset["status"] != "commissioning":
                raise Conflict("Only new (Inactive - new) assets go through induction approval")
            if conn.execute("SELECT 1 FROM approval_requests WHERE asset_id = ? AND request_type = 'asset_induction' "
                            "AND status = 'pending'", (asset["id"],)).fetchone():
                raise Conflict("An induction approval is already pending for this asset")
            approver = self._check_approver(conn, approver_id, user["id"])
            req = self._new_approval(conn, user, request_type="asset_induction", asset=asset, approver=approver,
                                     comment=comment, subject=f"induction of {asset['asset_tag']} {asset['description']}")
        notifications.send_in_background(self.db, self.settings)
        return self.get_approval(user, req["number"])

    def get_approval(self, user: dict, number: str) -> dict:
        self.require(user, "asset.view")
        with self.db.read() as conn:
            row = conn.execute(self._APPROVAL_SELECT + "WHERE r.number = ?", (number,)).fetchone()
            if row is None:
                raise NotFound(f"Approval request {number} not found")
            req = dict(row)
            req["signatures"] = self._signatures(conn, "approval", req["id"])
            if req["request_type"] == "asset_induction" and req["status"] == "pending":
                asset = self._asset(conn, req["asset_tag"])
                req["blockers"] = self._release_blockers(conn, dict(asset, qualification_status="qualified"))
            return req

    def list_approvals(self, user: dict, *, approver_id: Optional[int] = None, requested_by: Optional[int] = None,
                       status: Optional[str] = None, tag: Optional[str] = None) -> List[dict]:
        self.require(user, "asset.view")
        sql, args = self._APPROVAL_SELECT + "WHERE 1=1 ", []
        for col, val in (("r.approver_id", approver_id), ("r.requested_by", requested_by), ("r.status", status),
                         ("a.asset_tag", tag.upper() if tag else None)):
            if val:
                sql += f"AND {col} = ? "
                args.append(val)
        with self.db.read() as conn:
            numbers = [r["number"] for r in conn.execute(sql + "ORDER BY r.status = 'pending' DESC, r.id DESC", args)]
        return [self.get_approval(user, n) for n in numbers]

    def decide_approval(self, user: dict, number: str, decision: str, comment: Optional[str], password: Optional[str]) -> dict:
        self.require(user, "asset.status.release")
        if decision not in ("approve", "reject"):
            raise ValidationFailed("decision must be approve or reject")
        if decision == "reject":
            comment = _require_reason(comment)
        req = self.get_approval(user, number)
        if req["status"] != "pending":
            raise Conflict(f"Request is already {req['status']}")
        if req["approver_id"] != user["id"]:
            raise Forbidden(f"This request is assigned to {req['approver_name']}")
        if req["requested_by"] == user["id"]:
            raise Forbidden("You cannot approve your own request")
        if decision == "approve" and req.get("blockers"):
            raise Conflict("Cannot approve yet: " + "; ".join(req["blockers"]))
        meaning = "approved" if decision == "approve" else "rejected"
        self._verify_signer(user, password, meaning)
        with self.db.transaction() as conn:
            current = conn.execute("SELECT status FROM approval_requests WHERE number = ?", (number,)).fetchone()
            if current["status"] != "pending":
                raise Conflict(f"Request is already {current['status']}")
            new_status = "approved" if decision == "approve" else "rejected"
            now = clock.now_iso()
            conn.execute("UPDATE approval_requests SET status = ?, decided_by = ?, decided_at = ?, decision_comment = ? "
                         "WHERE number = ?", (new_status, user["id"], now, (comment or "").strip() or None, number))
            audit.record(conn, username=user["username"], action=f"APPROVAL_{new_status.upper()}", entity_type="approval",
                         entity_id=number, old={"status": "pending"}, new={"status": new_status, "decided_at": now},
                         reason=comment)
            signed = dict(conn.execute("SELECT * FROM approval_requests WHERE number = ?", (number,)).fetchone())
            self._apply_signature(conn, user, "approval", req["id"], meaning, signed)
            asset = self._asset(conn, req["asset_tag"])
            if req["request_type"] == "asset_induction" and new_status == "approved":
                self._activate_after_induction(conn, user, asset, req, comment)
            elif req["request_type"] == "work_order":
                state = "approved" if new_status == "approved" else "rejected"
                fields = "approval_state = 'approved'" if state == "approved" else \
                    "approval_state = 'rejected', status = 'open', closed_at = NULL, closed_by = NULL"
                conn.execute(f"UPDATE work_orders SET {fields} WHERE id = ?", (req["work_order_id"],))
                audit.record(conn, username=user["username"], action=f"WORK_ORDER_{state.upper()}", entity_type="work_order",
                             entity_id=req["work_order_number"], new={"approval_state": state,
                                                                      **({"status": "open"} if state == "rejected" else {})},
                             reason=comment)
            outcome = "APPROVED" if new_status == "approved" else "REJECTED"
            self._notify(conn, req["requested_by"], f"[GMP] {number} {outcome}: {req['asset_tag']}",
                         f"{user['full_name']} {outcome.lower()} {number} for {req['asset_tag']} - {req['asset_description']}.\n"
                         f"Comment: {comment or '-'}\n"
                         + ("\nThe work order has been re-opened for rework.\n" if req["request_type"] == "work_order"
                            and new_status == "rejected" else "")
                         + f"\n{self.settings.base_url}/")
        notifications.send_in_background(self.db, self.settings)
        return self.get_approval(user, number)

    def _activate_after_induction(self, conn, user: dict, asset: dict, req: dict, comment: Optional[str]) -> None:
        if asset["status"] != "commissioning":
            raise Conflict(f"Asset is no longer new (status {asset['status']})")
        fields: Dict[str, Any] = {"status": "in_service"}
        if asset["gmp_impact"] != "none" and asset["qualification_status"] != "qualified":
            fields["qualification_status"] = "qualified"
        reason = f"Induction approved ({req['number']})" + (f": {comment}" if comment else "")
        updated = self._set_asset_fields(conn, user, asset, fields, "INDUCTION_APPROVED", reason)
        self._apply_signature(conn, user, "asset", asset["id"], "released", self._asset_core(updated))
        self._fire_event(conn, user, updated, "asset_activated", req["number"])

    def reassign_approval(self, user: dict, number: str, approver_id: int, reason: str) -> dict:
        reason = _require_reason(reason)
        req = self.get_approval(user, number)
        if req["status"] != "pending":
            raise Conflict(f"Request is already {req['status']}")
        if user["id"] not in (req["requested_by"], req["approver_id"]) and not has_permission(user["role"], "asset.status.release"):
            raise Forbidden("Only the requester, the approver or QA can reassign a request")
        with self.db.transaction() as conn:
            approver = self._check_approver(conn, approver_id, req["requested_by"])
            conn.execute("UPDATE approval_requests SET approver_id = ? WHERE number = ?", (approver["id"], number))
            audit.record(conn, username=user["username"], action="APPROVAL_REASSIGNED", entity_type="approval",
                         entity_id=number, old={"approver_id": req["approver_id"]}, new={"approver_id": approver["id"]},
                         reason=reason)
            self._notify(conn, approver["id"], f"[GMP] Approval required ({number}): {req['asset_tag']}",
                         f"{number} for {req['asset_tag']} - {req['asset_description']} has been reassigned to you.\n"
                         f"Reason: {reason}\n\n{self.settings.base_url}/")
        notifications.send_in_background(self.db, self.settings)
        return self.get_approval(user, number)

    def cancel_approval(self, user: dict, number: str, reason: str) -> dict:
        reason = _require_reason(reason)
        req = self.get_approval(user, number)
        if req["status"] != "pending":
            raise Conflict(f"Request is already {req['status']}")
        if req["request_type"] != "asset_induction":
            raise Conflict("Work order approvals cannot be withdrawn - ask the approver to reject it")
        if user["id"] != req["requested_by"] and not has_permission(user["role"], "asset.status.release"):
            raise Forbidden("Only the requester or QA can withdraw a request")
        with self.db.transaction() as conn:
            conn.execute("UPDATE approval_requests SET status = 'cancelled', decided_by = ?, decided_at = ?, "
                         "decision_comment = ? WHERE number = ?", (user["id"], clock.now_iso(), reason, number))
            audit.record(conn, username=user["username"], action="APPROVAL_CANCELLED", entity_type="approval",
                         entity_id=number, old={"status": "pending"}, new={"status": "cancelled"}, reason=reason)
        return self.get_approval(user, number)

    def my_tasks(self, user: dict) -> dict:
        self.require(user, "asset.view")
        return {
            "approvals": self.list_approvals(user, approver_id=user["id"], status="pending"),
            "work_orders": [w for w in self.list_work_orders(user, status="open", assigned_to=user["id"])],
            "unassigned_work_orders": [w for w in self.list_work_orders(user, status="open") if not w["assigned_to"]]
            if has_permission(user["role"], "maintenance.plan") else [],
            "my_requests": self.list_approvals(user, requested_by=user["id"], status="pending"),
        }

    def outbox(self, user: dict, limit: int = 100) -> List[dict]:
        self.require(user, "user.manage")
        with self.db.read() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))]

    # ------------------------------------------------------------------
    # Deviations
    # ------------------------------------------------------------------

    def list_deviations(self, user: dict, status: Optional[str] = None) -> List[dict]:
        self.require(user, "asset.view")
        with self.db.read() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT d.*, a.asset_tag FROM deviations d JOIN assets a ON a.id = d.asset_id "
                "WHERE (? IS NULL OR d.status = ?) ORDER BY d.id DESC", (status, status))]
            for r in rows:
                r["signatures"] = self._signatures(conn, "deviation", r["id"])
            return rows

    def report_issue(self, user: dict, tag: str, description: str) -> dict:
        """Operator/technician raises a deviation and takes the asset out of service."""
        self.require(user, "asset.status.remove")
        description = _require_reason(description)
        with self.db.transaction() as conn:
            asset = self._asset(conn, tag)
            if asset["status"] == "retired":
                raise Conflict("Asset is retired")
            dev = self._open_deviation(conn, user, asset, "Reported issue", description)
            if asset["status"] == "in_service":
                self._set_asset_fields(conn, user, asset, {"status": "out_of_service"}, "STATUS_CHANGED",
                                       f"{dev['number']}: {description}")
        return dev

    def close_deviation(self, user: dict, number: str, comment: str, password: Optional[str]) -> dict:
        self.require(user, "deviation.close")
        comment = _require_reason(comment)
        with self.db.read() as conn:
            dev = conn.execute("SELECT * FROM deviations WHERE number = ?", (number,)).fetchone()
            if dev is None:
                raise NotFound(f"Deviation {number} not found")
            if dev["status"] != "open":
                raise Conflict("Deviation is already closed")
        self._verify_signer(user, password, "approved")
        with self.db.transaction() as conn:
            now = clock.now_iso()
            cur = conn.execute("UPDATE deviations SET status = 'closed', closed_at = ?, closed_by = ?, closure_comment = ? "
                               "WHERE number = ? AND status = 'open'", (now, user["username"], comment, number))
            if cur.rowcount != 1:
                raise Conflict("Deviation is already closed")
            closed = dict(conn.execute("SELECT * FROM deviations WHERE number = ?", (number,)).fetchone())
            audit.record(conn, username=user["username"], action="DEVIATION_CLOSED", entity_type="deviation", entity_id=number,
                         old={"status": "open"}, new={"status": "closed", "closed_at": now, "closed_by": user["username"]},
                         reason=comment)
            self._apply_signature(conn, user, "deviation", closed["id"], "approved", closed)
            asset = dict(conn.execute("SELECT * FROM assets WHERE id = ?", (closed["asset_id"],)).fetchone())
            self._fire_event(conn, user, asset, "deviation_closed", number)
        return next(d for d in self.list_deviations(user) if d["number"] == number)

    # ------------------------------------------------------------------
    # Reports and audit trail
    # ------------------------------------------------------------------

    def due_report(self, user: dict, days: int = 30) -> dict:
        self.require(user, "asset.view")
        today = clock.today()
        horizon = (today + timedelta(days=days)).isoformat()
        out: Dict[str, List[dict]] = {"calibration": [], "maintenance": []}
        with self.db.read() as conn:
            for a in conn.execute(self._ASSET_SELECT + "WHERE a.status != 'retired' AND a.calibration_required = 1 "
                                  "AND (a.calibration_due_on IS NULL OR a.calibration_due_on <= ?) ORDER BY a.calibration_due_on",
                                  (horizon,)):
                out["calibration"].append({"asset_tag": a["asset_tag"], "description": a["description"],
                                           "location": a["location_code"], "status": a["status"], "due_on": a["calibration_due_on"],
                                           "overdue": a["calibration_due_on"] is None or a["calibration_due_on"] < today.isoformat()})
            for r in conn.execute(
                    "SELECT s.title, s.next_due_on, a.asset_tag, a.description, a.status, l.code AS location_code "
                    "FROM pm_schedules s JOIN assets a ON a.id = s.asset_id LEFT JOIN locations l ON l.id = a.location_id "
                    "WHERE s.active = 1 AND s.trigger_type = 'time' AND a.status != 'retired' AND s.next_due_on <= ? "
                    "ORDER BY s.next_due_on", (horizon,)):
                out["maintenance"].append({"asset_tag": r["asset_tag"], "description": f"{r['title']} - {r['description']}",
                                           "location": r["location_code"], "status": r["status"], "due_on": r["next_due_on"],
                                           "overdue": r["next_due_on"] < today.isoformat()})
        return {"as_of": today.isoformat(), "horizon_days": days, **out}

    def audit_entries(self, user: dict, *, entity_type: Optional[str] = None, entity_id: Optional[str] = None,
                      username: Optional[str] = None, limit: int = 200, offset: int = 0) -> List[dict]:
        self.require(user, "audit.view")
        sql, args = "SELECT * FROM audit_trail WHERE 1=1 ", []
        for col, val in (("entity_type", entity_type), ("entity_id", entity_id), ("username", username)):
            if val:
                sql += f"AND {col} = ? "
                args.append(val)
        sql += "ORDER BY seq DESC LIMIT ? OFFSET ?"
        args += [min(max(limit, 1), 5000), max(offset, 0)]
        with self.db.read() as conn:
            return [dict(r) for r in conn.execute(sql, args)]

    def audit_csv(self, user: dict, **filters) -> str:
        rows = self.audit_entries(user, limit=5000, **filters)
        buf = io.StringIO()
        fields = ["seq", "timestamp", "username", "action", "entity_type", "entity_id", "old_value", "new_value",
                  "reason", "prev_hash", "hash"]
        writer = csv.DictWriter(buf, fieldnames=fields)
        writer.writeheader()
        for r in reversed(rows):
            writer.writerow({k: r[k] for k in fields})
        with self.db.transaction() as conn:
            audit.record(conn, username=user["username"], action="AUDIT_EXPORTED", entity_type="audit_trail",
                         new={"rows": len(rows), "filters": {k: v for k, v in filters.items() if v}})
        return buf.getvalue()

    def verify_audit(self, user: dict) -> dict:
        self.require(user, "audit.view")
        with self.db.read() as conn:
            return audit.verify_chain(conn)
