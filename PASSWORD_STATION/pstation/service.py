"""The password flows. Messages (codes and notices) are returned to the caller to be sent
after the response, so response time doesn't reveal whether an account matched."""

import hmac
import json
import logging
import re
import secrets
import smtplib
import ssl
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from typing import Optional

from . import directory as dirmod
from .directory import DirectoryError, DirUser, DomainConfig, PolicyError, check_basic_policy
from .security import (AuditLog, Box, code_hash, hash_password, iso, mask_email, mask_phone, new_code, now, parse,
                       verify_password, window_start)

log = logging.getLogger(__name__)
FLOW_MINUTES = 15
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class StationError(Exception):
    """Safe to show to the user."""


@dataclass
class Message:
    channel: str          # email | sms
    recipient: str
    subject: str
    body: str


def normalize_contact(channel: str, value: str) -> str:
    value = (value or "").strip()
    if channel == "email":
        value = value.lower()
        if not EMAIL_RE.match(value) or len(value) > 254:
            raise StationError("Enter a valid e-mail address.")
        return value
    if channel == "sms":
        digits = re.sub(r"[\s().-]", "", value)
        if digits.startswith("00"):
            digits = "+" + digits[2:]
        if not re.fullmatch(r"\+[1-9]\d{7,14}", digits):
            raise StationError("Enter the mobile number in international format, e.g. +1 555 123 4567.")
        return digits
    raise StationError("Choose e-mail or text message.")


class Station:
    def __init__(self, settings, db):
        self.s, self.db = settings, db
        self.box = Box(settings.data_key)
        self.audit = AuditLog(db)
        if not settings.secret_key:
            raise ValueError("PS_SECRET_KEY must be set")

    # --- Domains --------------------------------------------------------------------------
    def domains(self, include_disabled: bool = False) -> list[DomainConfig]:
        rows = self.db.read().execute("SELECT * FROM domains ORDER BY sort, name").fetchall()
        out = []
        for r in rows:
            if r["enabled"] or include_disabled:
                out.append(DomainConfig(id=r["id"], name=r["name"], kind=r["kind"], settings=json.loads(r["settings"]),
                                        secret=self.box.open(r["secret_enc"], f"domain:{r['id']}"),
                                        enabled=bool(r["enabled"]), sort=r["sort"]))
        return out

    def domain(self, domain_id: str) -> DomainConfig:
        for d in self.domains():
            if d.id == domain_id:
                return d
        raise StationError("Choose a domain.")

    def directory(self, cfg: DomainConfig) -> dirmod.Directory:
        if cfg.kind == "ad":
            return dirmod.ADDirectory(cfg)
        if cfg.kind == "demo":
            return dirmod.DemoDirectory(cfg, self.db)
        raise StationError("This domain is not configured correctly.")

    def save_domain(self, domain_id: str, name: str, kind: str, settings: dict, secret: Optional[str],
                    enabled: bool, sort: int = 0) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,30}", domain_id):
            raise StationError("Domain key: 2-31 lower-case letters, digits or dashes.")
        if kind not in ("ad", "demo"):
            raise StationError("Unknown directory type.")
        with self.db.tx() as conn:
            row = conn.execute("SELECT secret_enc FROM domains WHERE id = ?", (domain_id,)).fetchone()
            sealed = self.box.seal(secret, f"domain:{domain_id}") if secret else (row["secret_enc"] if row else None)
            conn.execute("INSERT INTO domains (id, name, kind, settings, secret_enc, enabled, sort) VALUES (?, ?, ?, ?, ?, ?, ?) "
                         "ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind, settings=excluded.settings, "
                         "secret_enc=excluded.secret_enc, enabled=excluded.enabled, sort=excluded.sort",
                         (domain_id, name, kind, json.dumps(settings), sealed, int(enabled), sort))

    def ensure_demo(self) -> None:
        if not self.s.demo or self.db.read().execute("SELECT 1 FROM domains").fetchone():
            return
        self.save_domain("gmp", "GMP domain (demo)", "demo", {"min_length": 12}, None, True, 1)
        self.save_domain("business", "Business - Entra ID / Microsoft 365 (demo)", "demo", {"min_length": 12}, None, True, 2)
        for d in ("gmp", "business"):
            dirmod.DemoDirectory.seed(self.db, d)

    # --- Throttling -------------------------------------------------------------------------
    def _allow(self, key: str, limit: int, minutes: int = 60) -> bool:
        with self.db.tx() as conn:
            conn.execute("DELETE FROM throttle WHERE at < ?", (window_start(24 * 60),))
            n = conn.execute("SELECT COUNT(*) FROM throttle WHERE key = ? AND at >= ?",
                             (key, window_start(minutes))).fetchone()[0]
            if n >= limit:
                return False
            conn.execute("INSERT INTO throttle (key, at) VALUES (?, ?)", (key, iso(now())))
            return True

    def check_ip(self, ip: str) -> None:
        if not self._allow(f"ip:{ip}", self.s.requests_per_ip_hour):
            self.audit.record("rate-limit", "denied", ip=ip, detail="too many requests from this address")
            raise StationError("Too many attempts from this network. Please wait an hour or contact "
                               f"{self.s.support_contact}.")

    # --- Lookups -------------------------------------------------------------------------------
    def _user(self, cfg: DomainConfig, username: str) -> tuple[dirmod.Directory, Optional[DirUser]]:
        d = self.directory(cfg)
        return d, d.find(username)

    def _usable(self, user: Optional[DirUser]) -> bool:
        return user is not None and not user.disabled and not user.protected

    def _enrollment(self, domain_id: str, username: str) -> dict:
        r = self.db.read().execute("SELECT * FROM enrollments WHERE domain_id = ? AND username = ?",
                                   (domain_id, username.lower())).fetchone()
        if not r:
            return {}
        ctx = f"enroll:{domain_id}:{username.lower()}"
        return {"email": self.box.open(r["email_enc"], ctx + ":email"), "sms": self.box.open(r["phone_enc"], ctx + ":sms")}

    def _notice(self, cfg: DomainConfig, user: DirUser, what: str) -> list[Message]:
        body = (f"Hello {user.display_name or user.username},\n\n{what} for your {cfg.name} account "
                f"({user.username}) at {now().strftime('%Y-%m-%d %H:%M UTC')} through {self.s.app_name}.\n\n"
                f"If this wasn't you, contact {self.s.support_contact} immediately.")
        recipients = {e for e in [user.email, self._enrollment(cfg.id, user.username).get("email")] if e}
        return [Message("email", r, f"{self.s.app_name}: {what.lower()}", body) for r in sorted(recipients)]

    # --- Change (knows current password) ----------------------------------------------------------
    def change(self, ip: str, domain_id: str, username: str, old: str, new: str, confirm: str) -> list[Message]:
        self.check_ip(ip)
        cfg = self.domain(domain_id)
        if new != confirm:
            raise StationError("The new passwords don't match.")
        d, user = self._user(cfg, username)
        if not self._usable(user):
            self.audit.record("change", "denied", ip=ip, domain_id=domain_id, username=username,
                              detail="unknown, disabled or protected account")
            raise StationError("The user name or current password is not correct.")
        try:
            check_basic_policy(cfg, user, new)
            d.change_password(user, old, new)
        except PolicyError as exc:
            self.audit.record("change", "denied", ip=ip, domain_id=domain_id, username=user.username, detail="policy")
            raise StationError(str(exc))
        except DirectoryError as exc:
            self.audit.record("change", "error", ip=ip, domain_id=domain_id, username=user.username, detail=str(exc))
            raise StationError(str(exc))
        self.audit.record("change", "ok", ip=ip, domain_id=domain_id, username=user.username)
        return self._notice(cfg, user, "Your password was changed")

    # --- Enrolment (knows current password) ----------------------------------------------------------
    def _new_flow(self, purpose: str, domain_id: str, username: str, **extra) -> str:
        flow_id = secrets.token_urlsafe(24)
        with self.db.tx() as conn:
            conn.execute("DELETE FROM flows WHERE expires_at < ?", (iso(now()),))
            conn.execute("INSERT INTO flows (id, purpose, domain_id, username, channel, pending_enc, verified, expires_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (flow_id, purpose, domain_id, username.lower(), extra.get("channel"), extra.get("pending_enc"),
                          int(extra.get("verified", 0)), iso(now() + timedelta(minutes=FLOW_MINUTES))))
        return flow_id

    def flow(self, flow_id: Optional[str], purpose: str) -> dict:
        row = self.db.read().execute("SELECT * FROM flows WHERE id = ?", (flow_id or "",)).fetchone()
        if row is None or row["purpose"] != purpose or parse(row["expires_at"]) < now():
            raise StationError("This request has expired. Please start again.")
        return dict(row)

    def _issue_code(self, flow_id: str, channel: str, recipient: str, cfg: DomainConfig, why: str) -> Message:
        code = new_code()
        with self.db.tx() as conn:
            conn.execute("UPDATE flows SET channel = ?, code_hash = ?, code_expires = ?, attempts = 0 WHERE id = ?",
                         (channel, code_hash(self.s.secret_key, flow_id, code),
                          iso(now() + timedelta(minutes=self.s.code_ttl_minutes)), flow_id))
        text = (f"{self.s.app_name} code: {code}. It's valid for {self.s.code_ttl_minutes} minutes and is for {why} "
                f"on your {cfg.name} account. Never share it - IT will never ask for it.")
        return Message(channel, recipient, f"{self.s.app_name} verification code", text)

    def enroll_start(self, ip: str, domain_id: str, username: str, password: str) -> str:
        self.check_ip(ip)
        cfg = self.domain(domain_id)
        d, user = self._user(cfg, username)
        if not self._usable(user) or not d.verify_password(user, password):
            self.audit.record("enroll-signin", "denied", ip=ip, domain_id=domain_id, username=username)
            raise StationError("The user name or password is not correct. (Privileged and disabled accounts "
                               "can't use self-service.)")
        self.audit.record("enroll-signin", "ok", ip=ip, domain_id=domain_id, username=user.username)
        return self._new_flow("enroll", domain_id, user.username, verified=1)

    def enroll_send(self, ip: str, flow_id: str, channel: str, contact: str) -> list[Message]:
        flow = self.flow(flow_id, "enroll")
        cfg = self.domain(flow["domain_id"])
        value = normalize_contact(channel, contact)
        if channel == "sms" and self.s.sms_provider != "sns" and not self.s.demo_outbox:
            raise StationError("Text messages aren't available yet - please use an e-mail address.")
        if not self._allow(f"acct:{cfg.id}:{flow['username']}", self.s.sends_per_account_hour):
            raise StationError("Too many codes requested for this account. Please wait an hour.")
        ctx = f"enroll:{cfg.id}:{flow['username']}:{channel}"
        with self.db.tx() as conn:
            conn.execute("UPDATE flows SET pending_enc = ? WHERE id = ?", (self.box.seal(value, ctx), flow_id))
        self.audit.record("enroll-code-sent", "ok", ip=ip, domain_id=cfg.id, username=flow["username"], detail=channel)
        return [self._issue_code(flow_id, channel, value, cfg, "confirming your recovery " +
                                 ("e-mail" if channel == "email" else "mobile number"))]

    def _check_code(self, flow: dict, code: str) -> bool:
        if not flow.get("code_hash") or not flow.get("code_expires") or parse(flow["code_expires"]) < now():
            return False
        if flow["attempts"] >= self.s.code_max_attempts:
            return False
        ok = hmac.compare_digest(flow["code_hash"], code_hash(self.s.secret_key, flow["id"], (code or "").strip()))
        with self.db.tx() as conn:
            conn.execute("UPDATE flows SET attempts = attempts + 1, verified = CASE WHEN ? THEN 1 ELSE verified END "
                         "WHERE id = ?", (int(ok), flow["id"]))
        return ok

    def enroll_confirm(self, ip: str, flow_id: str, code: str) -> list[Message]:
        flow = self.flow(flow_id, "enroll")
        if not self._check_code(flow, code):
            self.audit.record("enroll-confirm", "denied", ip=ip, domain_id=flow["domain_id"], username=flow["username"])
            raise StationError("That code isn't correct or has expired.")
        channel, user_ctx = flow["channel"], f"enroll:{flow['domain_id']}:{flow['username']}"
        value = self.box.open(flow["pending_enc"], f"{user_ctx}:{channel}")
        column = "email_enc" if channel == "email" else "phone_enc"
        with self.db.tx() as conn:
            conn.execute("INSERT INTO enrollments (domain_id, username, updated_at) VALUES (?, ?, ?) "
                         "ON CONFLICT(domain_id, username) DO NOTHING", (flow["domain_id"], flow["username"], iso(now())))
            conn.execute(f"UPDATE enrollments SET {column} = ?, updated_at = ? WHERE domain_id = ? AND username = ?",
                         (self.box.seal(value, f"{user_ctx}:{channel}"), iso(now()), flow["domain_id"], flow["username"]))
            conn.execute("UPDATE flows SET code_hash = NULL, pending_enc = NULL WHERE id = ?", (flow_id,))
        self.audit.record("enroll", "ok", ip=ip, domain_id=flow["domain_id"], username=flow["username"], detail=channel)
        cfg = self.domain(flow["domain_id"])
        user = self.directory(cfg).find(flow["username"])
        what = "A recovery " + ("e-mail address" if channel == "email" else "mobile number") + " was registered"
        return self._notice(cfg, user, what) if user else []

    def enrollment_summary(self, flow_id: str) -> dict:
        flow = self.flow(flow_id, "enroll")
        e = self._enrollment(flow["domain_id"], flow["username"])
        return {"email": mask_email(e["email"]) if e.get("email") else None,
                "sms": mask_phone(e["sms"]) if e.get("sms") else None}

    # --- Forgot password / unlock (proves identity with an enrolled contact) ------------------------
    def recovery_start(self, ip: str, purpose: str, domain_id: str, username: str, channel: str,
                       contact: str) -> tuple[str, list[Message]]:
        """Always returns a flow; a code is only sent when everything matches."""
        assert purpose in ("forgot", "unlock")
        self.check_ip(ip)
        cfg = self.domain(domain_id)
        value = normalize_contact(channel, contact)
        d, user = self._user(cfg, username)
        flow_id = self._new_flow(purpose, domain_id, user.username if user else username)
        enrolled = self._enrollment(domain_id, user.username) if self._usable(user) else {}
        stored = enrolled.get(channel)
        if not stored or not hmac.compare_digest(stored, value):
            reason = ("unknown account" if user is None else "disabled account" if user.disabled
                      else "protected account" if user.protected else "contact does not match enrolment")
            self.audit.record(f"{purpose}-request", "denied", ip=ip, domain_id=domain_id, username=username, detail=reason)
            return flow_id, []
        if not self._allow(f"acct:{cfg.id}:{user.username.lower()}", self.s.sends_per_account_hour):
            self.audit.record(f"{purpose}-request", "denied", ip=ip, domain_id=domain_id, username=user.username,
                              detail="too many codes")
            return flow_id, []
        self.audit.record(f"{purpose}-code-sent", "ok", ip=ip, domain_id=domain_id, username=user.username, detail=channel)
        why = "resetting your password" if purpose == "forgot" else "unlocking your account"
        return flow_id, [self._issue_code(flow_id, channel, value, cfg, why)]

    def recovery_verify(self, ip: str, flow_id: str, purpose: str, code: str) -> list[Message]:
        flow = self.flow(flow_id, purpose)
        if not self._check_code(flow, code):
            self.audit.record(f"{purpose}-verify", "denied", ip=ip, domain_id=flow["domain_id"], username=flow["username"])
            raise StationError("That code isn't correct or has expired. You can request a new one.")
        self.audit.record(f"{purpose}-verify", "ok", ip=ip, domain_id=flow["domain_id"], username=flow["username"])
        if purpose == "unlock":
            cfg = self.domain(flow["domain_id"])
            d, user = self._user(cfg, flow["username"])
            if not self._usable(user):
                raise StationError("This account can't be unlocked here.")
            try:
                d.unlock(user)
            except DirectoryError as exc:
                self.audit.record("unlock", "error", ip=ip, domain_id=cfg.id, username=user.username, detail=str(exc))
                raise StationError(str(exc))
            self._close(flow_id)
            self.audit.record("unlock", "ok", ip=ip, domain_id=cfg.id, username=user.username)
            return self._notice(cfg, user, "Your account was unlocked")
        return []

    def reset(self, ip: str, flow_id: str, new: str, confirm: str) -> list[Message]:
        flow = self.flow(flow_id, "forgot")
        if not flow["verified"]:
            raise StationError("Please enter the code we sent first.")
        if new != confirm:
            raise StationError("The new passwords don't match.")
        cfg = self.domain(flow["domain_id"])
        d, user = self._user(cfg, flow["username"])
        if not self._usable(user):
            raise StationError("This account can't be reset here.")
        try:
            check_basic_policy(cfg, user, new)
            d.reset_password(user, new)
        except PolicyError as exc:
            self.audit.record("reset", "denied", ip=ip, domain_id=cfg.id, username=user.username, detail="policy")
            raise StationError(str(exc))
        except DirectoryError as exc:
            self.audit.record("reset", "error", ip=ip, domain_id=cfg.id, username=user.username, detail=str(exc))
            raise StationError(str(exc))
        self._close(flow_id)
        self.audit.record("reset", "ok", ip=ip, domain_id=cfg.id, username=user.username)
        return self._notice(cfg, user, "Your password was reset")

    def _close(self, flow_id: str) -> None:
        with self.db.tx() as conn:
            conn.execute("DELETE FROM flows WHERE id = ?", (flow_id,))

    # --- Admin ---------------------------------------------------------------------------------------
    def ensure_admin(self) -> None:
        with self.db.tx() as conn:
            if conn.execute("SELECT 1 FROM admins").fetchone() or not self.s.admin_password:
                return
            conn.execute("INSERT INTO admins (username, password_hash) VALUES ('admin', ?)",
                         (hash_password(self.s.admin_password),))

    def admin_login(self, ip: str, username: str, password: str) -> bool:
        """Locks for 15 minutes after 5 failures (temporary, so a single admin can't be stranded)."""
        locked = False
        with self.db.tx() as conn:
            r = conn.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()
            if r is None:
                ok = False
            elif r["locked_until"] and parse(r["locked_until"]) > now():
                ok, locked = False, True
            else:
                ok = verify_password(password, r["password_hash"])
                failed = 0 if ok else r["failed"] + 1
                conn.execute("UPDATE admins SET failed = ?, locked_until = ? WHERE username = ?",
                             (failed, iso(now() + timedelta(minutes=15)) if failed >= 5 else None, username))
        self.audit.record("admin-login", "ok" if ok else "denied", ip=ip, username=username,
                          detail="temporarily locked" if locked else "")
        return ok

    def reset_admin(self, password: str) -> None:
        """Set 'admin' back to the given password and clear any lockout (server console only)."""
        with self.db.tx() as conn:
            conn.execute("INSERT INTO admins (username, password_hash) VALUES ('admin', ?) ON CONFLICT(username) "
                         "DO UPDATE SET password_hash = excluded.password_hash, failed = 0, locked_until = NULL",
                         (hash_password(password),))
        self.audit.record("admin-reset", "ok", username="admin", detail="from server console")

    def remove_enrollment(self, admin: str, ip: str, domain_id: str, username: str) -> bool:
        with self.db.tx() as conn:
            n = conn.execute("DELETE FROM enrollments WHERE domain_id = ? AND username = ?",
                             (domain_id, username.lower())).rowcount
        self.audit.record("enrollment-removed", "ok" if n else "error", ip=ip, domain_id=domain_id, username=username,
                          detail=f"by {admin}")
        return bool(n)


# --- Delivery ------------------------------------------------------------------------------------------

class Sender:
    def __init__(self, settings, db):
        self.s, self.db = settings, db

    def send(self, msg: Message) -> None:
        try:
            if self.s.demo_outbox:
                with self.db.tx() as conn:
                    conn.execute("INSERT INTO outbox (at, channel, recipient, subject, body) VALUES (?, ?, ?, ?, ?)",
                                 (iso(now()), msg.channel, msg.recipient, msg.subject, msg.body))
            elif msg.channel == "email":
                self._email(msg)
            elif msg.channel == "sms":
                self._sms(msg)
        except Exception:  # never surface delivery details to the requester
            log.exception("Delivery of a %s message failed", msg.channel)

    def _email(self, msg: Message) -> None:
        if not self.s.smtp_host:
            log.warning("No SMTP server configured; e-mail to %s not sent", mask_email(msg.recipient))
            return
        em = EmailMessage()
        em["From"], em["To"], em["Subject"] = self.s.smtp_from, msg.recipient, msg.subject
        em.set_content(msg.body)
        with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=30) as smtp:
            if self.s.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if self.s.smtp_user:
                smtp.login(self.s.smtp_user, self.s.smtp_password)
            smtp.send_message(em)

    def _sms(self, msg: Message) -> None:
        if self.s.sms_provider != "sns":
            log.warning("SMS is not configured; text to %s not sent", mask_phone(msg.recipient))
            return
        import boto3
        attrs = {"AWS.SNS.SMS.SMSType": {"DataType": "String", "StringValue": "Transactional"}}
        if self.s.sms_sender_id:
            attrs["AWS.SNS.SMS.SenderID"] = {"DataType": "String", "StringValue": self.s.sms_sender_id}
        boto3.client("sns", region_name=self.s.aws_region or None).publish(
            PhoneNumber=msg.recipient, Message=msg.body, MessageAttributes=attrs)
