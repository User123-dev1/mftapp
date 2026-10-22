"""
Single sign-on: identity-provider registry, login flows and just-in-time
user provisioning with IdP-group -> local group / role mapping.

Common provider settings (both protocols):
    username_claim      claim / attribute used as username (OIDC default
                        "preferred_username"; SAML default: NameID)
    email_claim         default "email"
    display_name_claim  default "name"
    groups_claim        default "groups"
    group_mappings      {"<IdP group>": "<local group name>"}; membership in
                        mapped local groups is synced at every login
    admin_groups        IdP groups whose members get the administrator role
    allowed_email_domains  optional allow-list, e.g. ["corp.example.com"]

OIDC settings: issuer, client_id, client_secret, scopes, discovery_url,
               allowed_algorithms, prompt, clock_skew_seconds
SAML settings: idp_entity_id, idp_sso_url, idp_x509_cert (PEM or list of PEMs),
               sp_entity_id, clock_skew_seconds
"""

import json
from datetime import timedelta
from typing import Callable, List, Optional

import httpx

from ..crypto import random_token
from ..db import commit_now, now_iso, put_one_time, take_one_time
from ..vault import VaultError
from .oidc import OIDCClient, OIDCError
from .saml import SAMLError, SAMLServiceProvider

LOGIN_STATE_TTL = timedelta(minutes=10)
EXCHANGE_CODE_TTL = timedelta(seconds=60)

_REQUIRED = {
    "oidc": ("issuer", "client_id"),
    "saml": ("idp_entity_id", "idp_sso_url", "idp_x509_cert"),
}


class SSOError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


def _as_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


class SSOService:
    def __init__(self, keyring, settings, audit, tokens,
                 http_factory: Callable[[], httpx.Client] = httpx.Client):
        self._keyring = keyring
        self._settings = settings
        self._audit = audit
        self._tokens = tokens
        self._http_factory = http_factory

    # ------------------------------------------------------------------ provider admin

    def create_provider(self, conn, p, name: str, type_: str, config: dict,
                        enabled: bool = True, jit_provisioning: bool = True,
                        default_role: str = "user") -> dict:
        if not p.is_admin:
            raise VaultError("administrator role required", 403)
        if type_ not in _REQUIRED:
            raise VaultError("type must be 'oidc' or 'saml'")
        if not name.replace("-", "").replace("_", "").isalnum() or name == "local":
            raise VaultError("provider name must be alphanumeric (dashes/underscores allowed)")
        if default_role not in ("user", "administrator"):
            raise VaultError("invalid default_role")
        config = dict(config)
        missing = [k for k in _REQUIRED[type_] if not config.get(k)]
        if missing:
            raise VaultError(f"missing provider settings: {missing}")
        client_secret = config.pop("client_secret", None)
        if conn.execute("SELECT 1 FROM sso_providers WHERE name = ?", (name,)).fetchone():
            raise VaultError("provider already exists", 409)
        conn.execute(
            "INSERT INTO sso_providers (name, type, config_json, client_secret_enc, enabled, "
            "jit_provisioning, default_role) VALUES (?,?,?,?,?,?,?)",
            (name, type_, json.dumps(config),
             self._keyring.encrypt_value(client_secret, f"sso:{name}") if client_secret else None,
             int(enabled), int(jit_provisioning), default_role))
        self._audit.record(conn, "SSO_PROVIDER_CREATE", p, "sso_provider", name, {"type": type_})
        return self.describe(conn, name)

    def update_provider(self, conn, p, name: str, enabled: Optional[bool] = None,
                        config: Optional[dict] = None) -> dict:
        if not p.is_admin:
            raise VaultError("administrator role required", 403)
        row = self._provider(conn, name, require_enabled=False)
        if enabled is not None:
            conn.execute("UPDATE sso_providers SET enabled = ? WHERE name = ?", (int(enabled), name))
        if config:
            config = dict(config)
            secret = config.pop("client_secret", None)
            merged = {**json.loads(row["config_json"]), **config}
            conn.execute("UPDATE sso_providers SET config_json = ? WHERE name = ?",
                         (json.dumps(merged), name))
            if secret:
                conn.execute("UPDATE sso_providers SET client_secret_enc = ? WHERE name = ?",
                             (self._keyring.encrypt_value(secret, f"sso:{name}"), name))
        self._audit.record(conn, "SSO_PROVIDER_UPDATE", p, "sso_provider", name,
                           {"enabled": enabled, "config_keys": sorted(config or {})})
        return self.describe(conn, name)

    def list_providers(self, conn, include_config: bool) -> List[dict]:
        rows = conn.execute("SELECT name FROM sso_providers ORDER BY name").fetchall()
        out = [self.describe(conn, r["name"]) for r in rows]
        if not include_config:
            out = [{"name": d["name"], "type": d["type"], "login_url": d["login_url"]}
                   for d in out if d["enabled"]]
        return out

    def describe(self, conn, name: str) -> dict:
        row = self._provider(conn, name, require_enabled=False)
        base = f"{self._settings.public_url}/sso/{name}"
        out = {"name": row["name"], "type": row["type"], "enabled": bool(row["enabled"]),
               "jit_provisioning": bool(row["jit_provisioning"]),
               "default_role": row["default_role"], "config": json.loads(row["config_json"]),
               "has_client_secret": row["client_secret_enc"] is not None,
               "login_url": f"{base}/login"}
        if row["type"] == "oidc":
            out["redirect_uri"] = f"{base}/callback"
        else:
            out["acs_url"] = f"{base}/acs"
            out["metadata_url"] = f"{base}/metadata"
        return out

    @staticmethod
    def _provider(conn, name: str, require_enabled: bool = True):
        row = conn.execute("SELECT * FROM sso_providers WHERE name = ?", (name,)).fetchone()
        if row is None or (require_enabled and not row["enabled"]):
            raise SSOError("unknown or disabled identity provider", 404)
        return row

    def _oidc_client(self, row) -> OIDCClient:
        secret = None
        if row["client_secret_enc"]:
            secret = self._keyring.decrypt_value(row["client_secret_enc"], f"sso:{row['name']}")
        return OIDCClient(json.loads(row["config_json"]), secret,
                          f"{self._settings.public_url}/sso/{row['name']}/callback",
                          self._http_factory())

    def _saml_sp(self, row) -> SAMLServiceProvider:
        base = f"{self._settings.public_url}/sso/{row['name']}"
        return SAMLServiceProvider(json.loads(row["config_json"]), f"{base}/metadata",
                                   f"{base}/acs")

    def saml_metadata(self, conn, name: str) -> str:
        row = self._provider(conn, name)
        if row["type"] != "saml":
            raise SSOError("not a SAML provider", 404)
        return self._saml_sp(row).metadata()

    # ------------------------------------------------------------------ login flows

    def begin_login(self, conn, name: str) -> str:
        """Return the IdP URL to redirect the browser to."""
        row = self._provider(conn, name)
        state = random_token()
        if row["type"] == "oidc":
            nonce, verifier = random_token(), random_token(48)
            put_one_time(conn, state, "sso-state",
                         {"provider": name, "nonce": nonce, "verifier": verifier}, LOGIN_STATE_TTL)
            return self._oidc_client(row).authorization_url(state, nonce, verifier)
        request_id = "_" + random_token(20)
        put_one_time(conn, state, "sso-state",
                     {"provider": name, "request_id": request_id}, LOGIN_STATE_TTL)
        return self._saml_sp(row).redirect_url(request_id, state)

    def _take_state(self, conn, name: str, state: Optional[str]) -> dict:
        data = take_one_time(conn, state or "", "sso-state")
        if data is None or data["provider"] != name:
            raise SSOError("invalid or expired SSO state; start the login again", 400)
        return data

    def complete_oidc(self, conn, name: str, code: Optional[str], state: Optional[str],
                      error: Optional[str], ip: Optional[str]) -> dict:
        row = self._provider(conn, name)
        if row["type"] != "oidc":
            raise SSOError("not an OIDC provider", 404)
        data = self._take_state(conn, name, state)
        if error or not code:
            self._fail(conn, name, ip, f"idp_error:{error or 'missing_code'}")
        try:
            claims = self._oidc_client(row).complete(code, data["verifier"], data["nonce"])
        except (OIDCError, httpx.HTTPError) as exc:
            self._fail(conn, name, ip, str(exc))
        config = json.loads(row["config_json"])
        identity = {
            "external_id": claims["sub"],
            "username": claims.get(config.get("username_claim", "preferred_username"))
            or claims.get("email") or claims["sub"],
            "email": claims.get(config.get("email_claim", "email")),
            "display_name": claims.get(config.get("display_name_claim", "name")),
            "groups": _as_list(claims.get(config.get("groups_claim", "groups"))),
        }
        return self._finish(conn, row, identity, ip)

    def complete_saml(self, conn, name: str, saml_response: Optional[str],
                      relay_state: Optional[str], ip: Optional[str]) -> dict:
        row = self._provider(conn, name)
        if row["type"] != "saml":
            raise SSOError("not a SAML provider", 404)
        data = self._take_state(conn, name, relay_state)
        if not saml_response:
            self._fail(conn, name, ip, "missing SAMLResponse")
        try:
            result = self._saml_sp(row).parse_response(saml_response, data["request_id"])
        except SAMLError as exc:
            self._fail(conn, name, ip, str(exc))

        # Replay protection: each assertion ID may be consumed once
        seen_key = f"saml:{name}:{result['assertion_id']}"
        if conn.execute("SELECT 1 FROM one_time_values WHERE key = ?", (seen_key,)).fetchone():
            self._fail(conn, name, ip, "assertion replay detected")
        put_one_time(conn, seen_key, "saml-seen", {}, timedelta(hours=24))

        config = json.loads(row["config_json"])
        attrs = result["attributes"]

        def first(key: Optional[str]) -> Optional[str]:
            values = attrs.get(key) if key else None
            return values[0] if values else None

        identity = {
            "external_id": result["name_id"],
            "username": first(config.get("username_claim")) or result["name_id"],
            "email": first(config.get("email_claim", "email")),
            "display_name": first(config.get("display_name_claim", "name")),
            "groups": attrs.get(config.get("groups_claim", "groups"), []),
        }
        return self._finish(conn, row, identity, ip)

    def _fail(self, conn, name: str, ip: Optional[str], reason: str):
        self._audit.record(conn, "SSO_LOGIN_FAILED", None, "sso_provider", name,
                           {"reason": reason}, ip=ip, username="anonymous")
        commit_now(conn)
        raise SSOError("single sign-on failed", 401)

    # ------------------------------------------------------------------ provisioning

    def _finish(self, conn, provider, identity: dict, ip: Optional[str]) -> dict:
        name = provider["name"]
        config = json.loads(provider["config_json"])
        domains = [d.lower() for d in config.get("allowed_email_domains") or []]
        if domains:
            email = (identity.get("email") or "").lower()
            if email.rpartition("@")[2] not in domains:
                self._fail(conn, name, ip, f"email domain not allowed: {email}")

        user = conn.execute("SELECT * FROM users WHERE auth_source = ? AND external_id = ?",
                            (name, identity["external_id"])).fetchone()
        if user is None:
            # Pre-provisioned account awaiting first SSO login?
            user = conn.execute(
                "SELECT * FROM users WHERE auth_source = ? AND external_id IS NULL AND username = ?",
                (name, identity["username"])).fetchone()
            if user is not None:
                conn.execute("UPDATE users SET external_id = ? WHERE id = ?",
                             (identity["external_id"], user["id"]))
        if user is None:
            if not provider["jit_provisioning"]:
                self._fail(conn, name, ip, f"user {identity['username']} is not provisioned")
            if conn.execute("SELECT 1 FROM users WHERE username = ?",
                            (identity["username"],)).fetchone():
                # Never link an SSO identity to an existing account owned by another source
                self._fail(conn, name, ip, f"username {identity['username']} already in use")
            cur = conn.execute(
                "INSERT INTO users (username, display_name, email, auth_source, external_id, role, "
                "created_at) VALUES (?,?,?,?,?,?,?)",
                (identity["username"], identity["display_name"], identity["email"], name,
                 identity["external_id"], provider["default_role"], now_iso()))
            user_id = cur.lastrowid
            self._audit.record(conn, "USER_PROVISIONED", None, "user", user_id,
                               {"provider": name, "username": identity["username"]},
                               ip=ip, username="system")
        else:
            user_id = user["id"]
            if not user["enabled"]:
                self._fail(conn, name, ip, f"account {user['username']} disabled")

        idp_groups = set(identity["groups"])
        admin_groups = set(config.get("admin_groups") or [])
        role = "administrator" if idp_groups & admin_groups else provider["default_role"]
        conn.execute("UPDATE users SET role = ?, email = COALESCE(?, email), "
                     "display_name = COALESCE(?, display_name) WHERE id = ?",
                     (role, identity["email"], identity["display_name"], user_id))
        self._sync_groups(conn, name, user_id, idp_groups, config.get("group_mappings") or {})

        username = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()[0]
        self._audit.record(conn, "LOGIN", None, "user", user_id,
                           {"method": "sso", "provider": name, "role": role},
                           ip=ip, username=username)
        if self._settings.sso_success_redirect:
            # Browser flow: hand the front end a one-time code, never a token in the URL
            code = random_token()
            put_one_time(conn, code, "sso-exchange", {"user_id": user_id, "provider": name},
                         EXCHANGE_CODE_TTL)
            sep = "&" if "?" in self._settings.sso_success_redirect else "?"
            return {"redirect": f"{self._settings.sso_success_redirect}{sep}code={code}"}
        return self._tokens.issue(conn, user_id, f"sso:{name}")

    @staticmethod
    def _sync_groups(conn, provider: str, user_id: int, idp_groups: set, mappings: dict) -> None:
        """Make membership of every mapped local group match the IdP's group claims."""
        mapped_ids, wanted = set(), set()
        for idp_group, local_name in mappings.items():
            row = conn.execute("SELECT id FROM groups WHERE name = ?", (local_name,)).fetchone()
            group_id = row["id"] if row else conn.execute(
                "INSERT INTO groups (name, description, source) VALUES (?,?,?)",
                (local_name, f"Synced from {provider}", provider)).lastrowid
            mapped_ids.add(group_id)
            if idp_group in idp_groups:
                wanted.add(group_id)
        for gid in mapped_ids - wanted:
            conn.execute("DELETE FROM group_members WHERE group_id = ? AND user_id = ?",
                         (gid, user_id))
        for gid in wanted:
            conn.execute("INSERT OR IGNORE INTO group_members VALUES (?, ?)", (gid, user_id))

    def exchange(self, conn, code: str) -> dict:
        data = take_one_time(conn, code, "sso-exchange")
        if data is None:
            raise SSOError("invalid or expired code", 400)
        return self._tokens.issue(conn, data["user_id"], f"sso:{data['provider']}")
