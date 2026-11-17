"""Web pages for Password Station."""

import csv
import hmac
import io
import json
import secrets
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .config import Settings
from .db import Database
from .directory import DirectoryError
from .service import Sender, Station, StationError

HERE = Path(__file__).resolve().parent


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    s = settings or Settings()
    db = Database(s.db_path)
    station, sender = Station(s, db), Sender(s, db)
    station.ensure_demo()
    station.ensure_admin()
    templates = Jinja2Templates(directory=str(HERE / "templates"))

    app = FastAPI(title=s.app_name, version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=s.secret_key, same_site="strict", https_only=s.base_url.startswith("https:"),
                       max_age=60 * 30, session_cookie="ps_session")
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.state.station, app.state.settings = station, s

    # --- helpers ---------------------------------------------------------------------------
    def csrf(request: Request) -> str:
        tok = request.session.get("csrf")
        if not tok:
            tok = request.session["csrf"] = secrets.token_urlsafe(24)
        return tok

    def check_csrf(request: Request, token: str) -> None:
        if not hmac.compare_digest(request.session.get("csrf", ""), token or ""):
            raise HTTPException(403, "Your session expired. Go back and try again.")

    def ip(request: Request) -> str:
        return request.client.host if request.client else ""

    def page(request: Request, name: str, status: int = 200, **ctx):
        ctx.update(app_name=s.app_name, csrf=csrf(request), domains=station.domains(), demo=s.demo,
                   demo_outbox=s.demo_outbox, support=s.support_contact, admin=request.session.get("admin"),
                   sms_enabled=s.sms_provider == "sns" or s.demo_outbox)
        resp = templates.TemplateResponse(request, name, ctx, status_code=status)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def send_all(bg: BackgroundTasks, messages) -> None:
        for m in messages:
            bg.add_task(sender.send, m)

    def go(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        return resp

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return page(request, "message.html", exc.status_code, title="Something went wrong", text=exc.detail, tone="bad")

    # --- public pages ------------------------------------------------------------------------
    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return page(request, "home.html")

    @app.get("/change", response_class=HTMLResponse)
    def change_form(request: Request):
        return page(request, "change.html", form={})

    @app.post("/change", response_class=HTMLResponse)
    def change(request: Request, bg: BackgroundTasks, csrf_token: str = Form(""), domain: str = Form(""),
               username: str = Form(""), current: str = Form(""), new: str = Form(""), confirm: str = Form("")):
        check_csrf(request, csrf_token)
        try:
            send_all(bg, station.change(ip(request), domain, username, current, new, confirm))
        except (StationError, DirectoryError) as exc:
            return page(request, "change.html", 400, error=str(exc), form={"domain": domain, "username": username})
        return page(request, "message.html", title="Password changed",
                    text="Your password has been changed. Use the new password everywhere you sign in with this account "
                         "(it may take a couple of minutes to reach Microsoft 365).", tone="ok")

    # Enrolment -------------------------------------------------------------------------------
    @app.get("/enroll", response_class=HTMLResponse)
    def enroll_form(request: Request):
        return page(request, "enroll_signin.html", form={})

    @app.post("/enroll", response_class=HTMLResponse)
    def enroll_signin(request: Request, csrf_token: str = Form(""), domain: str = Form(""), username: str = Form(""),
                      password: str = Form("")):
        check_csrf(request, csrf_token)
        try:
            request.session["enroll"] = station.enroll_start(ip(request), domain, username, password)
        except (StationError, DirectoryError) as exc:
            return page(request, "enroll_signin.html", 400, error=str(exc), form={"domain": domain, "username": username})
        return go("/enroll/contact")

    @app.get("/enroll/contact", response_class=HTMLResponse)
    def enroll_contact_form(request: Request, saved: str = ""):
        try:
            summary = station.enrollment_summary(request.session.get("enroll"))
        except StationError as exc:
            return page(request, "message.html", 400, title="Please sign in again", text=str(exc), tone="bad")
        return page(request, "enroll_contact.html", summary=summary, saved=saved)

    @app.post("/enroll/contact", response_class=HTMLResponse)
    def enroll_send(request: Request, bg: BackgroundTasks, csrf_token: str = Form(""), channel: str = Form(""),
                    contact: str = Form("")):
        check_csrf(request, csrf_token)
        flow_id = request.session.get("enroll")
        try:
            send_all(bg, station.enroll_send(ip(request), flow_id, channel, contact))
            summary = station.enrollment_summary(flow_id)
        except (StationError, DirectoryError) as exc:
            try:
                summary = station.enrollment_summary(flow_id)
            except StationError:
                return page(request, "message.html", 400, title="Please sign in again", text=str(exc), tone="bad")
            return page(request, "enroll_contact.html", 400, error=str(exc), summary=summary)
        return page(request, "code.html", action="/enroll/verify", channel=channel,
                    text="We've sent a code to the " + ("e-mail address" if channel == "email" else "mobile number") +
                         " you entered. Enter it to confirm it's yours.")

    @app.post("/enroll/verify", response_class=HTMLResponse)
    def enroll_verify(request: Request, bg: BackgroundTasks, csrf_token: str = Form(""), code: str = Form("")):
        check_csrf(request, csrf_token)
        try:
            send_all(bg, station.enroll_confirm(ip(request), request.session.get("enroll"), code))
        except StationError as exc:
            return page(request, "code.html", 400, action="/enroll/verify", error=str(exc), text="Enter the code we sent.")
        return go("/enroll/contact?saved=1")

    # Forgot password / unlock -------------------------------------------------------------------
    def recovery_form(request: Request, purpose: str, status=200, **ctx):
        return page(request, "recover.html", status, purpose=purpose, form=ctx.pop("form", {}), **ctx)

    @app.get("/forgot", response_class=HTMLResponse)
    def forgot_form(request: Request):
        return recovery_form(request, "forgot")

    @app.get("/unlock", response_class=HTMLResponse)
    def unlock_form(request: Request):
        return recovery_form(request, "unlock")

    def recovery_start(request: Request, bg: BackgroundTasks, purpose: str, csrf_token: str, domain: str,
                       username: str, channel: str, contact: str):
        check_csrf(request, csrf_token)
        try:
            flow_id, messages = station.recovery_start(ip(request), purpose, domain, username, channel, contact)
        except (StationError, DirectoryError) as exc:
            return recovery_form(request, purpose, 400, error=str(exc),
                                 form={"domain": domain, "username": username, "channel": channel})
        request.session[purpose] = flow_id
        send_all(bg, messages)
        return page(request, "code.html", action=f"/{purpose}/verify",
                    text="If those details match an enrolled account, we've sent a code. It can take a minute to "
                         "arrive. Enter it below.")

    @app.post("/forgot", response_class=HTMLResponse)
    def forgot_start(request: Request, bg: BackgroundTasks, csrf_token: str = Form(""), domain: str = Form(""),
                     username: str = Form(""), channel: str = Form("email"), contact: str = Form("")):
        return recovery_start(request, bg, "forgot", csrf_token, domain, username, channel, contact)

    @app.post("/unlock", response_class=HTMLResponse)
    def unlock_start(request: Request, bg: BackgroundTasks, csrf_token: str = Form(""), domain: str = Form(""),
                     username: str = Form(""), channel: str = Form("email"), contact: str = Form("")):
        return recovery_start(request, bg, "unlock", csrf_token, domain, username, channel, contact)

    @app.post("/forgot/verify", response_class=HTMLResponse)
    def forgot_verify(request: Request, csrf_token: str = Form(""), code: str = Form("")):
        check_csrf(request, csrf_token)
        try:
            station.recovery_verify(ip(request), request.session.get("forgot"), "forgot", code)
        except StationError as exc:
            return page(request, "code.html", 400, action="/forgot/verify", error=str(exc), text="Enter the code we sent.")
        flow = station.flow(request.session.get("forgot"), "forgot")
        return page(request, "new_password.html", policy=station.domain(flow["domain_id"]).policy_text)

    @app.post("/forgot/reset", response_class=HTMLResponse)
    def forgot_reset(request: Request, bg: BackgroundTasks, csrf_token: str = Form(""), new: str = Form(""),
                     confirm: str = Form("")):
        check_csrf(request, csrf_token)
        flow_id = request.session.get("forgot")
        try:
            send_all(bg, station.reset(ip(request), flow_id, new, confirm))
        except (StationError, DirectoryError) as exc:
            try:
                policy = station.domain(station.flow(flow_id, "forgot")["domain_id"]).policy_text
            except StationError:
                return page(request, "message.html", 400, title="Please start again", text=str(exc), tone="bad")
            return page(request, "new_password.html", 400, error=str(exc), policy=policy)
        request.session.pop("forgot", None)
        return page(request, "message.html", title="Password reset",
                    text="Your password has been reset and the account unlocked. Sign in with the new password "
                         "(Microsoft 365 may take a couple of minutes to pick it up).", tone="ok")

    @app.post("/unlock/verify", response_class=HTMLResponse)
    def unlock_verify(request: Request, bg: BackgroundTasks, csrf_token: str = Form(""), code: str = Form("")):
        check_csrf(request, csrf_token)
        try:
            send_all(bg, station.recovery_verify(ip(request), request.session.get("unlock"), "unlock", code))
        except (StationError, DirectoryError) as exc:
            return page(request, "code.html", 400, action="/unlock/verify", error=str(exc), text="Enter the code we sent.")
        request.session.pop("unlock", None)
        return page(request, "message.html", title="Account unlocked",
                    text="Your account is unlocked. If you've forgotten the password, use 'Forgot password' instead "
                         "of guessing, or the account will lock again.", tone="ok")

    # --- admin console -------------------------------------------------------------------------------
    def need_admin(request: Request) -> str:
        user = request.session.get("admin")
        if not user:
            raise HTTPException(403, "Administrator sign-in required.")
        return user

    @app.get("/admin/login", response_class=HTMLResponse)
    def admin_login_form(request: Request):
        return page(request, "admin_login.html")

    @app.post("/admin/login", response_class=HTMLResponse)
    def admin_login(request: Request, csrf_token: str = Form(""), username: str = Form(""), password: str = Form("")):
        check_csrf(request, csrf_token)
        if not station.admin_login(ip(request), username, password):
            return page(request, "admin_login.html", 400, error="Sign-in failed (5 failures lock it for 15 minutes).")
        request.session.clear()
        request.session["admin"] = username
        return go("/admin")

    @app.post("/admin/logout")
    def admin_logout(request: Request, csrf_token: str = Form("")):
        check_csrf(request, csrf_token)
        request.session.clear()
        return go("/")

    @app.get("/admin", response_class=HTMLResponse)
    def admin_home(request: Request):
        need_admin(request)
        rd = station.db.read()
        counts = {r["domain_id"]: r["n"] for r in rd.execute(
            "SELECT domain_id, COUNT(*) AS n FROM enrollments GROUP BY domain_id")}
        ok, n = station.audit.verify()
        return page(request, "admin.html", all_domains=station.domains(include_disabled=True), counts=counts,
                    chain_ok=ok, chain_n=n,
                    events=[dict(r) for r in rd.execute("SELECT * FROM events ORDER BY id DESC LIMIT 25")])

    @app.get("/admin/domain", response_class=HTMLResponse)
    def admin_domain_form(request: Request, id: str = ""):
        need_admin(request)
        d = next((x for x in station.domains(include_disabled=True) if x.id == id), None)
        return page(request, "admin_domain.html", d=d, settings_json=json.dumps(d.settings, indent=2) if d else "")

    @app.post("/admin/domain", response_class=HTMLResponse)
    def admin_domain_save(request: Request, csrf_token: str = Form(""), id: str = Form(""), name: str = Form(""),
                          kind: str = Form("ad"), hosts: str = Form(""), port: str = Form("636"),
                          base_dn: str = Form(""), bind_user: str = Form(""), bind_password: str = Form(""),
                          login_attribute: str = Form("sAMAccountName"), allowed_group_dn: str = Form(""),
                          protected_groups: str = Form(""), ca_pem: str = Form(""), min_length: str = Form("12"),
                          policy_text: str = Form(""), enabled: bool = Form(False), sort: str = Form("0"),
                          action: str = Form("save")):
        admin = need_admin(request)
        check_csrf(request, csrf_token)
        settings = {"hosts": [h.strip() for h in hosts.replace("\n", ",").split(",") if h.strip()],
                    "port": int(port or 636), "base_dn": base_dn.strip(), "bind_user": bind_user.strip(),
                    "login_attribute": login_attribute, "allowed_group_dn": allowed_group_dn.strip(),
                    "protected_groups": [g.strip() for g in protected_groups.splitlines() if g.strip()],
                    "ca_pem": ca_pem.strip(), "min_length": int(min_length or 12), "policy_text": policy_text.strip()}
        if kind == "demo":
            settings = {"min_length": int(min_length or 12), "policy_text": policy_text.strip()}
        try:
            station.save_domain(id.strip(), name.strip() or id, kind, settings, bind_password or None, enabled,
                                int(sort or 0))
            if kind == "demo":
                from .directory import DemoDirectory
                DemoDirectory.seed(station.db, id.strip())
        except (StationError, ValueError) as exc:
            return page(request, "admin_domain.html", 400, error=str(exc), d=None, settings_json="")
        station.audit.record("domain-saved", "ok", ip=ip(request), domain_id=id, detail=f"by {admin}")
        result = None
        d = next((x for x in station.domains(include_disabled=True) if x.id == id.strip()), None)
        if action == "test" and d:
            try:
                result = ("ok", station.directory(d).test())
            except Exception as exc:  # noqa: BLE001 - show the admin what failed
                result = ("bad", f"{type(exc).__name__}: {exc}")
        return page(request, "admin_domain.html", d=d, saved=True, test=result)

    @app.get("/admin/enrollments", response_class=HTMLResponse)
    def admin_enrollments(request: Request, q: str = ""):
        need_admin(request)
        rows = station.db.read().execute(
            "SELECT domain_id, username, email_enc IS NOT NULL AS has_email, phone_enc IS NOT NULL AS has_phone, updated_at "
            "FROM enrollments WHERE username LIKE ? ORDER BY domain_id, username LIMIT 500", (f"%{q.lower()}%",)).fetchall()
        return page(request, "admin_enrollments.html", rows=[dict(r) for r in rows], q=q)

    @app.post("/admin/enrollments/remove", response_class=HTMLResponse)
    def admin_remove_enrollment(request: Request, csrf_token: str = Form(""), domain_id: str = Form(""),
                                username: str = Form("")):
        admin = need_admin(request)
        check_csrf(request, csrf_token)
        station.remove_enrollment(admin, ip(request), domain_id, username)
        return go("/admin/enrollments")

    @app.get("/admin/audit.csv")
    def admin_audit_csv(request: Request):
        need_admin(request)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["id", "at", "ip", "domain", "username", "action", "result", "detail", "hash"])
        for r in station.db.read().execute("SELECT * FROM events ORDER BY id"):
            w.writerow([r["id"], r["at"], r["ip"], r["domain_id"], r["username"], r["action"], r["result"], r["detail"], r["hash"]])
        return Response(out.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="password-station-audit.csv"'})

    @app.get("/admin/outbox", response_class=HTMLResponse)
    def admin_outbox(request: Request):
        need_admin(request)
        if not s.demo_outbox:
            raise HTTPException(404, "The outbox is only used in demo mode.")
        rows = station.db.read().execute("SELECT * FROM outbox ORDER BY id DESC LIMIT 50").fetchall()
        return page(request, "admin_outbox.html", rows=[dict(r) for r in rows])

    return app
