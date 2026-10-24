"""Password hashing, session-based auth helpers and CSRF protection."""

import hashlib
import hmac
import secrets
from typing import Optional

from fastapi import Depends, Form, Request
from sqlalchemy.orm import Session

from .database import get_db
from .models import Role, User

_ITERATIONS = 260_000

# Stored instead of a hash for accounts that sign in only through SSO.
NO_PASSWORD = "!sso-only"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))
    return hmac.compare_digest(candidate.hex(), digest)


class LoginRequired(Exception):
    """Raised when a page needs a signed-in user; handled by redirecting to /login."""

    def __init__(self, next_url: str = "/"):
        self.next_url = next_url


class Forbidden(Exception):
    """Raised when the signed-in user lacks the role for a page."""


def login_user(request: Request, user: User) -> None:
    request.session.clear()
    request.session["user_id"] = user.id


def logout_user(request: Request) -> None:
    request.session.clear()


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        request.session.pop("user_id", None)
        return None
    return user


def get_current_user(request: Request, user: Optional[User] = Depends(get_optional_user)) -> User:
    if user is None:
        raise LoginRequired(str(request.url.path))
    return user


def require_staff(user: User = Depends(get_current_user)) -> User:
    if not user.is_staff:
        raise Forbidden()
    return user


def require_manager(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.MANAGER:
        raise Forbidden()
    return user


# --- CSRF -----------------------------------------------------------------

def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def verify_csrf(request: Request, csrf_token: str = Form("")) -> None:
    expected = request.session.get("csrf")
    if not expected or not hmac.compare_digest(expected, csrf_token):
        raise Forbidden()


# --- Flash messages -------------------------------------------------------

def flash(request: Request, message: str, category: str = "success") -> None:
    request.session.setdefault("flash", []).append([category, message])


def pop_flashes(request: Request) -> list:
    return request.session.pop("flash", [])
