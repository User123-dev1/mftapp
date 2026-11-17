"""Directory connectors: where accounts and passwords actually live.

ADDirectory  - Active Directory over LDAPS (GMP domain, business domain synced to Entra ID).
DemoDirectory - built-in test accounts for trying the app without a domain (PS_DEMO=true).

Rules enforced for every connector:
- Privileged accounts (AD adminCount=1, or members of the configured protected groups) are never
  changed, reset or unlocked through self-service.
- Disabled accounts are refused.
- If an "allowed group" is configured, only its members (including nested) can use the station.
"""

import json
import re
import ssl
from dataclasses import dataclass, field
from typing import Optional

from .security import hash_password, verify_password

AD_ADMIN_GROUPS = ("Domain Admins", "Enterprise Admins", "Schema Admins", "Administrators",
                   "Account Operators", "Backup Operators", "Server Operators", "Print Operators")


class DirectoryError(Exception):
    """Something went wrong; the message is safe to show to the user."""


class PolicyError(DirectoryError):
    """The directory rejected the new password (complexity, length, history or minimum age)."""


@dataclass
class DirUser:
    key: str                      # DN for AD
    username: str
    display_name: str = ""
    upn: str = ""
    email: str = ""               # corporate mailbox, for notices
    locked: bool = False
    disabled: bool = False
    protected: bool = False


@dataclass
class DomainConfig:
    id: str
    name: str
    kind: str                     # "ad" | "demo"
    settings: dict = field(default_factory=dict)
    secret: Optional[str] = None  # bind password (decrypted)
    enabled: bool = True
    sort: int = 0

    @property
    def min_length(self) -> int:
        return int(self.settings.get("min_length") or 12)

    @property
    def policy_text(self) -> str:
        return self.settings.get("policy_text") or (
            f"At least {self.min_length} characters, with three of: upper-case, lower-case, digit, symbol. "
            "Must not contain your name or user name, or match a recent password.")


def check_basic_policy(cfg: DomainConfig, user: DirUser, password: str) -> None:
    """Friendly pre-check; the directory itself remains the authority."""
    if len(password) < cfg.min_length:
        raise PolicyError(f"Use at least {cfg.min_length} characters.")
    classes = sum([bool(re.search(r"[a-z]", password)), bool(re.search(r"[A-Z]", password)),
                   bool(re.search(r"\d", password)), bool(re.search(r"[^A-Za-z0-9]", password))])
    if classes < 3:
        raise PolicyError("Use at least three of: upper-case, lower-case, digit, symbol.")
    low = password.lower()
    parts = [user.username] + [p for p in re.split(r"[\s.,_-]+", user.display_name or "") if len(p) >= 3]
    if any(p and p.lower() in low for p in parts):
        raise PolicyError("The password must not contain your name or user name.")


class Directory:
    def __init__(self, cfg: DomainConfig):
        self.cfg = cfg

    def find(self, username: str) -> Optional[DirUser]: ...
    def verify_password(self, user: DirUser, password: str) -> bool: ...
    def change_password(self, user: DirUser, old: str, new: str) -> None: ...
    def reset_password(self, user: DirUser, new: str) -> None: ...
    def unlock(self, user: DirUser) -> None: ...
    def test(self) -> str: ...


# --- Active Directory ----------------------------------------------------------------------

def _escape(value: str) -> str:
    """RFC 4515 escaping for LDAP filter values."""
    out = []
    for ch in value:
        if ch in "\\*()\x00":
            out.append("\\%02x" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


_AD_DATA = re.compile(r"data\s+([0-9a-fA-F]+)")


def _ad_error(conn, default: str) -> DirectoryError:
    """Translate an AD LDAP result into a message for the user (no internal details)."""
    result = getattr(conn, "result", None) or {}
    message = str(result.get("message", ""))
    desc = str(result.get("description", ""))
    code = (_AD_DATA.search(message).group(1).lower() if _AD_DATA.search(message) else "")
    if "0000052d" in message.lower() or desc == "constraintViolation" or desc == "unwillingToPerform":
        return PolicyError("The domain rejected this password: it doesn't meet the password policy, "
                           "matches a recent password, or the password was changed too recently.")
    if code == "775":
        return DirectoryError("The account is locked. Use 'Unlock my account' first.")
    if code in ("52e", "532", "773"):
        return DirectoryError("The current password is not correct.")
    if code == "533":
        return DirectoryError("This account is disabled.")
    return DirectoryError(default)


class ADDirectory(Directory):
    """Settings: hosts (list), port (636), base_dn, bind_user (DN or UPN), login_attribute
    (sAMAccountName|userPrincipalName), allowed_group_dn, protected_groups (list of DNs), ca_pem."""

    def __init__(self, cfg: DomainConfig, connection_factory=None):
        super().__init__(cfg)
        self._factory = connection_factory or self._connect

    # Connections ---------------------------------------------------------------------
    def _servers(self):
        from ldap3 import FIRST, Server, ServerPool, Tls
        s = self.cfg.settings
        tls = Tls(validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLS_CLIENT,
                  ca_certs_data=s.get("ca_pem") or None)
        hosts = s.get("hosts") or []
        if isinstance(hosts, str):
            hosts = [h.strip() for h in hosts.split(",") if h.strip()]
        if not hosts:
            raise DirectoryError("This domain has no domain controllers configured.")
        port = int(s.get("port") or 636)
        servers = [Server(h, port=port, use_ssl=True, tls=tls, connect_timeout=5) for h in hosts]
        return ServerPool(servers, FIRST, active=1, exhaust=True)

    def _connect(self, user: str, password: str):
        from ldap3 import SIMPLE, Connection
        return Connection(self._servers(), user=user, password=password, authentication=SIMPLE,
                          receive_timeout=10, raise_exceptions=False)

    def _service(self):
        conn = self._factory(self.cfg.settings.get("bind_user", ""), self.cfg.secret or "")
        if not conn.bind():
            raise DirectoryError("Password Station can't reach this domain right now. Please try later.")
        return conn

    # Operations ------------------------------------------------------------------------
    def find(self, username: str) -> Optional[DirUser]:
        username = username.strip()
        if "\\" in username:                       # DOMAIN\\user -> user
            username = username.split("\\", 1)[1]
        if not username or len(username) > 256:
            return None
        s = self.cfg.settings
        attr = "userPrincipalName" if "@" in username else (s.get("login_attribute") or "sAMAccountName")
        flt = f"(&(objectCategory=person)(objectClass=user)({attr}={_escape(username)})"
        if s.get("allowed_group_dn"):
            # LDAP_MATCHING_RULE_IN_CHAIN: membership including nested groups
            flt += f"(memberOf:1.2.840.113556.1.4.1941:={_escape(s['allowed_group_dn'])})"
        flt += ")"
        conn = self._service()
        try:
            conn.search(s.get("base_dn", ""), flt, attributes=[
                "sAMAccountName", "userPrincipalName", "displayName", "mail", "lockoutTime",
                "userAccountControl", "adminCount", "memberOf"], size_limit=2)
            if len(conn.entries) != 1:
                return None
            e = conn.entries[0]
            vals = e.entry_attributes_as_dict
            first = lambda k: (vals.get(k) or [None])[0]  # noqa: E731
            lockout = first("lockoutTime")
            locked = bool(lockout) and not (hasattr(lockout, "year") and lockout.year <= 1601) and str(lockout) not in ("0",)
            uac = int(first("userAccountControl") or 0)
            groups = [str(g).lower() for g in vals.get("memberOf") or []]
            protected_dns = [g.lower() for g in s.get("protected_groups") or []]
            protected = (str(first("adminCount") or "0") == "1"
                         or any(g in groups for g in protected_dns)
                         or any(g.startswith(f"cn={n.lower()},") for g in groups for n in AD_ADMIN_GROUPS))
            return DirUser(key=e.entry_dn, username=str(first("sAMAccountName") or username),
                           display_name=str(first("displayName") or ""), upn=str(first("userPrincipalName") or ""),
                           email=str(first("mail") or ""), locked=locked, disabled=bool(uac & 2),
                           protected=protected)
        finally:
            conn.unbind()

    def verify_password(self, user: DirUser, password: str) -> bool:
        if not password:
            return False
        conn = self._factory(user.upn or user.key, password)
        try:
            return bool(conn.bind())
        finally:
            conn.unbind()

    def change_password(self, user: DirUser, old: str, new: str) -> None:
        """User-initiated change (delete old + add new on unicodePwd): AD itself checks the old
        password and enforces history and minimum age. Sent over the service connection because a
        user whose password has expired can't bind with it - and that's when they need to change it."""
        conn = self._service()
        try:
            if not conn.extend.microsoft.modify_password(user.key, new, old):
                raise _ad_error(conn, "The current password is not correct, or the domain didn't accept the change.")
        finally:
            conn.unbind()

    def reset_password(self, user: DirUser, new: str) -> None:
        """Reset by the service account after identity verification; also clears a lockout."""
        conn = self._service()
        try:
            if not conn.extend.microsoft.modify_password(user.key, new):
                raise _ad_error(conn, "The domain didn't accept the new password.")
            conn.extend.microsoft.unlock_account(user.key)
        finally:
            conn.unbind()

    def unlock(self, user: DirUser) -> None:
        conn = self._service()
        try:
            if not conn.extend.microsoft.unlock_account(user.key):
                raise _ad_error(conn, "The domain didn't unlock the account.")
        finally:
            conn.unbind()

    def test(self) -> str:
        conn = self._service()
        try:
            conn.search(self.cfg.settings.get("base_dn", ""), "(objectClass=domain)", attributes=["name"], size_limit=1)
            return f"Connected and signed in as the service account. Base DN reachable: {bool(conn.entries)}."
        finally:
            conn.unbind()


# --- Demo directory (trial only) --------------------------------------------------------------

DEMO_USERS = [
    # username, display name, corporate e-mail, password, protected
    ("jdoe", "John Doe", "john.doe@abelzeta.com", "Welcome-Demo-2026", False),
    ("asmith", "Alice Smith", "alice.smith@abelzeta.com", "Welcome-Demo-2026", False),
    ("da-admin", "Domain Administrator", "", "Welcome-Demo-2026", True),
]


class DemoDirectory(Directory):
    """Accounts stored in Password Station's own database, mimicking AD's behaviour:
    lockout after 5 wrong passwords, history of 5, and the same policy checks."""

    def __init__(self, cfg: DomainConfig, db):
        super().__init__(cfg)
        self.db = db

    @staticmethod
    def seed(db, domain_id: str) -> None:
        with db.tx() as conn:
            for u, name, mail, pw, prot in DEMO_USERS:
                conn.execute("INSERT OR IGNORE INTO demo_users (domain_id, username, display_name, email, password_hash, "
                             "protected) VALUES (?, ?, ?, ?, ?, ?)",
                             (domain_id, u, name, mail.replace("@", f".{domain_id}@") if mail else "", hash_password(pw, 1000), int(prot)))

    def _row(self, username: str):
        return self.db.read().execute("SELECT * FROM demo_users WHERE domain_id = ? AND username = ?",
                                      (self.cfg.id, username.strip().lower().split("\\")[-1])).fetchone()

    def find(self, username: str) -> Optional[DirUser]:
        r = self._row(username)
        if r is None:
            return None
        return DirUser(key=r["username"], username=r["username"], display_name=r["display_name"] or "",
                       upn=f"{r['username']}@{self.cfg.id}.demo", email=r["email"] or "", locked=bool(r["locked"]),
                       disabled=bool(r["disabled"]), protected=bool(r["protected"]))

    def verify_password(self, user: DirUser, password: str) -> bool:
        r = self._row(user.key)
        if r is None or r["locked"]:
            return False
        ok = verify_password(password, r["password_hash"])
        with self.db.tx() as conn:
            failed = 0 if ok else r["failed"] + 1
            conn.execute("UPDATE demo_users SET failed = ?, locked = ? WHERE domain_id = ? AND username = ?",
                         (failed, int(failed >= 5), self.cfg.id, r["username"]))
        return ok

    def _set(self, user: DirUser, new: str) -> None:
        r = self._row(user.key)
        history = json.loads(r["history"])
        if any(verify_password(new, h) for h in history + [r["password_hash"]]):
            raise PolicyError("The domain rejected this password: it matches one of your recent passwords.")
        with self.db.tx() as conn:
            conn.execute("UPDATE demo_users SET password_hash = ?, history = ?, failed = 0, locked = 0 "
                         "WHERE domain_id = ? AND username = ?",
                         (hash_password(new, 1000), json.dumps(([r["password_hash"]] + history)[:5]), self.cfg.id, r["username"]))

    def change_password(self, user: DirUser, old: str, new: str) -> None:
        if self._row(user.key)["locked"]:
            raise DirectoryError("The account is locked. Use 'Unlock my account' first.")
        if not self.verify_password(user, old):
            raise DirectoryError("The current password is not correct.")
        self._set(user, new)

    def reset_password(self, user: DirUser, new: str) -> None:
        self._set(user, new)

    def unlock(self, user: DirUser) -> None:
        with self.db.tx() as conn:
            conn.execute("UPDATE demo_users SET failed = 0, locked = 0 WHERE domain_id = ? AND username = ?",
                         (self.cfg.id, user.key))

    def test(self) -> str:
        n = self.db.read().execute("SELECT COUNT(*) FROM demo_users WHERE domain_id = ?", (self.cfg.id,)).fetchone()[0]
        return f"Demo directory with {n} test accounts."
