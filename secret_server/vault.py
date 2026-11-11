"""
Vault operations: folders, templates, secrets, check-out / check-in,
password rotation, history, and the access-request (approval) workflow.

All methods take an open transaction (`conn`) and the acting Principal, enforce
permissions, and write an audit record for every security-relevant action.
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .auth import Principal
from .changers import PasswordChangeError, get_changer
from .crypto import decrypt_field, encrypt_field, generate_password, new_dek
from .db import EDIT, OWNER, PERMISSION_LABELS, PERMISSION_NAMES, VIEW, now_iso, utcnow
from .permissions import folder_level, is_approver, secret_acl_level, secret_level

FIELD_TYPES = {"text", "password", "notes", "url"}
SETTINGS_FIELDS = ("require_checkout", "checkout_minutes", "rotate_on_checkin",
                   "require_comment", "require_approval", "approver_group_id",
                   "password_changer", "expiry_days")


class VaultError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _not_found(kind: str) -> VaultError:
    return VaultError(f"{kind} not found", 404)


class Vault:
    def __init__(self, keyring, audit):
        self._keyring = keyring
        self._audit = audit

    # ------------------------------------------------------------------ helpers

    def _dek(self, secret) -> bytes:
        return self._keyring.unwrap_dek(secret["dek_wrapped"], f"secret:{secret['id']}:dek")

    @staticmethod
    def _field_aad(secret_id: int, slug: str) -> str:
        return f"secret:{secret_id}:field:{slug}"

    @staticmethod
    def _secret_row(conn, secret_id: int):
        row = conn.execute("SELECT * FROM secrets WHERE id = ? AND active = 1",
                           (secret_id,)).fetchone()
        if row is None:
            raise _not_found("secret")
        return row

    def _require_acl(self, conn, p: Principal, secret, level: int):
        acl = secret_acl_level(conn, p, secret)
        if acl == 0:
            raise _not_found("secret")  # don't reveal existence
        if acl < level:
            raise VaultError(f"{PERMISSION_LABELS[level]} permission required", 403)
        return acl

    @staticmethod
    def _validate_acl(conn, entries: List[dict]) -> List[tuple]:
        out = []
        for e in entries:
            ptype, pid = e["principal_type"], int(e["principal_id"])
            level = PERMISSION_NAMES.get(str(e["level"]).lower())
            if ptype not in ("user", "group") or level is None:
                raise VaultError("invalid permission entry")
            table = "users" if ptype == "user" else "groups"
            if conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (pid,)).fetchone() is None:
                raise VaultError(f"{ptype} {pid} does not exist")
            out.append((ptype, pid, level))
        return out

    @staticmethod
    def _acl_out(rows) -> List[dict]:
        return [{"principal_type": r["principal_type"], "principal_id": r["principal_id"],
                 "level": PERMISSION_LABELS[r["level"]]} for r in rows]

    # ------------------------------------------------------------------ templates

    @staticmethod
    def template(conn, template_id: int) -> dict:
        row = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
        if row is None:
            raise _not_found("template")
        return {"id": row["id"], "name": row["name"], "fields": json.loads(row["fields_json"]),
                "password_policy": json.loads(row["password_policy"]) if row["password_policy"] else None,
                "expiry_days": row["expiry_days"]}

    def list_templates(self, conn) -> List[dict]:
        return [self.template(conn, r["id"]) for r in conn.execute("SELECT id FROM templates ORDER BY name")]

    def create_template(self, conn, p: Principal, name: str, fields: List[dict],
                        password_policy: Optional[dict], expiry_days: Optional[int]) -> dict:
        if not p.is_admin:
            raise VaultError("administrator role required", 403)
        slugs = set()
        for f in fields:
            if f.get("type", "text") not in FIELD_TYPES:
                raise VaultError(f"invalid field type {f.get('type')}")
            if not f.get("slug") or f["slug"] in slugs:
                raise VaultError("field slugs must be present and unique")
            slugs.add(f["slug"])
        if password_policy:
            generate_password(password_policy)  # validates the policy
        try:
            cur = conn.execute(
                "INSERT INTO templates (name, fields_json, password_policy, expiry_days) VALUES (?,?,?,?)",
                (name, json.dumps(fields), json.dumps(password_policy) if password_policy else None,
                 expiry_days))
        except Exception:
            raise VaultError("template name already exists", 409)
        self._audit.record(conn, "TEMPLATE_CREATE", p, "template", cur.lastrowid, {"name": name})
        return self.template(conn, cur.lastrowid)

    def generate_for_template(self, conn, template_id: Optional[int]) -> str:
        policy = self.template(conn, template_id)["password_policy"] if template_id else None
        return generate_password(policy)

    # ------------------------------------------------------------------ folders

    @staticmethod
    def _folder_row(conn, folder_id: int):
        row = conn.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)).fetchone()
        if row is None:
            raise _not_found("folder")
        return row

    def create_folder(self, conn, p: Principal, name: str, parent_id: Optional[int],
                      inherit_permissions: bool = True) -> dict:
        if parent_id is None:
            if not p.is_admin:
                raise VaultError("only administrators can create root folders", 403)
            inherit_permissions = False
        else:
            self._folder_row(conn, parent_id)
            if folder_level(conn, p, parent_id) < EDIT:
                raise VaultError("Edit permission on parent folder required", 403)
        cur = conn.execute(
            "INSERT INTO folders (name, parent_id, inherit_permissions, created_at) VALUES (?,?,?,?)",
            (name, parent_id, int(inherit_permissions), now_iso()))
        folder_id = cur.lastrowid
        if not inherit_permissions:
            # Creator owns a folder that breaks inheritance
            conn.execute("INSERT INTO folder_permissions VALUES (?, 'user', ?, ?)",
                         (folder_id, p.user_id, OWNER))
        self._audit.record(conn, "FOLDER_CREATE", p, "folder", folder_id,
                           {"name": name, "parent_id": parent_id})
        return self.folder(conn, p, folder_id)

    def folder(self, conn, p: Principal, folder_id: int) -> dict:
        row = self._folder_row(conn, folder_id)
        level = folder_level(conn, p, folder_id)
        if level == 0:
            raise _not_found("folder")
        return {"id": row["id"], "name": row["name"], "parent_id": row["parent_id"],
                "inherit_permissions": bool(row["inherit_permissions"]),
                "my_permission": PERMISSION_LABELS[level]}

    def list_folders(self, conn, p: Principal) -> List[dict]:
        out = []
        for row in conn.execute("SELECT id FROM folders ORDER BY parent_id, name"):
            if folder_level(conn, p, row["id"]) >= VIEW:
                out.append(self.folder(conn, p, row["id"]))
        return out

    def folder_permissions(self, conn, p: Principal, folder_id: int) -> dict:
        row = self._folder_row(conn, folder_id)
        if folder_level(conn, p, folder_id) < OWNER:
            raise VaultError("Owner permission required", 403)
        rows = conn.execute("SELECT * FROM folder_permissions WHERE folder_id = ?", (folder_id,))
        return {"inherit_permissions": bool(row["inherit_permissions"]),
                "entries": self._acl_out(rows)}

    def set_folder_permissions(self, conn, p: Principal, folder_id: int, inherit: bool,
                               entries: List[dict]) -> dict:
        row = self._folder_row(conn, folder_id)
        if folder_level(conn, p, folder_id) < OWNER:
            raise VaultError("Owner permission required", 403)
        if inherit and row["parent_id"] is None:
            raise VaultError("root folders cannot inherit permissions")
        acl = self._validate_acl(conn, entries)
        conn.execute("DELETE FROM folder_permissions WHERE folder_id = ?", (folder_id,))
        conn.executemany("INSERT INTO folder_permissions VALUES (?,?,?,?)",
                         [(folder_id, *e) for e in acl])
        conn.execute("UPDATE folders SET inherit_permissions = ? WHERE id = ?",
                     (int(inherit), folder_id))
        self._audit.record(conn, "FOLDER_PERMISSIONS", p, "folder", folder_id,
                           {"inherit": inherit, "entries": entries})
        if folder_level(conn, p, folder_id) < OWNER:
            # Owner removed their own access; nothing more to show them
            return {"inherit_permissions": inherit, "entries": []}
        return self.folder_permissions(conn, p, folder_id)

    # ------------------------------------------------------------------ secrets

    def _validate_fields(self, template: dict, fields: Dict[str, str], partial: bool) -> None:
        known = {f["slug"]: f for f in template["fields"]}
        unknown = set(fields) - set(known)
        if unknown:
            raise VaultError(f"unknown fields for template: {sorted(unknown)}")
        if not partial:
            missing = [s for s, f in known.items() if f.get("required") and not fields.get(s)]
            if missing:
                raise VaultError(f"missing required fields: {missing}")

    @staticmethod
    def _password_slug(template: dict) -> Optional[str]:
        for f in template["fields"]:
            if f["slug"] == "password":
                return "password"
        for f in template["fields"]:
            if f.get("type") == "password":
                return f["slug"]
        return None

    def _write_field(self, conn, secret, dek: bytes, slug: str, value: str,
                     actor_id: Optional[int]) -> None:
        old = conn.execute("SELECT value_enc FROM secret_fields WHERE secret_id = ? AND slug = ?",
                           (secret["id"], slug)).fetchone()
        if old is not None:
            conn.execute("INSERT INTO secret_field_history (secret_id, slug, value_enc, changed_by, "
                         "changed_at) VALUES (?,?,?,?,?)",
                         (secret["id"], slug, old["value_enc"], actor_id, now_iso()))
        conn.execute(
            "INSERT INTO secret_fields (secret_id, slug, value_enc) VALUES (?,?,?) "
            "ON CONFLICT (secret_id, slug) DO UPDATE SET value_enc = excluded.value_enc",
            (secret["id"], slug, encrypt_field(dek, value, self._field_aad(secret["id"], slug))))

    def create_secret(self, conn, p: Principal, name: str, folder_id: int, template_id: int,
                      fields: Dict[str, str], generate_password_field: bool = False,
                      settings: Optional[dict] = None) -> dict:
        self._folder_row(conn, folder_id)
        if folder_level(conn, p, folder_id) < EDIT:
            raise VaultError("Edit permission on folder required", 403)
        template = self.template(conn, template_id)
        fields = {k: v for k, v in fields.items() if v is not None}
        pw_slug = self._password_slug(template)
        if generate_password_field and pw_slug and not fields.get(pw_slug):
            fields[pw_slug] = generate_password(template["password_policy"])
        self._validate_fields(template, fields, partial=False)
        settings = self._validate_settings(conn, settings or {})

        now = now_iso()
        cur = conn.execute(
            "INSERT INTO secrets (name, folder_id, template_id, dek_wrapped, expiry_days, "
            "password_changed_at, created_at, updated_at) VALUES (?,?,?,'',?,?,?,?)",
            (name, folder_id, template_id, settings.pop("expiry_days", template["expiry_days"]),
             now, now, now))
        secret_id = cur.lastrowid
        dek = new_dek()
        conn.execute("UPDATE secrets SET dek_wrapped = ? WHERE id = ?",
                     (self._keyring.wrap_dek(dek, f"secret:{secret_id}:dek"), secret_id))
        self._apply_settings(conn, secret_id, settings)
        secret = self._secret_row(conn, secret_id)
        for slug, value in fields.items():
            self._write_field(conn, secret, dek, slug, value, p.user_id)
        self._audit.record(conn, "SECRET_CREATE", p, "secret", secret_id,
                           {"name": name, "folder_id": folder_id, "template": template["name"]})
        return self.describe(conn, p, secret_id)

    def _validate_settings(self, conn, settings: dict) -> dict:
        out = {k: v for k, v in settings.items() if k in SETTINGS_FIELDS and v is not None}
        if "password_changer" in out:
            try:
                get_changer(out["password_changer"])
            except PasswordChangeError as exc:
                raise VaultError(str(exc))
        if out.get("approver_group_id") is not None and conn.execute(
                "SELECT 1 FROM groups WHERE id = ?", (out["approver_group_id"],)).fetchone() is None:
            raise VaultError("approver group does not exist")
        if out.get("checkout_minutes") is not None and not 1 <= int(out["checkout_minutes"]) <= 10080:
            raise VaultError("checkout_minutes must be between 1 and 10080")
        return out

    @staticmethod
    def _apply_settings(conn, secret_id: int, settings: dict) -> None:
        for key, value in settings.items():
            if isinstance(value, bool):
                value = int(value)
            conn.execute(f"UPDATE secrets SET {key} = ? WHERE id = ?", (value, secret_id))

    def describe(self, conn, p: Principal, secret_id: int) -> dict:
        secret = self._secret_row(conn, secret_id)
        acl = self._require_acl(conn, p, secret, VIEW)
        effective = secret_level(conn, p, secret)
        template = self.template(conn, secret["template_id"])
        present = {r["slug"] for r in conn.execute(
            "SELECT slug FROM secret_fields WHERE secret_id = ?", (secret_id,))}
        expires_at = None
        if secret["expiry_days"] and secret["password_changed_at"]:
            expires_at = (datetime.fromisoformat(secret["password_changed_at"])
                          + timedelta(days=secret["expiry_days"])).isoformat()
        return {
            "id": secret["id"], "name": secret["name"], "folder_id": secret["folder_id"],
            "template": {"id": template["id"], "name": template["name"]},
            "fields": [{"slug": f["slug"], "name": f.get("name", f["slug"]),
                        "type": f.get("type", "text"), "has_value": f["slug"] in present}
                       for f in template["fields"]],
            "inherit_permissions": bool(secret["inherit_permissions"]),
            "settings": {k: (bool(secret[k]) if k in ("require_checkout", "rotate_on_checkin",
                                                      "require_comment", "require_approval")
                             else secret[k]) for k in SETTINGS_FIELDS},
            "checked_out_by": secret["checked_out_by"]
            if secret["checked_out_until"] and secret["checked_out_until"] > now_iso() else None,
            "checked_out_until": secret["checked_out_until"]
            if secret["checked_out_until"] and secret["checked_out_until"] > now_iso() else None,
            "password_changed_at": secret["password_changed_at"],
            "expires_at": expires_at,
            "expired": bool(expires_at and expires_at < now_iso()),
            "my_permission": PERMISSION_LABELS[acl],
            "access_granted": effective >= VIEW,
            "updated_at": secret["updated_at"],
        }

    def search(self, conn, p: Principal, text: Optional[str] = None,
               folder_id: Optional[int] = None) -> List[dict]:
        sql, args = "SELECT * FROM secrets WHERE active = 1", []
        if text:
            sql += " AND name LIKE ?"
            args.append(f"%{text}%")
        if folder_id is not None:
            sql += " AND folder_id = ?"
            args.append(folder_id)
        out = []
        for row in conn.execute(sql + " ORDER BY name", args).fetchall():
            if secret_acl_level(conn, p, row) >= VIEW:
                out.append({"id": row["id"], "name": row["name"], "folder_id": row["folder_id"],
                            "template_id": row["template_id"]})
        return out

    def _check_access_gates(self, conn, p: Principal, secret, comment: Optional[str]) -> int:
        self._require_acl(conn, p, secret, VIEW)
        level = secret_level(conn, p, secret)
        if level < VIEW:
            raise VaultError("access requires an approved access request", 403)
        if secret["require_comment"] and not (comment and comment.strip()):
            raise VaultError("a comment is required to access this secret", 428)
        if secret["require_checkout"]:
            if secret["checked_out_by"] != p.user_id or \
                    not secret["checked_out_until"] or secret["checked_out_until"] <= now_iso():
                raise VaultError("secret must be checked out first", 409)
        return level

    def reveal(self, conn, p: Principal, secret_id: int, comment: Optional[str] = None,
               slugs: Optional[List[str]] = None) -> Dict[str, str]:
        secret = self._secret_row(conn, secret_id)
        self._check_access_gates(conn, p, secret, comment)
        dek = self._dek(secret)
        sql, args = "SELECT slug, value_enc FROM secret_fields WHERE secret_id = ?", [secret_id]
        if slugs:
            sql += f" AND slug IN ({','.join('?' * len(slugs))})"
            args += slugs
        values = {r["slug"]: decrypt_field(dek, r["value_enc"], self._field_aad(secret_id, r["slug"]))
                  for r in conn.execute(sql, args)}
        self._audit.record(conn, "SECRET_VIEW", p, "secret", secret_id,
                           {"fields": sorted(values), "comment": comment})
        return values

    def update_secret(self, conn, p: Principal, secret_id: int, name: Optional[str] = None,
                      folder_id: Optional[int] = None, fields: Optional[Dict[str, str]] = None,
                      settings: Optional[dict] = None, comment: Optional[str] = None) -> dict:
        secret = self._secret_row(conn, secret_id)
        self._require_acl(conn, p, secret, EDIT)
        if secret["require_checkout"] and secret["checked_out_until"] and \
                secret["checked_out_until"] > now_iso() and secret["checked_out_by"] != p.user_id:
            raise VaultError("secret is checked out by another user", 409)
        changes = {}
        if name:
            conn.execute("UPDATE secrets SET name = ? WHERE id = ?", (name, secret_id))
            changes["name"] = name
        if folder_id is not None and folder_id != secret["folder_id"]:
            self._folder_row(conn, folder_id)
            if folder_level(conn, p, folder_id) < EDIT:
                raise VaultError("Edit permission on destination folder required", 403)
            conn.execute("UPDATE secrets SET folder_id = ? WHERE id = ?", (folder_id, secret_id))
            changes["folder_id"] = folder_id
        if settings:
            self._require_acl(conn, p, secret, OWNER)
            settings = self._validate_settings(conn, settings)
            self._apply_settings(conn, secret_id, settings)
            changes["settings"] = settings
        if fields:
            template = self.template(conn, secret["template_id"])
            self._validate_fields(template, fields, partial=True)
            dek = self._dek(secret)
            for slug, value in fields.items():
                self._write_field(conn, secret, dek, slug, value, p.user_id)
            if self._password_slug(template) in fields:
                conn.execute("UPDATE secrets SET password_changed_at = ? WHERE id = ?",
                             (now_iso(), secret_id))
            changes["fields"] = sorted(fields)
        conn.execute("UPDATE secrets SET updated_at = ? WHERE id = ?", (now_iso(), secret_id))
        self._audit.record(conn, "SECRET_EDIT", p, "secret", secret_id,
                           {"changes": changes, "comment": comment})
        return self.describe(conn, p, secret_id)

    def delete_secret(self, conn, p: Principal, secret_id: int) -> None:
        secret = self._secret_row(conn, secret_id)
        self._require_acl(conn, p, secret, OWNER)
        conn.execute("UPDATE secrets SET active = 0, updated_at = ? WHERE id = ?",
                     (now_iso(), secret_id))
        self._audit.record(conn, "SECRET_DELETE", p, "secret", secret_id, {"name": secret["name"]})

    # ------------------------------------------------------------------ permissions

    def secret_permissions(self, conn, p: Principal, secret_id: int) -> dict:
        secret = self._secret_row(conn, secret_id)
        self._require_acl(conn, p, secret, OWNER)
        rows = conn.execute("SELECT * FROM secret_permissions WHERE secret_id = ?", (secret_id,))
        return {"inherit_permissions": bool(secret["inherit_permissions"]),
                "entries": self._acl_out(rows)}

    def set_secret_permissions(self, conn, p: Principal, secret_id: int, inherit: bool,
                               entries: List[dict]) -> dict:
        secret = self._secret_row(conn, secret_id)
        self._require_acl(conn, p, secret, OWNER)
        acl = self._validate_acl(conn, entries)
        conn.execute("DELETE FROM secret_permissions WHERE secret_id = ?", (secret_id,))
        conn.executemany("INSERT INTO secret_permissions VALUES (?,?,?,?)",
                         [(secret_id, *e) for e in acl])
        conn.execute("UPDATE secrets SET inherit_permissions = ? WHERE id = ?",
                     (int(inherit), secret_id))
        self._audit.record(conn, "SECRET_PERMISSIONS", p, "secret", secret_id,
                           {"inherit": inherit, "entries": entries})
        if secret_acl_level(conn, p, self._secret_row(conn, secret_id)) < OWNER:
            # Owner removed their own access; nothing more to show them
            return {"inherit_permissions": inherit, "entries": []}
        return self.secret_permissions(conn, p, secret_id)

    # ------------------------------------------------------------------ check-out / rotation

    def check_out(self, conn, p: Principal, secret_id: int, comment: Optional[str] = None) -> dict:
        secret = self._secret_row(conn, secret_id)
        if not secret["require_checkout"]:
            raise VaultError("secret does not use check-out")
        self._require_acl(conn, p, secret, VIEW)
        if secret_level(conn, p, secret) < VIEW:
            raise VaultError("access requires an approved access request", 403)
        if secret["require_comment"] and not (comment and comment.strip()):
            raise VaultError("a comment is required to access this secret", 428)
        until = now_iso(timedelta(minutes=secret["checkout_minutes"]))
        cur = conn.execute(
            "UPDATE secrets SET checked_out_by = ?, checked_out_until = ? WHERE id = ? AND "
            "(checked_out_by IS NULL OR checked_out_by = ? OR checked_out_until <= ?)",
            (p.user_id, until, secret_id, p.user_id, now_iso()))
        if cur.rowcount == 0:
            raise VaultError("secret is checked out by another user", 409)
        self._audit.record(conn, "SECRET_CHECKOUT", p, "secret", secret_id,
                           {"until": until, "comment": comment})
        return {"checked_out_until": until}

    def check_in(self, conn, p: Principal, secret_id: int) -> dict:
        secret = self._secret_row(conn, secret_id)
        acl = self._require_acl(conn, p, secret, VIEW)
        if secret["checked_out_by"] is None:
            raise VaultError("secret is not checked out")
        if secret["checked_out_by"] != p.user_id and acl < OWNER:
            raise VaultError("secret is checked out by another user", 409)
        return self._release(conn, secret, p)

    def _release(self, conn, secret, actor: Optional[Principal]) -> dict:
        rotated = False
        if secret["rotate_on_checkin"]:
            self._rotate(conn, secret, actor, reason="check-in")
            rotated = True
        conn.execute("UPDATE secrets SET checked_out_by = NULL, checked_out_until = NULL "
                     "WHERE id = ?", (secret["id"],))
        self._audit.record(conn, "SECRET_CHECKIN", actor, "secret", secret["id"],
                           {"rotated": rotated, "previous_holder": secret["checked_out_by"]},
                           username=None if actor else "system")
        return {"checked_in": True, "rotated": rotated}

    def rotate(self, conn, p: Principal, secret_id: int) -> dict:
        secret = self._secret_row(conn, secret_id)
        self._require_acl(conn, p, secret, EDIT)
        self._rotate(conn, secret, p, reason="manual")
        return {"rotated": True, "password_changed_at": now_iso()}

    def _rotate(self, conn, secret, actor: Optional[Principal], reason: str) -> None:
        template = self.template(conn, secret["template_id"])
        slug = self._password_slug(template)
        if slug is None:
            raise VaultError("template has no password field to rotate")
        dek = self._dek(secret)
        current = {r["slug"]: decrypt_field(dek, r["value_enc"], self._field_aad(secret["id"], r["slug"]))
                   for r in conn.execute("SELECT slug, value_enc FROM secret_fields WHERE secret_id = ?",
                                         (secret["id"],))}
        new_password = generate_password(template["password_policy"])
        changer = secret["password_changer"]
        try:
            get_changer(changer).change(current, new_password)
        except PasswordChangeError as exc:
            self._audit.record(conn, "SECRET_ROTATE_FAILED", actor, "secret", secret["id"],
                               {"reason": reason, "changer": changer, "error": str(exc)},
                               username=None if actor else "system")
            raise VaultError(f"password change failed: {exc}", 502)
        self._write_field(conn, secret, dek, slug, new_password, actor.user_id if actor else None)
        conn.execute("UPDATE secrets SET password_changed_at = ?, updated_at = ? WHERE id = ?",
                     (now_iso(), now_iso(), secret["id"]))
        self._audit.record(conn, "SECRET_ROTATE", actor, "secret", secret["id"],
                           {"reason": reason, "changer": changer},
                           username=None if actor else "system")

    def history(self, conn, p: Principal, secret_id: int) -> List[dict]:
        secret = self._secret_row(conn, secret_id)
        self._require_acl(conn, p, secret, OWNER)
        dek = self._dek(secret)
        rows = conn.execute(
            "SELECT h.*, u.username FROM secret_field_history h LEFT JOIN users u "
            "ON u.id = h.changed_by WHERE secret_id = ? ORDER BY h.id DESC", (secret_id,))
        out = [{"slug": r["slug"], "replaced_at": r["changed_at"], "replaced_by": r["username"],
                "value": decrypt_field(dek, r["value_enc"], self._field_aad(secret_id, r["slug"]))}
               for r in rows]
        self._audit.record(conn, "SECRET_HISTORY_VIEW", p, "secret", secret_id)
        return out

    def run_maintenance(self, conn) -> dict:
        """Release expired check-outs (rotating where configured) and report expired passwords."""
        released, errors = [], []
        for secret in conn.execute(
                "SELECT * FROM secrets WHERE active = 1 AND checked_out_by IS NOT NULL "
                "AND checked_out_until <= ?", (now_iso(),)).fetchall():
            try:
                self._release(conn, secret, None)
                released.append(secret["id"])
            except VaultError as exc:
                errors.append({"secret_id": secret["id"], "error": str(exc)})
        return {"released": released, "errors": errors, "expired": self.expired_ids(conn)}

    @staticmethod
    def expired_ids(conn) -> List[int]:
        now = utcnow()
        out = []
        for r in conn.execute("SELECT id, expiry_days, password_changed_at FROM secrets "
                              "WHERE active = 1 AND expiry_days IS NOT NULL"):
            changed = datetime.fromisoformat(r["password_changed_at"])
            if changed + timedelta(days=r["expiry_days"]) < now:
                out.append(r["id"])
        return out

    # ------------------------------------------------------------------ access requests

    def request_access(self, conn, p: Principal, secret_id: int, reason: str,
                       minutes: int = 60) -> dict:
        secret = self._secret_row(conn, secret_id)
        self._require_acl(conn, p, secret, VIEW)
        if not secret["require_approval"]:
            raise VaultError("secret does not require approval")
        if not reason.strip():
            raise VaultError("a reason is required")
        if not 1 <= minutes <= 1440:
            raise VaultError("minutes must be between 1 and 1440")
        cur = conn.execute(
            "INSERT INTO access_requests (secret_id, requester_id, reason, minutes, created_at) "
            "VALUES (?,?,?,?,?)", (secret_id, p.user_id, reason, minutes, now_iso()))
        self._audit.record(conn, "ACCESS_REQUEST", p, "secret", secret_id,
                           {"request_id": cur.lastrowid, "reason": reason, "minutes": minutes})
        return self._request_out(conn, cur.lastrowid)

    @staticmethod
    def _request_out(conn, request_id: int) -> dict:
        r = conn.execute(
            "SELECT r.*, s.name AS secret_name, u.username AS requester FROM access_requests r "
            "JOIN secrets s ON s.id = r.secret_id JOIN users u ON u.id = r.requester_id "
            "WHERE r.id = ?", (request_id,)).fetchone()
        return {"id": r["id"], "secret_id": r["secret_id"], "secret_name": r["secret_name"],
                "requester": r["requester"], "reason": r["reason"], "minutes": r["minutes"],
                "status": r["status"], "review_note": r["review_note"],
                "expires_at": r["expires_at"], "created_at": r["created_at"]}

    def list_requests(self, conn, p: Principal) -> List[dict]:
        """Requests the principal made, plus pending requests they can review."""
        out = []
        for r in conn.execute(
                "SELECT r.id AS request_id, r.requester_id, r.status, s.* FROM access_requests r "
                "JOIN secrets s ON s.id = r.secret_id ORDER BY r.id DESC").fetchall():
            mine = r["requester_id"] == p.user_id
            reviewable = r["status"] == "pending" and r["requester_id"] != p.user_id and \
                (p.is_admin or is_approver(p, r))
            if mine or reviewable:
                out.append(self._request_out(conn, r["request_id"]))
        return out

    def review_request(self, conn, p: Principal, request_id: int, approve: bool,
                       note: Optional[str] = None) -> dict:
        r = conn.execute("SELECT * FROM access_requests WHERE id = ?", (request_id,)).fetchone()
        if r is None:
            raise _not_found("access request")
        secret = self._secret_row(conn, r["secret_id"])
        if not (p.is_admin or is_approver(p, secret)):
            raise VaultError("not an approver for this secret", 403)
        if r["requester_id"] == p.user_id:
            raise VaultError("cannot review your own request", 403)
        if r["status"] != "pending":
            raise VaultError(f"request already {r['status']}", 409)
        status = "approved" if approve else "denied"
        expires = now_iso(timedelta(minutes=r["minutes"])) if approve else None
        conn.execute("UPDATE access_requests SET status = ?, reviewer_id = ?, review_note = ?, "
                     "reviewed_at = ?, expires_at = ? WHERE id = ?",
                     (status, p.user_id, note, now_iso(), expires, request_id))
        self._audit.record(conn, "ACCESS_APPROVED" if approve else "ACCESS_DENIED", p, "secret",
                           secret["id"], {"request_id": request_id, "note": note})
        return self._request_out(conn, request_id)

    # ------------------------------------------------------------------ key management

    def rewrap_all(self, conn) -> int:
        """Re-wrap every data key and master-key-encrypted value under the current master key."""
        count = 0
        for s in conn.execute("SELECT id, dek_wrapped FROM secrets").fetchall():
            if self._keyring.needs_rewrap(s["dek_wrapped"]):
                aad = f"secret:{s['id']}:dek"
                conn.execute("UPDATE secrets SET dek_wrapped = ? WHERE id = ?",
                             (self._keyring.wrap_dek(self._keyring.unwrap_dek(s["dek_wrapped"], aad), aad),
                              s["id"]))
                count += 1
        for u in conn.execute("SELECT id, mfa_secret_enc FROM users WHERE mfa_secret_enc IS NOT NULL").fetchall():
            if self._keyring.needs_rewrap(u["mfa_secret_enc"]):
                aad = f"mfa:{u['id']}"
                conn.execute("UPDATE users SET mfa_secret_enc = ? WHERE id = ?",
                             (self._keyring.encrypt_value(self._keyring.decrypt_value(u["mfa_secret_enc"], aad), aad),
                              u["id"]))
                count += 1
        for sp in conn.execute("SELECT id, name, client_secret_enc FROM sso_providers "
                               "WHERE client_secret_enc IS NOT NULL").fetchall():
            if self._keyring.needs_rewrap(sp["client_secret_enc"]):
                aad = f"sso:{sp['name']}"
                conn.execute("UPDATE sso_providers SET client_secret_enc = ? WHERE id = ?",
                             (self._keyring.encrypt_value(self._keyring.decrypt_value(sp["client_secret_enc"], aad), aad),
                              sp["id"]))
                count += 1
        self._audit.record(conn, "MASTER_KEY_REWRAP", None, "keyring", self._keyring.current_id,
                           {"rewrapped": count}, username="system")
        return count
