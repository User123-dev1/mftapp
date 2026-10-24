"""FastAPI application: web pages for requesters, service desk agents and IT managers."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import database, services
from .config import settings
from .emailer import deliver_pending
from .models import Category, EmailLog, Priority, Role, Status, Ticket, User
from .security import (
    Forbidden,
    LoginRequired,
    csrf_token,
    flash,
    get_current_user,
    get_optional_user,
    hash_password,
    login_user,
    logout_user,
    pop_flashes,
    require_manager,
    require_staff,
    verify_csrf,
    verify_password,
)

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Sort key putting Critical first (enum values sort alphabetically in the database).
PRIORITY_RANK = case(
    {Priority.CRITICAL: 0, Priority.HIGH: 1, Priority.MEDIUM: 2, Priority.LOW: 3}, value=Ticket.priority
)


def ensure_admin() -> None:
    """Create the first IT manager account if none exists."""
    db = database.SessionLocal()
    try:
        exists = db.scalar(select(User).where(User.role == Role.MANAGER))
        if exists is None:
            db.add(
                User(
                    email=settings.admin_email.lower(),
                    full_name=settings.admin_name,
                    password_hash=hash_password(settings.admin_password),
                    role=Role.MANAGER,
                )
            )
            db.commit()
            logger.warning("Created initial IT manager account %s - change its password.", settings.admin_email)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    ensure_admin()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax", max_age=60 * 60 * 12)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc: LoginRequired):
    return RedirectResponse(f"/login?next={quote(exc.next_url)}", status_code=303)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    return render(request, "error.html", {"message": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Forbidden)
async def _forbidden(request: Request, exc: Forbidden):
    return render(request, "error.html", {"message": "You do not have permission to do that."}, status_code=403)


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


def _safe_next(next_url: str) -> str:
    return next_url if next_url.startswith("/") and not next_url.startswith("//") else "/"


def _get_ticket(db: Session, number: str, user: User) -> Ticket:
    ticket = db.scalar(select(Ticket).where(Ticket.number == number.upper()))
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if not user.is_staff and ticket.requester_id != user.id:
        raise Forbidden()
    return ticket


# --- Auth -------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    return render(request, "login.html", {"next": _safe_next(next)})


@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(database.get_db),
    _: None = Depends(verify_csrf),
):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        flash(request, "Invalid email or password.", "error")
        return render(request, "login.html", {"next": _safe_next(next), "email": email}, status_code=400)
    login_user(request, user)
    return redirect(_safe_next(next))


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return render(request, "register.html", {"form": {}})


@app.post("/register")
def register(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    department: str = Form(""),
    phone: str = Form(""),
    db: Session = Depends(database.get_db),
    _: None = Depends(verify_csrf),
):
    form = {"full_name": full_name, "email": email, "department": department, "phone": phone}
    email = email.strip().lower()
    error = None
    if not full_name.strip() or "@" not in email:
        error = "Please enter your name and a valid email address."
    elif len(password) < 8:
        error = "Password must be at least 8 characters."
    elif db.scalar(select(User).where(User.email == email)):
        error = "An account with that email already exists."
    if error:
        flash(request, error, "error")
        return render(request, "register.html", {"form": form}, status_code=400)

    user = User(
        email=email,
        full_name=full_name.strip(),
        department=department.strip() or None,
        phone=phone.strip() or None,
        password_hash=hash_password(password),
        role=Role.REQUESTER,
    )
    db.add(user)
    db.commit()
    login_user(request, user)
    flash(request, "Welcome! Your account has been created.")
    return redirect("/")


@app.post("/logout")
def logout(request: Request, _: None = Depends(verify_csrf)):
    logout_user(request)
    return redirect("/login")


# --- Portal / dashboard -----------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request, user: Optional[User] = Depends(get_optional_user), db: Session = Depends(database.get_db)):
    if user is None:
        return redirect("/login")
    if user.is_staff:
        return redirect("/dashboard")
    my_open = db.scalars(
        select(Ticket)
        .where(Ticket.requester_id == user.id, Ticket.status.notin_([Status.CLOSED, Status.CANCELLED]))
        .order_by(Ticket.created_at.desc())
        .limit(10)
    ).all()
    return render(request, "portal.html", {"tickets": my_open}, user=user)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(require_staff), db: Session = Depends(database.get_db)):
    open_statuses = [s for s in Status if s.is_open]
    by_status = dict(db.execute(select(Ticket.status, func.count()).group_by(Ticket.status)).all())
    by_category = dict(
        db.execute(
            select(Ticket.category, func.count()).where(Ticket.status.in_(open_statuses)).group_by(Ticket.category)
        ).all()
    )
    unassigned = db.scalars(
        select(Ticket).where(Ticket.status == Status.NEW).order_by(Ticket.created_at)
    ).all()
    my_work = db.scalars(
        select(Ticket)
        .where(Ticket.assignee_id == user.id, Ticket.status.in_(open_statuses))
        .order_by(PRIORITY_RANK, Ticket.created_at)
    ).all()
    agents = services.active_users_with_role(db, Role.AGENT) + services.active_users_with_role(db, Role.MANAGER)
    workload = dict(
        db.execute(
            select(Ticket.assignee_id, func.count())
            .where(Ticket.status.in_(open_statuses), Ticket.assignee_id.is_not(None))
            .group_by(Ticket.assignee_id)
        ).all()
    )
    return render(
        request,
        "dashboard.html",
        {
            "by_status": by_status,
            "by_category": by_category,
            "unassigned": unassigned,
            "my_work": my_work,
            "agents": agents,
            "workload": workload,
        },
        user=user,
    )


# --- Tickets ----------------------------------------------------------------

@app.get("/tickets/new", response_class=HTMLResponse)
def new_ticket_page(request: Request, category: str = "general", user: User = Depends(get_current_user)):
    try:
        selected = Category(category)
    except ValueError:
        selected = Category.GENERAL
    return render(
        request,
        "ticket_new.html",
        {"selected": selected, "category_fields": services.CATEGORY_FIELDS, "form": {}},
        user=user,
    )


@app.post("/tickets/new")
async def create_ticket(
    request: Request,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(database.get_db),
    _: None = Depends(verify_csrf),
):
    form = await request.form()
    try:
        category = Category(form.get("category", ""))
        priority = Priority(form.get("priority", "medium"))
    except ValueError:
        flash(request, "Please choose a valid request type and priority.", "error")
        return redirect("/tickets/new")

    details = {
        key: str(form.get(f"detail_{category.value}_{key}", ""))
        for key, _label in services.CATEGORY_FIELDS.get(category, [])
    }
    try:
        ticket = services.create_ticket(
            db,
            user,
            title=str(form.get("title", "")),
            description=str(form.get("description", "")),
            category=category,
            priority=priority,
            details=details,
        )
    except services.WorkflowError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return render(
            request,
            "ticket_new.html",
            {"selected": category, "category_fields": services.CATEGORY_FIELDS, "form": dict(form)},
            status_code=400,
            user=user,
        )
    db.commit()
    background.add_task(deliver_pending)
    flash(request, f"Ticket {ticket.number} submitted. You will receive email updates.")
    return redirect(f"/tickets/{ticket.number}")


@app.get("/tickets", response_class=HTMLResponse)
def list_tickets(
    request: Request,
    status: str = "open",
    category: str = "",
    assignee: str = "",
    q: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(database.get_db),
):
    stmt = select(Ticket)
    if not user.is_staff:
        stmt = stmt.where(Ticket.requester_id == user.id)

    if status == "open":
        stmt = stmt.where(Ticket.status.in_([s for s in Status if s.is_open]))
    elif status and status != "all":
        try:
            stmt = stmt.where(Ticket.status == Status(status))
        except ValueError:
            pass
    if category:
        try:
            stmt = stmt.where(Ticket.category == Category(category))
        except ValueError:
            pass
    if user.is_staff and assignee:
        if assignee == "me":
            stmt = stmt.where(Ticket.assignee_id == user.id)
        elif assignee == "none":
            stmt = stmt.where(Ticket.assignee_id.is_(None))
        elif assignee.isdigit():
            stmt = stmt.where(Ticket.assignee_id == int(assignee))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Ticket.number.ilike(like), Ticket.title.ilike(like), Ticket.description.ilike(like)))

    tickets = db.scalars(stmt.order_by(Ticket.created_at.desc()).limit(500)).all()
    staff = (
        services.active_users_with_role(db, Role.AGENT) + services.active_users_with_role(db, Role.MANAGER)
        if user.is_staff
        else []
    )
    return render(
        request,
        "ticket_list.html",
        {
            "tickets": tickets,
            "filters": {"status": status, "category": category, "assignee": assignee, "q": q},
            "staff": staff,
        },
        user=user,
    )


@app.get("/tickets/{number}", response_class=HTMLResponse)
def ticket_detail(
    request: Request, number: str, user: User = Depends(get_current_user), db: Session = Depends(database.get_db)
):
    ticket = _get_ticket(db, number, user)
    comments = [c for c in ticket.comments if user.is_staff or not c.internal]
    staff = []
    if user.role == Role.MANAGER:
        staff = services.active_users_with_role(db, Role.AGENT) + services.active_users_with_role(db, Role.MANAGER)
    return render(
        request,
        "ticket_detail.html",
        {
            "ticket": ticket,
            "details": services.parse_details(ticket),
            "comments": comments,
            "staff": staff,
            "can_work": services.can_work(user, ticket),
            "next_statuses": services.allowed_statuses(user, ticket),
        },
        user=user,
    )


def _ticket_action(request: Request, background: BackgroundTasks, db: Session, ticket: Ticket, action, ok: str):
    try:
        action()
    except services.WorkflowError as exc:
        db.rollback()
        flash(request, str(exc), "error")
    else:
        db.commit()
        background.add_task(deliver_pending)
        flash(request, ok)
    return redirect(f"/tickets/{ticket.number}")


@app.post("/tickets/{number}/assign")
def assign(
    request: Request,
    number: str,
    background: BackgroundTasks,
    assignee_id: int = Form(...),
    user: User = Depends(require_manager),
    db: Session = Depends(database.get_db),
    _: None = Depends(verify_csrf),
):
    ticket = _get_ticket(db, number, user)
    agent = db.get(User, assignee_id)
    if agent is None:
        raise HTTPException(status_code=400, detail="Unknown user")
    return _ticket_action(
        request, background, db, ticket,
        lambda: services.assign_ticket(db, ticket, agent, user),
        f"Assigned to {agent.full_name}.",
    )


@app.post("/tickets/{number}/status")
def update_status(
    request: Request,
    number: str,
    background: BackgroundTasks,
    status: str = Form(...),
    resolution_notes: str = Form(""),
    user: User = Depends(require_staff),
    db: Session = Depends(database.get_db),
    _: None = Depends(verify_csrf),
):
    ticket = _get_ticket(db, number, user)
    try:
        new_status = Status(status)
    except ValueError:
        raise HTTPException(status_code=400, detail="Unknown status")
    return _ticket_action(
        request, background, db, ticket,
        lambda: services.change_status(db, ticket, new_status, user, resolution_notes),
        f"Status updated to {new_status.label}.",
    )


@app.post("/tickets/{number}/update")
def update_ticket(
    request: Request,
    number: str,
    background: BackgroundTasks,
    category: str = Form(...),
    priority: str = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(database.get_db),
    _: None = Depends(verify_csrf),
):
    ticket = _get_ticket(db, number, user)
    try:
        new_category, new_priority = Category(category), Priority(priority)
    except ValueError:
        raise HTTPException(status_code=400, detail="Unknown category or priority")
    return _ticket_action(
        request, background, db, ticket,
        lambda: services.update_fields(db, ticket, user, new_category, new_priority),
        "Ticket updated.",
    )


@app.post("/tickets/{number}/comment")
def comment(
    request: Request,
    number: str,
    background: BackgroundTasks,
    body: str = Form(...),
    internal: bool = Form(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(database.get_db),
    _: None = Depends(verify_csrf),
):
    ticket = _get_ticket(db, number, user)
    return _ticket_action(
        request, background, db, ticket,
        lambda: services.add_comment(db, ticket, user, body, internal=internal and user.is_staff),
        "Work note added." if internal and user.is_staff else "Comment added.",
    )


@app.post("/tickets/{number}/reopen")
def reopen(
    request: Request,
    number: str,
    background: BackgroundTasks,
    reason: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(database.get_db),
    _: None = Depends(verify_csrf),
):
    ticket = _get_ticket(db, number, user)
    return _ticket_action(
        request, background, db, ticket,
        lambda: services.requester_reopen(db, ticket, user, reason),
        "Ticket reopened.",
    )


@app.post("/tickets/{number}/cancel")
def cancel(
    request: Request,
    number: str,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(database.get_db),
    _: None = Depends(verify_csrf),
):
    ticket = _get_ticket(db, number, user)
    return _ticket_action(
        request, background, db, ticket,
        lambda: services.requester_cancel(db, ticket, user),
        "Ticket cancelled.",
    )


# --- Administration (IT manager) ---------------------------------------------

@app.get("/admin/users", response_class=HTMLResponse)
def users_page(request: Request, user: User = Depends(require_manager), db: Session = Depends(database.get_db)):
    users = db.scalars(select(User).order_by(User.role, User.full_name)).all()
    return render(request, "users.html", {"users": users}, user=user)


@app.post("/admin/users")
def create_user(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("agent"),
    department: str = Form(""),
    user: User = Depends(require_manager),
    db: Session = Depends(database.get_db),
    _: None = Depends(verify_csrf),
):
    email = email.strip().lower()
    try:
        new_role = Role(role)
    except ValueError:
        new_role = Role.AGENT
    if "@" not in email or not full_name.strip() or len(password) < 8:
        flash(request, "Name, valid email and a password of 8+ characters are required.", "error")
    elif db.scalar(select(User).where(User.email == email)):
        flash(request, "A user with that email already exists.", "error")
    else:
        db.add(
            User(
                email=email,
                full_name=full_name.strip(),
                department=department.strip() or None,
                password_hash=hash_password(password),
                role=new_role,
            )
        )
        db.commit()
        flash(request, f"Created {new_role.label} {full_name}.")
    return redirect("/admin/users")


@app.post("/admin/users/{user_id}")
def edit_user(
    request: Request,
    user_id: int,
    role: str = Form(...),
    is_active: bool = Form(False),
    password: str = Form(""),
    user: User = Depends(require_manager),
    db: Session = Depends(database.get_db),
    _: None = Depends(verify_csrf),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        new_role = Role(role)
    except ValueError:
        raise HTTPException(status_code=400, detail="Unknown role")
    if target.id == user.id and (new_role != Role.MANAGER or not is_active):
        flash(request, "You cannot remove your own manager role or deactivate yourself.", "error")
        return redirect("/admin/users")
    if password and len(password) < 8:
        flash(request, "Password must be at least 8 characters.", "error")
        return redirect("/admin/users")
    target.role = new_role
    target.is_active = is_active
    if password:
        target.password_hash = hash_password(password)
    db.commit()
    flash(request, f"Updated {target.full_name}.")
    return redirect("/admin/users")


@app.get("/admin/emails", response_class=HTMLResponse)
def email_log(request: Request, user: User = Depends(require_manager), db: Session = Depends(database.get_db)):
    emails = db.scalars(select(EmailLog).order_by(EmailLog.created_at.desc(), EmailLog.id.desc()).limit(200)).all()
    return render(request, "emails.html", {"emails": emails, "smtp_configured": bool(settings.smtp_host)}, user=user)


# --- Profile -------------------------------------------------------------------

@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, user: User = Depends(get_current_user)):
    return render(request, "profile.html", user=user)


@app.post("/profile/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(database.get_db),
    _: None = Depends(verify_csrf),
):
    if not verify_password(current_password, user.password_hash):
        flash(request, "Current password is incorrect.", "error")
    elif len(new_password) < 8:
        flash(request, "New password must be at least 8 characters.", "error")
    else:
        user.password_hash = hash_password(new_password)
        db.commit()
        flash(request, "Password changed.")
    return redirect("/profile")
