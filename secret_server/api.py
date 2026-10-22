"""
REST API for Secret Server (FastAPI).

    /api/auth/*            local login, TOTP MFA, logout, SSO code exchange
    /sso/{provider}/*      SSO browser endpoints (login, OIDC callback, SAML ACS, metadata)
    /api/sso/providers     identity provider administration
    /api/users, /api/groups
    /api/folders, /api/templates, /api/secrets, /api/access-requests
    /api/audit             audit log + chain verification
"""

from typing import Callable, Dict, List, Optional

import httpx
from fastapi import Depends, FastAPI, Form, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .audit import AuditLog
from .auth import AuthError, LocalAuthenticator, Principal, TokenService
from .config import Settings
from .crypto import CryptoError, KeyRing
from .db import Database
from .directory import Directory
from .sso import SSOError, SSOService
from .vault import Vault, VaultError


# ---------------------------------------------------------------------- schemas

class LoginIn(BaseModel):
    username: str
    password: str


class MfaVerifyIn(BaseModel):
    mfa_token: str
    code: str


class CodeIn(BaseModel):
    code: str


class UserIn(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: Optional[str] = None
    role: str = "user"
    display_name: Optional[str] = None
    email: Optional[str] = None
    auth_source: str = "local"


class UserPatch(BaseModel):
    role: Optional[str] = None
    enabled: Optional[bool] = None
    display_name: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    reset_mfa: bool = False


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = None


class MemberIn(BaseModel):
    user_id: int


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    parent_id: Optional[int] = None
    inherit_permissions: bool = True


class AclEntry(BaseModel):
    principal_type: str
    principal_id: int
    level: str


class PermissionsIn(BaseModel):
    inherit_permissions: bool
    entries: List[AclEntry] = []


class TemplateField(BaseModel):
    slug: str
    name: Optional[str] = None
    type: str = "text"
    required: bool = False


class TemplateIn(BaseModel):
    name: str
    fields: List[TemplateField]
    password_policy: Optional[dict] = None
    expiry_days: Optional[int] = None


class SecretSettings(BaseModel):
    require_checkout: Optional[bool] = None
    checkout_minutes: Optional[int] = None
    rotate_on_checkin: Optional[bool] = None
    require_comment: Optional[bool] = None
    require_approval: Optional[bool] = None
    approver_group_id: Optional[int] = None
    password_changer: Optional[str] = None
    expiry_days: Optional[int] = None


class SecretIn(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    folder_id: int
    template_id: int
    fields: Dict[str, str] = {}
    generate_password: bool = False
    settings: SecretSettings = SecretSettings()


class SecretPatch(BaseModel):
    name: Optional[str] = None
    folder_id: Optional[int] = None
    fields: Optional[Dict[str, str]] = None
    settings: Optional[SecretSettings] = None
    comment: Optional[str] = None


class RevealIn(BaseModel):
    comment: Optional[str] = None
    fields: Optional[List[str]] = None


class CommentIn(BaseModel):
    comment: Optional[str] = None


class AccessRequestIn(BaseModel):
    reason: str
    minutes: int = 60


class ReviewIn(BaseModel):
    note: Optional[str] = None


class ProviderIn(BaseModel):
    name: str
    type: str
    config: dict
    enabled: bool = True
    jit_provisioning: bool = True
    default_role: str = "user"


class ProviderPatch(BaseModel):
    enabled: Optional[bool] = None
    config: Optional[dict] = None


# ---------------------------------------------------------------------- app

def create_app(settings: Optional[Settings] = None,
               http_factory: Callable[[], httpx.Client] = httpx.Client) -> FastAPI:
    settings = settings or Settings.from_env()
    keyring = KeyRing.from_settings(settings)
    db = Database(settings.db_path)
    audit = AuditLog(keyring)
    tokens = TokenService(keyring, settings)
    local_auth = LocalAuthenticator(keyring, settings, tokens, audit)
    vault = Vault(keyring, audit)
    directory = Directory(audit)
    sso = SSOService(keyring, settings, audit, tokens, http_factory)

    app = FastAPI(title="Secret Server", version="1.0.0",
                  description="Enterprise privileged password vault with SSO")
    app.state.settings, app.state.db, app.state.vault = settings, db, vault
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                           allow_methods=["*"], allow_headers=["Authorization", "Content-Type"])

    for exc_type in (VaultError, AuthError, SSOError):
        @app.exception_handler(exc_type)
        async def _handle(request: Request, exc: Exception):
            return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(CryptoError)
    async def _crypto(request: Request, exc: CryptoError):
        return JSONResponse({"detail": "cryptographic integrity failure"}, status_code=500)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    bearer = HTTPBearer(auto_error=False)

    def get_conn():
        with db.transaction() as conn:
            yield conn

    def client_ip(request: Request) -> Optional[str]:
        return request.client.host if request.client else None

    def principal(request: Request, conn=Depends(get_conn),
                  creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> Principal:
        if creds is None:
            raise AuthError("authentication required")
        return tokens.authenticate(conn, creds.credentials, client_ip(request))

    # ------------------------------------------------------------------ health

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # ------------------------------------------------------------------ auth

    @app.post("/api/auth/login")
    def login(body: LoginIn, request: Request, conn=Depends(get_conn)):
        return local_auth.login(conn, body.username, body.password, client_ip(request))

    @app.post("/api/auth/mfa/verify")
    def mfa_verify(body: MfaVerifyIn, request: Request, conn=Depends(get_conn)):
        return local_auth.verify_mfa(conn, body.mfa_token, body.code, client_ip(request))

    @app.post("/api/auth/mfa/enroll")
    def mfa_enroll(p: Principal = Depends(principal), conn=Depends(get_conn)):
        return local_auth.begin_mfa_enrollment(conn, p)

    @app.post("/api/auth/mfa/confirm")
    def mfa_confirm(body: CodeIn, p: Principal = Depends(principal), conn=Depends(get_conn)):
        local_auth.confirm_mfa_enrollment(conn, p, body.code)
        return {"mfa_enabled": True}

    @app.post("/api/auth/logout")
    def logout(p: Principal = Depends(principal), conn=Depends(get_conn)):
        tokens.revoke(conn, p.session_id)
        audit.record(conn, "LOGOUT", p, "user", p.user_id)
        return {"logged_out": True}

    @app.get("/api/auth/me")
    def me(p: Principal = Depends(principal), conn=Depends(get_conn)):
        return {**directory.get_user(conn, p.user_id), "group_ids": sorted(p.group_ids)}

    @app.post("/api/auth/sso/exchange")
    def sso_exchange(body: CodeIn, conn=Depends(get_conn)):
        return sso.exchange(conn, body.code)

    @app.get("/api/auth/providers")
    def public_providers(conn=Depends(get_conn)):
        """Enabled identity providers, for rendering 'Sign in with ...' buttons."""
        return sso.list_providers(conn, include_config=False)

    # ------------------------------------------------------------------ SSO browser endpoints

    def _sso_result(result: dict):
        if "redirect" in result:
            return RedirectResponse(result["redirect"], status_code=303)
        return result

    @app.get("/sso/{name}/login")
    def sso_login(name: str, conn=Depends(get_conn)):
        return RedirectResponse(sso.begin_login(conn, name), status_code=302)

    @app.get("/sso/{name}/callback")
    def sso_oidc_callback(name: str, request: Request, code: Optional[str] = None,
                          state: Optional[str] = None, error: Optional[str] = None,
                          conn=Depends(get_conn)):
        return _sso_result(sso.complete_oidc(conn, name, code, state, error, client_ip(request)))

    @app.post("/sso/{name}/acs")
    def sso_saml_acs(name: str, request: Request, SAMLResponse: Optional[str] = Form(None),
                     RelayState: Optional[str] = Form(None), conn=Depends(get_conn)):
        return _sso_result(sso.complete_saml(conn, name, SAMLResponse, RelayState,
                                             client_ip(request)))

    @app.get("/sso/{name}/metadata")
    def sso_saml_metadata(name: str, conn=Depends(get_conn)):
        return Response(sso.saml_metadata(conn, name), media_type="application/samlmetadata+xml")

    # ------------------------------------------------------------------ SSO admin

    @app.get("/api/sso/providers")
    def list_providers(p: Principal = Depends(principal), conn=Depends(get_conn)):
        if not p.is_admin:
            raise VaultError("administrator role required", 403)
        return sso.list_providers(conn, include_config=True)

    @app.post("/api/sso/providers", status_code=201)
    def create_provider(body: ProviderIn, p: Principal = Depends(principal),
                        conn=Depends(get_conn)):
        return sso.create_provider(conn, p, body.name, body.type, body.config, body.enabled,
                                   body.jit_provisioning, body.default_role)

    @app.patch("/api/sso/providers/{name}")
    def update_provider(name: str, body: ProviderPatch, p: Principal = Depends(principal),
                        conn=Depends(get_conn)):
        return sso.update_provider(conn, p, name, body.enabled, body.config)

    # ------------------------------------------------------------------ users & groups

    @app.get("/api/users")
    def list_users(p: Principal = Depends(principal), conn=Depends(get_conn)):
        return directory.list_users(conn, p)

    @app.post("/api/users", status_code=201)
    def create_user(body: UserIn, p: Principal = Depends(principal), conn=Depends(get_conn)):
        return directory.create_user(conn, p, body.username, body.password, body.role,
                                     body.display_name, body.email, body.auth_source)

    @app.patch("/api/users/{user_id}")
    def update_user(user_id: int, body: UserPatch, p: Principal = Depends(principal),
                    conn=Depends(get_conn)):
        return directory.update_user(conn, p, user_id, **body.model_dump())

    @app.post("/api/users/{user_id}/unlock")
    def unlock_user(user_id: int, p: Principal = Depends(principal), conn=Depends(get_conn)):
        return directory.unlock_user(conn, p, user_id)

    @app.get("/api/groups")
    def list_groups(p: Principal = Depends(principal), conn=Depends(get_conn)):
        return directory.list_groups(conn, p)

    @app.post("/api/groups", status_code=201)
    def create_group(body: GroupIn, p: Principal = Depends(principal), conn=Depends(get_conn)):
        return directory.create_group(conn, p, body.name, body.description)

    @app.post("/api/groups/{group_id}/members")
    def add_member(group_id: int, body: MemberIn, p: Principal = Depends(principal),
                   conn=Depends(get_conn)):
        return directory.add_member(conn, p, group_id, body.user_id)

    @app.delete("/api/groups/{group_id}/members/{user_id}")
    def remove_member(group_id: int, user_id: int, p: Principal = Depends(principal),
                      conn=Depends(get_conn)):
        return directory.remove_member(conn, p, group_id, user_id)

    # ------------------------------------------------------------------ folders

    @app.get("/api/folders")
    def list_folders(p: Principal = Depends(principal), conn=Depends(get_conn)):
        return vault.list_folders(conn, p)

    @app.post("/api/folders", status_code=201)
    def create_folder(body: FolderIn, p: Principal = Depends(principal), conn=Depends(get_conn)):
        return vault.create_folder(conn, p, body.name, body.parent_id, body.inherit_permissions)

    @app.get("/api/folders/{folder_id}/permissions")
    def folder_permissions(folder_id: int, p: Principal = Depends(principal),
                           conn=Depends(get_conn)):
        return vault.folder_permissions(conn, p, folder_id)

    @app.put("/api/folders/{folder_id}/permissions")
    def set_folder_permissions(folder_id: int, body: PermissionsIn,
                               p: Principal = Depends(principal), conn=Depends(get_conn)):
        return vault.set_folder_permissions(conn, p, folder_id, body.inherit_permissions,
                                            [e.model_dump() for e in body.entries])

    # ------------------------------------------------------------------ templates

    @app.get("/api/templates")
    def list_templates(p: Principal = Depends(principal), conn=Depends(get_conn)):
        return vault.list_templates(conn)

    @app.post("/api/templates", status_code=201)
    def create_template(body: TemplateIn, p: Principal = Depends(principal),
                        conn=Depends(get_conn)):
        return vault.create_template(conn, p, body.name, [f.model_dump() for f in body.fields],
                                     body.password_policy, body.expiry_days)

    @app.get("/api/password/generate")
    def generate(template_id: Optional[int] = None, p: Principal = Depends(principal),
                 conn=Depends(get_conn)):
        return {"password": vault.generate_for_template(conn, template_id)}

    # ------------------------------------------------------------------ secrets

    @app.get("/api/secrets")
    def search_secrets(q: Optional[str] = Query(None, max_length=256),
                       folder_id: Optional[int] = None,
                       p: Principal = Depends(principal), conn=Depends(get_conn)):
        return vault.search(conn, p, q, folder_id)

    @app.get("/api/secrets/expired")
    def expired_secrets(p: Principal = Depends(principal), conn=Depends(get_conn)):
        out = []
        for secret_id in vault.expired_ids(conn):
            try:
                out.append(vault.describe(conn, p, secret_id))
            except VaultError:
                continue
        return out

    @app.post("/api/secrets", status_code=201)
    def create_secret(body: SecretIn, p: Principal = Depends(principal), conn=Depends(get_conn)):
        return vault.create_secret(conn, p, body.name, body.folder_id, body.template_id,
                                   body.fields, body.generate_password,
                                   body.settings.model_dump(exclude_none=True))

    @app.get("/api/secrets/{secret_id}")
    def get_secret(secret_id: int, p: Principal = Depends(principal), conn=Depends(get_conn)):
        return vault.describe(conn, p, secret_id)

    @app.post("/api/secrets/{secret_id}/reveal")
    def reveal_secret(secret_id: int, body: RevealIn, p: Principal = Depends(principal),
                      conn=Depends(get_conn)):
        return vault.reveal(conn, p, secret_id, body.comment, body.fields)

    @app.patch("/api/secrets/{secret_id}")
    def update_secret(secret_id: int, body: SecretPatch, p: Principal = Depends(principal),
                      conn=Depends(get_conn)):
        return vault.update_secret(
            conn, p, secret_id, body.name, body.folder_id, body.fields,
            body.settings.model_dump(exclude_none=True) if body.settings else None, body.comment)

    @app.delete("/api/secrets/{secret_id}", status_code=204)
    def delete_secret(secret_id: int, p: Principal = Depends(principal), conn=Depends(get_conn)):
        vault.delete_secret(conn, p, secret_id)
        return Response(status_code=204)

    @app.get("/api/secrets/{secret_id}/permissions")
    def secret_permissions(secret_id: int, p: Principal = Depends(principal),
                           conn=Depends(get_conn)):
        return vault.secret_permissions(conn, p, secret_id)

    @app.put("/api/secrets/{secret_id}/permissions")
    def set_secret_permissions(secret_id: int, body: PermissionsIn,
                               p: Principal = Depends(principal), conn=Depends(get_conn)):
        return vault.set_secret_permissions(conn, p, secret_id, body.inherit_permissions,
                                            [e.model_dump() for e in body.entries])

    @app.post("/api/secrets/{secret_id}/check-out")
    def check_out(secret_id: int, body: CommentIn, p: Principal = Depends(principal),
                  conn=Depends(get_conn)):
        return vault.check_out(conn, p, secret_id, body.comment)

    @app.post("/api/secrets/{secret_id}/check-in")
    def check_in(secret_id: int, p: Principal = Depends(principal), conn=Depends(get_conn)):
        return vault.check_in(conn, p, secret_id)

    @app.post("/api/secrets/{secret_id}/rotate")
    def rotate(secret_id: int, p: Principal = Depends(principal), conn=Depends(get_conn)):
        return vault.rotate(conn, p, secret_id)

    @app.get("/api/secrets/{secret_id}/history")
    def history(secret_id: int, p: Principal = Depends(principal), conn=Depends(get_conn)):
        return vault.history(conn, p, secret_id)

    @app.get("/api/secrets/{secret_id}/audit")
    def secret_audit(secret_id: int, p: Principal = Depends(principal), conn=Depends(get_conn)):
        vault.secret_permissions(conn, p, secret_id)  # Owner check
        rows = conn.execute(
            "SELECT ts, username, action, details, ip FROM audit_log "
            "WHERE target_type = 'secret' AND target_id = ? ORDER BY id DESC LIMIT 500",
            (str(secret_id),))
        return [dict(r) for r in rows]

    @app.post("/api/secrets/{secret_id}/access-requests", status_code=201)
    def request_access(secret_id: int, body: AccessRequestIn, p: Principal = Depends(principal),
                       conn=Depends(get_conn)):
        return vault.request_access(conn, p, secret_id, body.reason, body.minutes)

    # ------------------------------------------------------------------ access requests

    @app.get("/api/access-requests")
    def list_requests(p: Principal = Depends(principal), conn=Depends(get_conn)):
        return vault.list_requests(conn, p)

    @app.post("/api/access-requests/{request_id}/approve")
    def approve(request_id: int, body: ReviewIn, p: Principal = Depends(principal),
                conn=Depends(get_conn)):
        return vault.review_request(conn, p, request_id, True, body.note)

    @app.post("/api/access-requests/{request_id}/deny")
    def deny(request_id: int, body: ReviewIn, p: Principal = Depends(principal),
             conn=Depends(get_conn)):
        return vault.review_request(conn, p, request_id, False, body.note)

    # ------------------------------------------------------------------ audit & maintenance

    @app.get("/api/audit")
    def audit_log(limit: int = Query(100, le=1000), action: Optional[str] = None,
                  p: Principal = Depends(principal), conn=Depends(get_conn)):
        if not p.is_admin:
            raise VaultError("administrator role required", 403)
        sql, args = "SELECT * FROM audit_log", []
        if action:
            sql += " WHERE action = ?"
            args.append(action)
        rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))
        return [dict(r) for r in rows]

    @app.get("/api/audit/verify")
    def audit_verify(p: Principal = Depends(principal), conn=Depends(get_conn)):
        if not p.is_admin:
            raise VaultError("administrator role required", 403)
        return audit.verify(conn)

    @app.post("/api/maintenance/run")
    def maintenance(p: Principal = Depends(principal), conn=Depends(get_conn)):
        if not p.is_admin:
            raise VaultError("administrator role required", 403)
        return vault.run_maintenance(conn)

    return app
