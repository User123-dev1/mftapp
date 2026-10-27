"""Shared helpers for rendering pages."""

import json
from pathlib import Path
from typing import Optional

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import database, sso
from .config import settings
from .models import Category, ChangeState, ChangeType, CIStatus, CIType, Priority, PRStatus, Role, Status, User
from .security import csrf_token, pop_flashes

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["fromjson"] = json.loads


def _duration(hours) -> str:
    if hours is None:
        return "—"
    return f"{hours:.1f} h" if hours < 48 else f"{hours / 24:.1f} d"


templates.env.filters["duration"] = _duration


def render(request: Request, name: str, context: Optional[dict] = None, status_code: int = 200, user=None):
    user = user if user is not None else _session_user(request)
    ctx = {
        "app_name": settings.app_name,
        "user": user,
        "my_approvals": _approval_count(user),
        "ao_approvals": _order_approval_count(user),
        "csrf_token": csrf_token(request),
        "flashes": pop_flashes(request),
        "Status": Status,
        "Category": Category,
        "Priority": Priority,
        "Role": Role,
        "CIType": CIType,
        "CIStatus": CIStatus,
        "ChangeState": ChangeState,
        "PRStatus": PRStatus,
        "ChangeType": ChangeType,
        "sso_providers": sso.providers(),
        "local_login": settings.local_login,
    }
    ctx.update(context or {})
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def _approval_count(user: Optional[User]) -> int:
    if user is None or not user.can_approve_changes:
        return 0
    from .changes import pending_for

    db = database.SessionLocal()
    try:
        return len(pending_for(db, db.get(User, user.id)))
    finally:
        db.close()


def _order_approval_count(user: Optional[User]) -> int:
    if user is None:
        return 0
    from .abelorder import pending_for

    db = database.SessionLocal()
    try:
        return len(pending_for(db, db.get(User, user.id)))
    finally:
        db.close()


def _session_user(request: Request) -> Optional[User]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    db = database.SessionLocal()
    try:
        return db.get(User, user_id)
    finally:
        db.close()


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)
