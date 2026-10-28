"""Service notices, the Service Status page and Mail Setup (distribution lists, test email)."""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import database, notices
from .config import settings
from .emailer import deliver_pending, queue_email
from .models import (
    ChangeRequest, CIStatus, ConfigItem, DistributionList, NoticeKind, NoticeSeverity, NoticeStatus, ServiceNotice,
    Ticket, User, utcnow,
)
from .security import flash, get_current_user, require_manager, require_staff, verify_csrf
from .web import redirect, render

router = APIRouter()


def _get(db: Session, number: str) -> ServiceNotice:
    notice = db.scalar(select(ServiceNotice).where(ServiceNotice.number == number.upper()))
    if notice is None:
        raise HTTPException(status_code=404, detail="Notice not found")
    return notice


def _parse(form) -> tuple[dict, list[int], list[int]]:
    def dt(key) -> Optional[datetime]:
        raw = str(form.get(key, "") or "").strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            raise notices.NoticeError(f"Invalid date/time: {raw}")
    try:
        kind, severity = NoticeKind(form.get("kind", "")), NoticeSeverity(form.get("severity", "warning"))
    except ValueError:
        raise notices.NoticeError("Choose a notice type and severity.")
    data = {k: str(form.get(k, "") or "") for k in ("title", "message", "impact", "user_action", "extra_recipients")}
    data.update(kind=kind, severity=severity, start_at=dt("start_at"), end_at=dt("end_at"),
                show_banner=form.get("show_banner") == "true")
    list_ids = [int(v) for v in form.getlist("list_ids") if str(v).isdigit()]
    item_ids = [int(v) for v in form.getlist("item_ids") if str(v).isdigit()]
    return data, list_ids, item_ids


def _form_context(db: Session, notice: Optional[ServiceNotice], form: dict, change=None, ticket=None) -> dict:
    return {
        "notice": notice, "form": form, "change": change, "ticket": ticket,
        "lists": db.scalars(select(DistributionList).where(DistributionList.active.is_(True))
                            .order_by(DistributionList.name)).all(),
        "items": db.scalars(select(ConfigItem).where(ConfigItem.status != CIStatus.RETIRED)
                            .order_by(ConfigItem.name)).all(),
        "NoticeKind": NoticeKind, "NoticeSeverity": NoticeSeverity,
    }


def _echo(form) -> dict:
    values = {k: form.get(k) for k in form.keys()}
    values["list_ids"], values["item_ids"] = form.getlist("list_ids"), form.getlist("item_ids")
    return values


# --- Staff: list, create, edit ---------------------------------------------------------------

@router.get("/notices", response_class=HTMLResponse)
def list_notices(request: Request, view: str = "active", user: User = Depends(require_staff),
                 db: Session = Depends(database.get_db)):
    stmt = select(ServiceNotice)
    if view == "drafts":
        stmt = stmt.where(ServiceNotice.status == NoticeStatus.DRAFT)
    elif view == "all":
        pass
    else:
        view = "active"
        stmt = stmt.where(ServiceNotice.status == NoticeStatus.ACTIVE)
    rows = db.scalars(stmt.order_by(ServiceNotice.id.desc()).limit(300)).all()
    return render(request, "notice_list.html", {"rows": rows, "view": view}, user=user)


@router.get("/notices/new", response_class=HTMLResponse)
def new_notice(request: Request, change: str = "", ticket: str = "", user: User = Depends(require_staff),
               db: Session = Depends(database.get_db)):
    chg = db.scalar(select(ChangeRequest).where(ChangeRequest.number == change.upper())) if change else None
    tkt = db.scalar(select(Ticket).where(Ticket.number == ticket.upper())) if ticket else None
    form: dict = {"kind": "outage", "severity": "warning", "show_banner": "true"}
    if chg:
        form.update(notices.defaults_from_change(chg))
    elif tkt:
        form.update(notices.defaults_from_ticket(tkt))
    form["list_ids"] = [str(dl.id) for dl in db.scalars(select(DistributionList).where(
        DistributionList.is_default.is_(True), DistributionList.active.is_(True)))]
    return render(request, "notice_form.html", _form_context(db, None, form, chg, tkt), user=user)


@router.post("/notices/new")
async def create_notice(request: Request, background: BackgroundTasks, user: User = Depends(require_staff),
                        db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    form = await request.form()
    chg = db.scalar(select(ChangeRequest).where(ChangeRequest.number == str(form.get("change", "")).upper()))
    tkt = db.scalar(select(Ticket).where(Ticket.number == str(form.get("ticket", "")).upper()))
    try:
        data, list_ids, item_ids = _parse(form)
        notice = notices.create(db, user, data, list_ids, item_ids, chg, tkt)
    except notices.NoticeError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return render(request, "notice_form.html", _form_context(db, None, _echo(form), chg, tkt),
                      status_code=400, user=user)
    db.commit()
    flash(request, f"{notice.number} saved as a draft. Review it below, then send.")
    return redirect(f"/notices/{notice.number}")


@router.get("/notices/{number}/edit", response_class=HTMLResponse)
def edit_page(request: Request, number: str, user: User = Depends(require_staff),
              db: Session = Depends(database.get_db)):
    n = _get(db, number)
    if n.status != NoticeStatus.DRAFT:
        return redirect(f"/notices/{n.number}")
    form = {"kind": n.kind.value, "severity": n.severity.value, "title": n.title, "message": n.message,
            "impact": n.impact or "", "user_action": n.user_action or "", "extra_recipients": n.extra_recipients or "",
            "start_at": n.start_at.strftime("%Y-%m-%dT%H:%M") if n.start_at else "",
            "end_at": n.end_at.strftime("%Y-%m-%dT%H:%M") if n.end_at else "",
            "show_banner": "true" if n.show_banner else "",
            "list_ids": [str(dl.id) for dl in n.lists], "item_ids": [str(ci.id) for ci in n.items]}
    return render(request, "notice_form.html", _form_context(db, n, form, n.change, n.ticket), user=user)


@router.post("/notices/{number}/edit")
async def edit(request: Request, number: str, user: User = Depends(require_staff),
               db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    n = _get(db, number)
    form = await request.form()
    try:
        data, list_ids, item_ids = _parse(form)
        notices.update_draft(db, n, user, data, list_ids, item_ids)
    except notices.NoticeError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return render(request, "notice_form.html", _form_context(db, n, _echo(form), n.change, n.ticket),
                      status_code=400, user=user)
    db.commit()
    flash(request, "Draft saved.")
    return redirect(f"/notices/{n.number}")


@router.get("/notices/{number}", response_class=HTMLResponse)
def detail(request: Request, number: str, user: User = Depends(require_staff),
           db: Session = Depends(database.get_db)):
    n = _get(db, number)
    preview = notices._body(n, f"{n.kind.label.upper()}: {n.title}")
    return render(request, "notice_detail.html", {
        "n": n, "recipients": notices.recipients(n), "preview_subject": f"[{n.kind.tag}] {n.title}",
        "preview": preview, "can_send": user.may_send_notices, "window": notices.window_text(n),
    }, user=user)


def _action(request, background, db, n, fn, ok):
    try:
        count = fn()
    except notices.NoticeError as exc:
        db.rollback()
        flash(request, str(exc), "error")
    else:
        db.commit()
        background.add_task(deliver_pending)
        flash(request, ok.format(count=count))
    return redirect(f"/notices/{n.number}")


@router.post("/notices/{number}/send")
def send(request: Request, number: str, background: BackgroundTasks, confirm: bool = Form(False),
         user: User = Depends(require_staff), db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    n = _get(db, number)
    if not confirm:
        flash(request, "Tick the confirmation box to send.", "error")
        return redirect(f"/notices/{n.number}")
    return _action(request, background, db, n, lambda: notices.send(db, n, user), "Notice sent to {count} address(es).")


@router.post("/notices/{number}/update")
def post_update(request: Request, number: str, background: BackgroundTasks, message: str = Form(""),
                user: User = Depends(require_staff), db: Session = Depends(database.get_db),
                _: None = Depends(verify_csrf)):
    n = _get(db, number)
    return _action(request, background, db, n, lambda: notices.post_update(db, n, user, message),
                   "Update sent to {count} address(es).")


@router.post("/notices/{number}/resolve")
def resolve(request: Request, number: str, background: BackgroundTasks, message: str = Form(""),
            user: User = Depends(require_staff), db: Session = Depends(database.get_db),
            _: None = Depends(verify_csrf)):
    n = _get(db, number)
    return _action(request, background, db, n, lambda: notices.resolve(db, n, user, message),
                   "Resolved - notification sent to {count} address(es).")


@router.post("/notices/{number}/cancel")
def cancel(request: Request, number: str, background: BackgroundTasks, message: str = Form(""),
           user: User = Depends(require_staff), db: Session = Depends(database.get_db),
           _: None = Depends(verify_csrf)):
    n = _get(db, number)
    return _action(request, background, db, n, lambda: notices.cancel(db, n, user, message),
                   "Notice cancelled ({count} address(es) notified).")


# --- Everyone: Service Status -------------------------------------------------------------------------

@router.get("/status", response_class=HTMLResponse)
def status_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(database.get_db)):
    now = datetime.now()
    active = db.scalars(select(ServiceNotice).where(ServiceNotice.status == NoticeStatus.ACTIVE)
                        .order_by(ServiceNotice.start_at, ServiceNotice.sent_at)).all()
    current = [n for n in active if not n.start_at or n.start_at <= now]
    upcoming = [n for n in active if n.start_at and n.start_at > now]
    recent = db.scalars(select(ServiceNotice).where(
        ServiceNotice.status.in_([NoticeStatus.RESOLVED, NoticeStatus.CANCELLED]),
        ServiceNotice.sent_at.is_not(None), ServiceNotice.closed_at >= utcnow() - timedelta(days=14),
    ).order_by(ServiceNotice.closed_at.desc())).all()
    return render(request, "status.html", {"current": current, "upcoming": upcoming, "recent": recent,
                                           "window": notices.window_text}, user=user)


# --- Mail setup (IT managers) ------------------------------------------------------------------------------

@router.get("/admin/mail", response_class=HTMLResponse)
def mail_setup(request: Request, user: User = Depends(require_manager), db: Session = Depends(database.get_db)):
    lists = db.scalars(select(DistributionList).order_by(DistributionList.active.desc(), DistributionList.name)).all()
    return render(request, "mail_setup.html", {"lists": lists, "settings": settings}, user=user)


@router.post("/admin/mail/lists")
def save_list(request: Request, name: str = Form(...), email: str = Form(...), description: str = Form(""),
              is_default: bool = Form(False), active: bool = Form(False), list_id: str = Form(""),
              user: User = Depends(require_manager), db: Session = Depends(database.get_db),
              _: None = Depends(verify_csrf)):
    name, email = name.strip(), email.strip().lower()
    dl = db.get(DistributionList, int(list_id)) if list_id.isdigit() else DistributionList()
    if not name or not notices._EMAIL_RE.match(email):
        flash(request, "Enter a name and a valid list email address.", "error")
        return redirect("/admin/mail#lists")
    for col, value in ((DistributionList.name, name), (DistributionList.email, email)):
        clash = db.scalar(select(DistributionList).where(func.lower(col) == value.lower()))
        if clash and clash is not dl:
            flash(request, f"Another list already uses {value}.", "error")
            return redirect("/admin/mail#lists")
    dl.name, dl.email, dl.description = name, email, description.strip() or None
    dl.is_default, dl.active = is_default, active
    db.add(dl)
    db.commit()
    flash(request, f"Saved list {name}.")
    return redirect("/admin/mail#lists")


@router.post("/admin/mail/test")
def send_test(request: Request, background: BackgroundTasks, to: str = Form(...),
              user: User = Depends(require_manager), db: Session = Depends(database.get_db),
              _: None = Depends(verify_csrf)):
    to = to.strip().lower()
    if not notices._EMAIL_RE.match(to):
        flash(request, "Enter a valid email address.", "error")
        return redirect("/admin/mail")
    queue_email(db, to, f"{settings.app_name} test email",
                f"This is a test message from {settings.app_name}, sent by {user.full_name}.\n\n"
                f"If you received it, outgoing email is working.")
    db.commit()
    background.add_task(deliver_pending)
    flash(request, f"Test email queued to {to}. Check the Email Log for the result.")
    return redirect("/admin/emails")
