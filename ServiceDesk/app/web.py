"""Shared helpers for rendering pages."""

from pathlib import Path
from typing import Optional

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import database, sso
from .config import settings
from .models import Category, CIStatus, CIType, Priority, Role, Status, User
from .security import csrf_token, pop_flashes

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def render(request: Request, name: str, context: Optional[dict] = None, status_code: int = 200, user=None):
    ctx = {
        "app_name": settings.app_name,
        "user": user if user is not None else _session_user(request),
        "csrf_token": csrf_token(request),
        "flashes": pop_flashes(request),
        "Status": Status,
        "Category": Category,
        "Priority": Priority,
        "Role": Role,
        "CIType": CIType,
        "CIStatus": CIStatus,
        "sso_providers": sso.providers(),
        "local_login": settings.local_login,
    }
    ctx.update(context or {})
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


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
