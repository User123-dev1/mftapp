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

import csv
import hashlib
import io
import json
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from . import audit, clock
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
# Target status -> (permission, signature meaning or None if no e-signature needed)
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
    "is_reference_standard", "calibration_required", "calibration_interval_days", "pm_required", "pm_interval_days",
)


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValidationFailed(f"{field} must be an ISO date (YYYY-MM-DD)")


def _plus_days(d: Optional[str], days: Optional[int]) -> Optional[str]:
    if not d or not days:
        return None
    return (date.fromisoformat(d) + timedelta(days=days)).isoformat()


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
        return {k: user[k] for k in ("id", "username", "full_name", "role", "active", "locked")}

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

    def create_user(self, admin: dict, username: str, full_name: str, role: str, temporary_password: str) -> dict:
        self.require(admin, "user.manage")
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
                "INSERT INTO users (username, full_name, role, password_hash, password_changed_at, must_change_password, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                (username, full_name.strip(), role, hash_password(temporary_password, self.settings.pbkdf2_iterations), now, now),
            )
            audit.record(conn, username=admin["username"], action="USER_CREATED", entity_type="user", entity_id=cur.lastrowid,
                         new={"username": username, "full_name": full_name.strip(), "role": role})
            return self._public_user(dict(conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()))

    def update_user(self, admin: dict, user_id: int, *, role: Optional[str] = None, active: Optional[bool] = None,
                    unlock: bool = False, temporary_password: Optional[str] = None, reason: Optional[str] = None) -> dict:
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
        for flag, interval in (("calibration_required", "calibration_interval_days"), ("pm_required", "pm_interval_days")):
            if data.get(flag):
                value = data.get(interval)
                if not isinstance(value, int) or not 1 <= value <= 3650:
                    raise ValidationFailed(f"{interval} (1-3650) is required when {flag} is set")
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
            return asset

    def create_asset(self, user: dict, data: dict) -> dict:
        self.require(user, "asset.create")
        data = dict(data)
        tag = (data.get("asset_tag") or "").strip().upper()
        if not TAG_PATTERN.match(tag):
            raise ValidationFailed("Asset tag must be 2-40 characters: A-Z, 0-9, - _ .")
        self._validate_asset_fields(data)
        gtin = self._normalise_gtin(data.get("gtin"))
        today = clock.today().isoformat()
        now = clock.now_iso()
        with self.db.transaction() as conn:
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
            pm_interval = data.get("pm_interval_days") if data.get("pm_required") else None
            cur = conn.execute(
                "INSERT INTO assets (asset_tag, description, category, gmp_impact, manufacturer, model, serial_number, gtin, "
                "department, location_id, status, qualification_status, is_reference_standard, calibration_required, "
                "calibration_interval_days, pm_required, pm_interval_days, pm_due_on, created_at, created_by, updated_at, updated_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'commissioning', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tag, data["description"].strip(), data["category"].strip(), data["gmp_impact"],
                    data.get("manufacturer"), data.get("model"), data.get("serial_number"), gtin, data.get("department"),
                    location_id, "not_required" if data["gmp_impact"] == "none" else "pending",
                    int(bool(data.get("is_reference_standard"))), int(bool(data.get("calibration_required"))),
                    data.get("calibration_interval_days") if data.get("calibration_required") else None,
                    int(bool(data.get("pm_required"))), pm_interval, _plus_days(today, pm_interval),
                    now, user["username"], now, user["username"],
                ),
            )
            asset = self._asset_core(dict(conn.execute("SELECT * FROM assets WHERE id = ?", (cur.lastrowid,)).fetchone()))
            audit.record(conn, username=user["username"], action="ASSET_CREATED", entity_type="asset", entity_id=tag,
                         new=asset, reason=data.get("reason") or "New asset registration")
        return self.get_asset(user, tag)

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
            for flag in ("is_reference_standard", "calibration_required", "pm_required"):
                merged[flag] = int(bool(merged[flag]))
            self._validate_asset_fields(merged)
            if not merged["calibration_required"]:
                merged["calibration_interval_days"] = None
                merged["calibration_due_on"] = None
            else:
                merged["calibration_due_on"] = _plus_days(merged["last_calibrated_on"], merged["calibration_interval_days"])
            if not merged["pm_required"]:
                merged["pm_interval_days"] = None
                merged["pm_due_on"] = None
            elif merged["pm_interval_days"] != old["pm_interval_days"] or not old["pm_required"]:
                base = merged["last_pm_on"] or clock.today().isoformat()
                merged["pm_due_on"] = _plus_days(base, merged["pm_interval_days"])
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
        return self.get_asset(user, tag)

    def change_status(self, user: dict, tag: str, new_status: str, reason: str, password: Optional[str] = None) -> dict:
        if new_status not in STATUS_RULES:
            raise ValidationFailed(f"Status must be one of {', '.join(STATUS_RULES)}")
        permission, meaning = STATUS_RULES[new_status]
        self.require(user, permission)
        reason = _require_reason(reason)
        with self.db.read() as conn:
            asset = self._asset(conn, tag)
            self._check_transition(conn, asset, new_status)
        if meaning:
            self._verify_signer(user, password, meaning)
        with self.db.transaction() as conn:
            asset = self._asset(conn, tag)
            self._check_transition(conn, asset, new_status)
            updated = self._set_asset_fields(conn, user, asset, {"status": new_status}, "STATUS_CHANGED", reason)
            if meaning:
                self._apply_signature(conn, user, "asset", asset["id"], meaning, self._asset_core(updated))
        return self.get_asset(user, tag)

    def _check_transition(self, conn, asset: dict, new_status: str) -> None:
        if new_status not in STATUS_TRANSITIONS[asset["status"]]:
            raise Conflict(f"Cannot change status from {asset['status']} to {new_status}")
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
        if asset["pm_required"] and asset["pm_due_on"] and date.fromisoformat(asset["pm_due_on"]) < on:
            blockers.append(f"Preventive maintenance overdue since {asset['pm_due_on']}")
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
        lines = [asset["description"][:48]]
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
        self.require(user, "maintenance.record")
        if maintenance_type not in ("preventive", "corrective"):
            raise ValidationFailed("maintenance_type must be preventive or corrective")
        performed = _parse_date(performed_on, "performed_on")
        if performed > clock.today():
            raise ValidationFailed("Maintenance date cannot be in the future")
        if not description or len(description.strip()) < 5:
            raise ValidationFailed("A description of the work performed is required")
        with self.db.read() as conn:
            if self._asset(conn, tag)["status"] == "retired":
                raise Conflict("Retired assets cannot be maintained")
        self._verify_signer(user, password, "performed")
        with self.db.transaction() as conn:
            asset = self._asset(conn, tag)
            record = {
                "asset_id": asset["id"], "maintenance_type": maintenance_type, "performed_on": performed.isoformat(),
                "performed_by": user["username"], "description": description.strip(),
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
            if maintenance_type == "preventive" and asset["pm_required"] and \
                    (not asset["last_pm_on"] or record["performed_on"] >= asset["last_pm_on"]):
                fields.update(last_pm_on=record["performed_on"],
                              pm_due_on=_plus_days(record["performed_on"], asset["pm_interval_days"]))
            if requalification_required and asset["gmp_impact"] != "none":
                fields["qualification_status"] = "requalification_required"
            if fields:
                self._set_asset_fields(conn, user, asset, fields, "MAINTENANCE_STATUS_UPDATED",
                                       f"Maintenance record #{record['id']}")
        return self.get_asset(user, tag)

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
            for a in conn.execute(self._ASSET_SELECT + "WHERE a.status != 'retired' AND a.pm_required = 1 "
                                  "AND a.pm_due_on <= ? ORDER BY a.pm_due_on", (horizon,)):
                out["maintenance"].append({"asset_tag": a["asset_tag"], "description": a["description"],
                                           "location": a["location_code"], "status": a["status"], "due_on": a["pm_due_on"],
                                           "overdue": a["pm_due_on"] < today.isoformat()})
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
