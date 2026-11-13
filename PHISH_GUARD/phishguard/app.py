"""FastAPI application: security console API, time-of-click pages, report endpoints, Outlook add-in."""

from __future__ import annotations

import asyncio
import html
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel

from . import __version__
from .auth import AuthError, Users
from .authcheck import DNS
from .config import Settings
from .db import Database
from .engine import Scanner
from .graph import GraphClient, GraphError
from .llm import LLMAnalyzer
from .sandbox import CapeSandbox
from .service import Conflict, NotFound, PhishGuard
from .urls import httpx_get

logger = logging.getLogger("phishguard")
STATIC = Path(__file__).parent / "static"
ADDIN = Path(__file__).parent / "addin"


class LoginIn(BaseModel):
    username: str
    password: str


class PasswordIn(BaseModel):
    old_password: str
    new_password: str


class ActionIn(BaseModel):
    reason: str = ""
    mode: str = "quarantine"


class ReviewIn(BaseModel):
    decision: str
    note: str = ""
    block: bool = True
    purge: bool = True


class IndicatorIn(BaseModel):
    kind: str
    value: str
    action: str = "block"
    note: str = ""
    hunt: bool = True


class AddinReportIn(BaseModel):
    internet_message_id: str
    reporter: str
    comment: str = ""


class UserIn(BaseModel):
    username: str
    full_name: str
    role: str
    temporary_password: str
    email: str = ""


class UserPatch(BaseModel):
    role: Optional[str] = None
    active: Optional[bool] = None
    unlock: bool = False
    temporary_password: Optional[str] = None


def create_app(settings: Optional[Settings] = None, *, llm_client=None, graph: Optional[GraphClient] = None,
               sandbox: Optional[CapeSandbox] = None, page_get=None, resolver=None, dns: Optional[DNS] = None,
               deliver=None, background: bool = True) -> FastAPI:
    s = settings or Settings()
    db = Database(s.db_path)
    users = Users(db, s.session_idle_minutes)
    llm = LLMAnalyzer(s.llm_model, s.llm_max_chars, client=llm_client) if s.llm_enabled else None
    if graph is None and s.m365_configured:
        graph = GraphClient(s.m365_tenant_id, s.m365_client_id, s.m365_client_secret)
    if sandbox is None and s.cape_url:
        sandbox = CapeSandbox(s.cape_url, s.cape_token)
    if page_get is None and s.click_fetch:
        page_get = httpx_get()
    guard = PhishGuard(s, db, Scanner(s, llm, dns or (DNS() if s.dns_checks else None)), graph=graph,
                       sandbox=sandbox, page_get=page_get, resolver=resolver, deliver=deliver)
    generated = users.ensure_admin(s.admin_password)
    if generated:
        print(f"\n*** PhishGuard console: user 'admin', temporary password: {generated} ***\n")

    @asynccontextmanager
    async def lifespan(_app):
        async def loop(fn, every):
            while True:
                try:
                    await asyncio.to_thread(fn)
                except Exception:
                    logger.exception("Background job %s failed", fn.__name__)
                await asyncio.sleep(every)
        tasks = []
        if background and guard.graph:
            tasks.append(asyncio.create_task(loop(guard.poll_m365, s.m365_poll_seconds)))
        if background and guard.sandbox:
            tasks.append(asyncio.create_task(loop(guard.sandbox_cycle, 60)))
        yield
        for t in tasks:
            t.cancel()

    app = FastAPI(title="PhishGuard", version=__version__, lifespan=lifespan,
                  description="AI phishing, BEC and malware protection for e-mail")
    app.state.guard, app.state.users, app.state.settings = guard, users, s

    @app.exception_handler(AuthError)
    async def _auth(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=401)

    @app.exception_handler(NotFound)
    async def _nf(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(Conflict)
    async def _cf(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def _ve(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(GraphError)
    async def _ge(_, exc):
        return JSONResponse({"detail": f"Microsoft 365: {exc}"}, status_code=502)

    def current(authorization: Optional[str] = Header(default=None)) -> dict:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise AuthError("Not signed in")
        return users.session(authorization[7:])

    def need(permission: str):
        def dep(user: dict = Depends(current)) -> dict:
            if user["must_change_password"]:
                raise AuthError("Change your password first")
            if permission not in user["permissions"]:
                raise HTTPException(403, "Your role does not allow this")
            return user
        return dep

    # ------------------------------------------------------------------ console
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.post("/api/auth/login")
    def login(body: LoginIn):
        return users.login(body.username, body.password)

    @app.post("/api/auth/logout")
    def logout(authorization: str = Header()):
        users.logout(authorization[7:])
        return {"ok": True}

    @app.get("/api/me")
    def me(user: dict = Depends(current)):
        return user

    @app.post("/api/auth/change-password")
    def change_password(body: PasswordIn, user: dict = Depends(current)):
        users.change_password(user, body.old_password, body.new_password)
        return {"ok": True}

    @app.get("/api/status")
    def status(user: dict = Depends(need("view"))):
        return {"version": __version__, "m365": bool(guard.graph), "gateway_next_hop": s.smtp_next_hop or None,
                "ai": bool(llm), "ai_model": s.llm_model if llm else None, "sandbox": bool(guard.sandbox),
                "click_fetch": bool(page_get), "dns_checks": s.dns_checks, "report_mailbox": s.report_mailbox or None,
                "org_domains": s.org_domains, "thresholds": {"warn": s.warn_score, "quarantine": s.quarantine_score},
                "base_url": s.base_url}

    @app.get("/api/stats")
    def stats(days: int = 7, user: dict = Depends(need("view"))):
        return guard.stats(days)

    @app.get("/api/messages")
    def messages(verdict: Optional[str] = None, status: Optional[str] = None, q: Optional[str] = None,
                 limit: int = 200, user: dict = Depends(need("view"))):
        sql, args = ("SELECT id, source, mailbox, subject, from_address, from_display, scanned_at, score, verdict, "
                     "category, summary, status FROM messages WHERE 1=1 "), []
        for col, val in (("verdict", verdict), ("status", status)):
            if val:
                sql += f"AND {col} = ? "
                args.append(val)
        if q:
            sql += "AND (subject LIKE ? OR from_address LIKE ? OR mailbox LIKE ?) "
            args += [f"%{q}%"] * 3
        with db.read() as conn:
            return [dict(r) for r in conn.execute(sql + "ORDER BY id DESC LIMIT ?", (*args, min(limit, 1000)))]

    @app.get("/api/messages/{message_id}")
    def message(message_id: int, user: dict = Depends(need("view"))):
        row = guard.message_row(message_id)
        row.update(signals=json.loads(row["signals"]), indicators=json.loads(row["indicators"]),
                   llm=json.loads(row["llm"]) if row["llm"] else None, has_copy=bool(row.pop("raw_path")))
        with db.read() as conn:
            row["reports"] = [dict(r) for r in conn.execute("SELECT * FROM reports WHERE message_id = ?", (message_id,))]
            row["sandbox"] = [dict(r) for r in conn.execute("SELECT * FROM sandbox_jobs WHERE message_id = ?", (message_id,))]
            row["clicks"] = [dict(r) for r in conn.execute(
                "SELECT c.* FROM clicks c JOIN links l ON l.token = c.token WHERE l.message_id = ? ORDER BY c.id DESC",
                (message_id,))]
            row["copies"] = [dict(r) for r in conn.execute(
                "SELECT id, mailbox, status FROM messages WHERE internet_message_id = ? AND internet_message_id != '' "
                "AND id != ?", (row["internet_message_id"] or "", message_id))]
        return row

    @app.get("/api/messages/{message_id}/raw")
    def message_raw(message_id: int, user: dict = Depends(need("review"))):
        return Response(guard.raw(message_id), media_type="message/rfc822",
                        headers={"Content-Disposition": f"attachment; filename=message-{message_id}.eml.txt"})

    @app.post("/api/messages/{message_id}/quarantine")
    def quarantine(message_id: int, body: ActionIn, user: dict = Depends(need("act"))):
        return guard.quarantine(message_id, user["username"], body.reason)

    @app.post("/api/messages/{message_id}/release")
    def release(message_id: int, body: ActionIn, user: dict = Depends(need("act"))):
        return guard.release(message_id, user["username"], body.reason)

    @app.post("/api/messages/{message_id}/purge")
    def purge(message_id: int, body: ActionIn, user: dict = Depends(need("act"))):
        return guard.purge_everywhere(message_id, user["username"], body.mode, body.reason)

    @app.post("/api/scan")
    def scan(raw: str = Body(..., media_type="text/plain"), user: dict = Depends(need("review"))):
        message_id, result = guard.process(raw.encode("utf-8", "surrogateescape"), "api", act=False)
        return message(message_id, user)

    @app.get("/api/reports")
    def reports(status: Optional[str] = "new", user: dict = Depends(need("view"))):
        with db.read() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT r.*, m.subject, m.from_address, m.score, m.verdict, m.category, m.status AS message_status "
                "FROM reports r JOIN messages m ON m.id = r.message_id WHERE (? IS NULL OR ? = '' OR r.status = ?) "
                "ORDER BY r.id DESC LIMIT 500", (status, status, status))]

    @app.post("/api/reports/{report_id}/review")
    def review(report_id: int, body: ReviewIn, user: dict = Depends(need("review"))):
        return guard.review_report(report_id, body.decision, user["username"], body.note, block=body.block, purge=body.purge)

    @app.get("/api/indicators")
    def indicators(user: dict = Depends(need("view"))):
        with db.read() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM indicators WHERE active = 1 ORDER BY id DESC")]

    @app.post("/api/indicators", status_code=201)
    def add_indicator(body: IndicatorIn, user: dict = Depends(need("intel"))):
        return guard.add_indicator(body.kind, body.value, body.action, "analyst", user["username"], body.note, body.hunt)

    @app.delete("/api/indicators/{indicator_id}")
    def remove_indicator(indicator_id: int, user: dict = Depends(need("intel"))):
        with db.tx() as conn:
            conn.execute("UPDATE indicators SET active = 0 WHERE id = ?", (indicator_id,))
            guard._log(conn, user["username"], "INDICATOR_REMOVED", str(indicator_id))
        return {"ok": True}

    @app.get("/api/clicks")
    def clicks(user: dict = Depends(need("view"))):
        with db.read() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM clicks ORDER BY id DESC LIMIT 500")]

    @app.get("/api/activity")
    def activity(user: dict = Depends(need("view"))):
        with db.read() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM actions ORDER BY id DESC LIMIT 500")]

    @app.get("/api/users")
    def list_users(user: dict = Depends(need("users"))):
        return users.list()

    @app.post("/api/users", status_code=201)
    def create_user(body: UserIn, user: dict = Depends(need("users"))):
        users.create(body.username, body.full_name, body.role, body.temporary_password, body.email)
        return {"ok": True}

    @app.patch("/api/users/{user_id}")
    def patch_user(user_id: int, body: UserPatch, user: dict = Depends(need("users"))):
        if user_id == user["id"] and (body.active is False or (body.role and body.role != "admin")):
            raise ValueError("You cannot deactivate or demote yourself")
        users.update(user_id, role=body.role, active=body.active, unlock=body.unlock, temp_password=body.temporary_password)
        return {"ok": True}

    # ------------------------------------------------------------------ time-of-click (public)
    @app.get("/c/{token}", include_in_schema=False)
    def click(token: str, request: Request, proceed: int = 0):
        try:
            r = guard.click(token, request.client.host if request.client else "")
        except NotFound:
            return HTMLResponse(page("Link not recognised", "<p>This protected link is not valid.</p>", "bad"), 404)
        if r["decision"] == "allowed" or (r["decision"] == "warned" and proceed):
            return RedirectResponse(r["url"], status_code=302)
        reasons = "".join(f"<li>{html.escape(x)}</li>" for x in r["reasons"][:6])
        report = (f'<p><a class="btn" href="{guard.report_link(r["message_id"], r["recipient"])}">Report this e-mail '
                  "to IT Security</a></p>") if r.get("message_id") and r.get("recipient") else ""
        if r["decision"] == "blocked":
            body = (f"<p>PhishGuard blocked this link because it is dangerous.</p><p class='url'>{html.escape(r['url'])}</p>"
                    f"<ul>{reasons}</ul>{report}<p>If you entered a password on this site, change it now and tell IT.</p>")
            return HTMLResponse(page("Dangerous link blocked", body, "bad"), 403)
        body = (f"<p>This link looks suspicious. Only continue if you trust the sender and expected this link.</p>"
                f"<p class='url'>{html.escape(r['url'])}</p><ul>{reasons}</ul>{report}"
                f'<p><a href="/c/{html.escape(token)}?proceed=1">Continue to the site anyway</a></p>')
        return HTMLResponse(page("Suspicious link", body, "warn"))

    # ------------------------------------------------------------------ one-click report (public, signed)
    @app.get("/r/{message_id}/{r}/{sig}", include_in_schema=False)
    def report_page(message_id: int, r: str, sig: str):
        recipient = guard.verify_report_link(message_id, r, sig)
        # a POST button, so mail scanners that pre-fetch links cannot file reports by themselves
        body = (f"<p>Report this e-mail to IT Security as phishing or a scam?</p><p>Reporting as "
                f"<b>{html.escape(recipient)}</b>.</p><form method='post'><textarea name='comment' "
                "placeholder='Optional: why does it look suspicious?'></textarea><br>"
                "<button class='btn'>Report phishing</button></form>")
        return HTMLResponse(page("Report phishing", body, "warn"))

    @app.post("/r/{message_id}/{r}/{sig}", include_in_schema=False)
    async def report_submit(message_id: int, r: str, sig: str, request: Request):
        recipient = guard.verify_report_link(message_id, r, sig)
        form = (await request.body()).decode("utf-8", "replace")
        from urllib.parse import parse_qs
        comment = (parse_qs(form).get("comment") or [""])[0]
        res = guard.report(recipient, "banner", message_id=message_id, comment=comment)
        text = "You already reported this message - thank you." if res["duplicate"] else \
            "Thank you. IT Security will review the message and remove it from every mailbox if it is malicious."
        return HTMLResponse(page("Report received", f"<p>{text}</p>", "ok"))

    # ------------------------------------------------------------------ Outlook add-in
    @app.post("/api/addin/report")
    def addin_report(body: AddinReportIn):
        res = guard.report(body.reporter, "addin", internet_message_id=body.internet_message_id, comment=body.comment)
        return {"ok": True, "duplicate": res["duplicate"]}

    @app.get("/addin/manifest.xml", include_in_schema=False)
    def manifest():
        xml = (ADDIN / "manifest.xml").read_text().replace("{{BASE_URL}}", s.base_url)
        return Response(xml, media_type="application/xml")

    @app.get("/addin/{name}", include_in_schema=False)
    def addin_file(name: str):
        if name not in ("commands.html", "icon-16.png", "icon-32.png", "icon-80.png"):
            raise NotFound("Not found")
        return FileResponse(ADDIN / name)

    return app


def page(title: str, body: str, tone: str) -> str:
    color = {"bad": "#b42318", "warn": "#b54708", "ok": "#127a3a"}[tone]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} - PhishGuard</title><style>
body{{font-family:system-ui,Segoe UI,sans-serif;background:#f4f6f8;color:#1b2430;margin:0;padding:24px}}
.card{{max-width:620px;margin:40px auto;background:#fff;border-radius:12px;border-top:6px solid {color};padding:24px 28px;box-shadow:0 2px 12px #0001}}
h1{{color:{color};font-size:22px;margin:0 0 12px}} .url{{word-break:break-all;font-family:ui-monospace,monospace;background:#f4f6f8;padding:8px;border-radius:6px}}
.btn{{display:inline-block;background:{color};color:#fff;border:0;padding:10px 16px;border-radius:6px;text-decoration:none;font-size:15px;cursor:pointer}}
textarea{{width:100%;min-height:70px;margin:8px 0;font:inherit}} a{{color:{color}}}
</style></head><body><div class="card"><h1>{html.escape(title)}</h1>{body}<p style="color:#5d6b7a;font-size:13px">PhishGuard e-mail protection</p></div></body></html>"""
