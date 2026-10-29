"""Change management pages, change policy settings and CI relationships."""

import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import changes, database, services
from .emailer import deliver_pending
from .models import (
    Category, ChangeRequest, ChangeState, ChangeType, CIRelationship, CIStatus, ConfigItem, Level, Ticket, User,
)
from .security import Forbidden, flash, require_manager, require_staff, verify_csrf
from .web import redirect, render

router = APIRouter()


def _get_change(db: Session, number: str) -> ChangeRequest:
    change = db.scalar(select(ChangeRequest).where(ChangeRequest.number == number.upper()))
    if change is None:
        raise HTTPException(status_code=404, detail="Change not found")
    return change


def _parse_dt(raw) -> Optional[datetime]:
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        raise changes.ChangeError(f"Invalid date/time: {raw}")


def _form_data(db: Session, form) -> tuple[dict, list[ConfigItem], Optional[User]]:
    try:
        data = {
            "change_type": ChangeType(form.get("change_type", "normal")),
            "risk": Level(form.get("risk", "medium")),
            "impact": Level(form.get("impact", "medium")),
        }
    except ValueError:
        raise changes.ChangeError("Choose a valid type, risk and impact.")
    for f in ("title", "description", "justification", "implementation_plan", "backout_plan", "test_plan"):
        data[f] = str(form.get(f, ""))
    data["planned_start"] = _parse_dt(form.get("planned_start"))
    data["planned_end"] = _parse_dt(form.get("planned_end"))
    ids = [int(v) for v in form.getlist("item_ids") if str(v).isdigit()]
    items = list(db.scalars(select(ConfigItem).where(ConfigItem.id.in_(ids)))) if ids else []
    impl_id = str(form.get("implementer_id", ""))
    implementer = db.get(User, int(impl_id)) if impl_id.isdigit() else None
    return data, items, implementer


def _form_context(db: Session, change: Optional[ChangeRequest], form: dict, ticket: Optional[Ticket]) -> dict:
    staff = [u for u in db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name))
             if u.is_staff]
    all_items = db.scalars(select(ConfigItem).where(ConfigItem.status != CIStatus.RETIRED)
                           .order_by(ConfigItem.name)).all()
    if change is not None:
        selected = {ci.id for ci in change.items}
    else:
        selected = {int(v) for v in form.get("item_ids", []) if str(v).isdigit()}
    return {"change": change, "form": form, "ticket": ticket, "staff": staff, "all_items": all_items,
            "selected_items": selected, "ChangeType": ChangeType, "Level": Level}


# --- List / schedule ----------------------------------------------------------------------

@router.get("/changes", response_class=HTMLResponse)
def list_changes(request: Request, view: str = "active", q: str = "", user: User = Depends(require_staff),
                 db: Session = Depends(database.get_db)):
    stmt = select(ChangeRequest)
    if view == "approvals":
        rows = changes.pending_for(db, user)
    else:
        if view == "schedule":
            stmt = stmt.where(ChangeRequest.state.in_([ChangeState.APPROVED, ChangeState.IN_PROGRESS,
                                                       ChangeState.PENDING_APPROVAL]),
                              ChangeRequest.planned_end >= datetime.now() - timedelta(days=1))
            stmt = stmt.order_by(ChangeRequest.planned_start)
        elif view == "mine":
            stmt = stmt.where(or_(ChangeRequest.requested_by_id == user.id, ChangeRequest.implementer_id == user.id))
            stmt = stmt.order_by(ChangeRequest.id.desc())
        elif view == "all":
            stmt = stmt.order_by(ChangeRequest.id.desc())
        else:
            view = "active"
            stmt = stmt.where(ChangeRequest.state.in_([s for s in ChangeState if s.is_active]))
            stmt = stmt.order_by(ChangeRequest.planned_start.is_(None), ChangeRequest.planned_start)
        if q.strip():
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(ChangeRequest.number.ilike(like), ChangeRequest.title.ilike(like)))
        rows = db.scalars(stmt.limit(500)).all()

    by_day: dict = {}
    if view == "schedule":
        for c in rows:
            by_day.setdefault(c.planned_start.date() if c.planned_start else None, []).append(c)
    return render(request, "change_list.html", {
        "changes": rows, "view": view, "q": q, "by_day": by_day,
        "conflicts": {c.id: changes.conflicts(db, c) for c in rows if c.state.is_active},
    }, user=user)


# --- Create / edit ------------------------------------------------------------------------------

@router.get("/changes/new", response_class=HTMLResponse)
def new_change_page(request: Request, ticket: str = "", user: User = Depends(require_staff),
                    db: Session = Depends(database.get_db)):
    t = db.scalar(select(Ticket).where(Ticket.number == ticket.upper())) if ticket else None
    if t is not None and not services.can_see_ticket(user, t):
        raise Forbidden()
    form: dict = {"change_type": "normal", "risk": "medium", "impact": "medium", "implementer_id": str(user.id)}
    if t is not None:
        details = "\n".join(f"{label}: {value}" for label, value in services.parse_details(t))
        form.update({
            "title": t.title,
            "description": f"Requested in {t.number} by {t.requester.full_name}.\n\n{t.description}"
                           + (f"\n\n{details}" if details else ""),
            "implementer_id": str(t.assignee_id or user.id),
            "item_ids": [str(t.ci_id)] if t.ci_id else [],
            "ticket": t.number,
        })
        if t.category == Category.INCIDENT:
            form["change_type"] = "emergency" if t.priority.value == "critical" else "normal"
    return render(request, "change_form.html", _form_context(db, None, form, t), user=user)


@router.post("/changes/new")
async def create_change(request: Request, background: BackgroundTasks, user: User = Depends(require_staff),
                        db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    form = await request.form()
    number = str(form.get("ticket", "")).upper()
    ticket = db.scalar(select(Ticket).where(Ticket.number == number)) if number else None
    if ticket is not None and not services.can_see_ticket(user, ticket):
        raise Forbidden()
    try:
        data, items, implementer = _form_data(db, form)
        change = changes.create_change(db, user, data, items, implementer, ticket)
        if form.get("action") == "submit":
            changes.submit(db, change, user)
    except changes.ChangeError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        values = {k: form.get(k) for k in form.keys()}
        values["item_ids"] = form.getlist("item_ids")
        return render(request, "change_form.html", _form_context(db, None, values, ticket), status_code=400,
                      user=user)
    db.commit()
    background.add_task(deliver_pending)
    flash(request, f"{change.number} {'submitted' if form.get('action') == 'submit' else 'saved as draft'}.")
    return redirect(f"/changes/{change.number}")


@router.get("/changes/{number}/edit", response_class=HTMLResponse)
def edit_change_page(request: Request, number: str, user: User = Depends(require_staff),
                     db: Session = Depends(database.get_db)):
    change = _get_change(db, number)
    if change.state != ChangeState.DRAFT or not changes.can_edit(user, change):
        flash(request, "Only the requester, implementer or a manager can edit a draft change.", "error")
        return redirect(f"/changes/{change.number}")
    return render(request, "change_form.html", _form_context(db, change, {}, change.ticket), user=user)


@router.post("/changes/{number}/edit")
async def edit_change(request: Request, number: str, background: BackgroundTasks,
                      user: User = Depends(require_staff), db: Session = Depends(database.get_db),
                      _: None = Depends(verify_csrf)):
    change = _get_change(db, number)
    form = await request.form()
    try:
        data, items, implementer = _form_data(db, form)
        changes.update_change(db, change, user, data, items, implementer)
        if form.get("action") == "submit":
            changes.submit(db, change, user)
    except changes.ChangeError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return redirect(f"/changes/{change.number}/edit")
    db.commit()
    background.add_task(deliver_pending)
    flash(request, "Submitted." if form.get("action") == "submit" else "Saved.")
    return redirect(f"/changes/{change.number}")


# --- Detail / actions ----------------------------------------------------------------------------

@router.get("/changes/{number}", response_class=HTMLResponse)
def change_detail(request: Request, number: str, user: User = Depends(require_staff),
                  db: Session = Depends(database.get_db)):
    change = _get_change(db, number)
    return render(request, "change_detail.html", {
        "change": change,
        "can_edit": changes.can_edit(user, change),
        "can_vote": changes.can_vote(user, change),
        "approvers": changes.approvers(db, change) if change.state == ChangeState.PENDING_APPROVAL else [],
        "impacted": changes.impacted(db, change.items),
        "conflicts": changes.conflicts(db, change),
        "current_votes": [a for a in change.approvals if a.current],
        "can_see_ticket": change.ticket is not None and services.can_see_ticket(user, change.ticket),
        "old_votes": [a for a in change.approvals if not a.current],
    }, user=user)


def _action(request: Request, background: BackgroundTasks, db: Session, change: ChangeRequest, fn, ok: str):
    try:
        fn()
    except (changes.ChangeError, services.WorkflowError) as exc:
        db.rollback()
        flash(request, str(exc), "error")
    else:
        db.commit()
        background.add_task(deliver_pending)
        flash(request, ok)
    return redirect(f"/changes/{change.number}")


@router.post("/changes/{number}/submit")
def submit_change(request: Request, number: str, background: BackgroundTasks, user: User = Depends(require_staff),
                  db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    change = _get_change(db, number)
    return _action(request, background, db, change, lambda: changes.submit(db, change, user), "Submitted.")


@router.post("/changes/{number}/decide")
def decide_change(request: Request, number: str, background: BackgroundTasks, decision: str = Form(...),
                  comment: str = Form(""), user: User = Depends(require_staff),
                  db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    change = _get_change(db, number)
    approve = decision == "approve"
    return _action(request, background, db, change, lambda: changes.decide(db, change, user, approve, comment),
                   "Approved." if approve else "Rejected.")


@router.post("/changes/{number}/revise")
def revise_change(request: Request, number: str, background: BackgroundTasks, user: User = Depends(require_staff),
                  db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    change = _get_change(db, number)
    return _action(request, background, db, change, lambda: changes.revise(db, change, user),
                   "Back to draft - edit and resubmit.")


@router.post("/changes/{number}/start")
def start_change(request: Request, number: str, background: BackgroundTasks, user: User = Depends(require_staff),
                 db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    change = _get_change(db, number)
    return _action(request, background, db, change, lambda: changes.start(db, change, user),
                   "Implementation started.")


@router.post("/changes/{number}/finish")
def finish_change(request: Request, number: str, background: BackgroundTasks, outcome: str = Form(...),
                  notes: str = Form(""), user: User = Depends(require_staff),
                  db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    change = _get_change(db, number)
    success = outcome == "completed"
    return _action(request, background, db, change, lambda: changes.finish(db, change, user, success, notes),
                   "Change completed." if success else "Change marked as failed.")


@router.post("/changes/{number}/cancel")
def cancel_change(request: Request, number: str, background: BackgroundTasks, reason: str = Form(""),
                  user: User = Depends(require_staff), db: Session = Depends(database.get_db),
                  _: None = Depends(verify_csrf)):
    change = _get_change(db, number)
    return _action(request, background, db, change, lambda: changes.cancel(db, change, user, reason),
                   "Change cancelled.")


# --- Policy settings -------------------------------------------------------------------------

@router.get("/admin/change-settings", response_class=HTMLResponse)
def change_settings_page(request: Request, user: User = Depends(require_manager),
                         db: Session = Depends(database.get_db)):
    controlled = db.scalars(select(ConfigItem).where(ConfigItem.change_controlled.is_(True),
                                                     ConfigItem.status != CIStatus.RETIRED)
                            .order_by(ConfigItem.name)).all()
    people = db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name)).all()
    return render(request, "change_settings.html", {
        "policy": changes.policy(db), "controlled": controlled,
        "approvers": [u for u in people if u.can_approve_changes],
    }, user=user)


@router.post("/admin/change-settings")
async def save_change_settings(request: Request, user: User = Depends(require_manager),
                               db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    form = await request.form()
    cats = [c.value for c in Category if c.value in form.getlist("categories")]
    try:
        normal = max(1, min(10, int(form.get("approvals_normal", 1))))
        high = max(1, min(10, int(form.get("approvals_high_risk", 2))))
    except ValueError:
        flash(request, "Approval counts must be numbers.", "error")
        return redirect("/admin/change-settings")
    changes.set_setting(db, "change.categories", json.dumps(cats))
    changes.set_setting(db, "change.approvals_normal", str(normal))
    changes.set_setting(db, "change.approvals_high_risk", str(high))
    db.commit()
    flash(request, "Change policy saved.")
    return redirect("/admin/change-settings")


# --- CI relationships ---------------------------------------------------------------------------

@router.post("/cmdb/{ci_id:int}/relationships")
def add_relationship(request: Request, ci_id: int, other_id: int = Form(...), direction: str = Form("depends_on"),
                     user: User = Depends(require_staff), db: Session = Depends(database.get_db),
                     _: None = Depends(verify_csrf)):
    ci, other = db.get(ConfigItem, ci_id), db.get(ConfigItem, other_id)
    if ci is None or other is None:
        raise HTTPException(status_code=404, detail="Item not found")
    source, target = (ci, other) if direction == "depends_on" else (other, ci)
    try:
        changes.add_relationship(db, source, target)
    except changes.ChangeError as exc:
        db.rollback()
        flash(request, str(exc), "error")
    else:
        db.commit()
        flash(request, f"{source.name} now depends on {target.name}.")
    return redirect(f"/cmdb/{ci_id}#relationships")


@router.post("/cmdb/{ci_id:int}/relationships/{rel_id:int}/delete")
def delete_relationship(request: Request, ci_id: int, rel_id: int, user: User = Depends(require_staff),
                        db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    rel = db.get(CIRelationship, rel_id)
    if rel is not None and ci_id in (rel.source_id, rel.target_id):
        db.delete(rel)
        db.commit()
        flash(request, "Relationship removed.")
    return redirect(f"/cmdb/{ci_id}#relationships")
