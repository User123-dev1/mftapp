"""AbelOrder pages: purchase request form, approvals, procurement and settings."""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from . import abelorder as ao
from . import database
from .abelorder_pdf import render as render_pdf
from .emailer import deliver_pending
from .models import (
    Department, PRStatus, PurchaseApproval, PurchaseAttachment, PurchaseRequest, Role, Urgency, User, Vendor,
)
from .security import Forbidden, flash, get_current_user, require_manager, verify_csrf
from .web import redirect, render

router = APIRouter()


def _get(db: Session, number: str, user: User) -> PurchaseRequest:
    pr = db.scalar(select(PurchaseRequest).where(PurchaseRequest.number == number.upper()))
    if pr is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if not ao.can_view(user, pr):
        raise Forbidden()
    return pr


def _form_context(db: Session, user: User, pr: Optional[PurchaseRequest], form: dict) -> dict:
    people = db.scalars(select(User).where(User.is_active.is_(True), User.id != user.id).order_by(User.full_name)).all()
    pol = ao.policy(db)
    return {
        "pr": pr, "form": form, "people": people, "Urgency": Urgency,
        "departments": db.scalars(select(Department).order_by(Department.name)).all(),
        "vendors": db.scalars(select(Vendor).order_by(Vendor.name)).all(),
        "uoms": ao.UOM_CHOICES,
        "items": form["items"] if "items" in form else ao.form_items(pr),
        "policy": pol, "locked_supervisor": user.supervisor,
        "allowed_ext": ", ".join(sorted(ao.ALLOWED_EXTENSIONS)),
    }


def _form_values(form) -> dict:
    """Echo the submitted form back after a validation error (including the item grid)."""
    values = {k: form.get(k) for k in form.keys() if not k.startswith("item_") and k != "quotes"}
    cols = [form.getlist(k) for k in ("item_qty", "item_uom", "item_desc", "item_catalog", "item_unit_cost")]
    values["items"] = [{"qty": q, "uom": u, "desc": d, "catalog": c, "cost": x} for q, u, d, c, x in zip(*cols)]
    return values


def _discard_new_files(db: Session) -> None:
    """Delete files written during a request that is being rolled back."""
    for path in db.info.pop("ao_new_files", []):
        path.unlink(missing_ok=True)


async def _save_uploads(db: Session, pr: PurchaseRequest, user: User, form) -> list[str]:
    errors = []
    for f in form.getlist("quotes"):
        if isinstance(f, UploadFile) and f.filename:
            data = await f.read(ao.MAX_UPLOAD_BYTES + 1)
            try:
                ao.add_attachment(db, pr, user, f.filename, data)
            except ao.OrderError as exc:
                errors.append(str(exc))
    return errors


# --- Lists ---------------------------------------------------------------------------------------

@router.get("/abelorder", response_class=HTMLResponse)
def list_requests(request: Request, view: str = "mine", q: str = "", user: User = Depends(get_current_user),
                  db: Session = Depends(database.get_db)):
    sees_all = user.is_procurement or user.role == Role.MANAGER
    if view == "approvals":
        rows = ao.pending_for(db, user)
    else:
        stmt = select(PurchaseRequest)
        if view == "procurement" and sees_all:
            stmt = stmt.where(PurchaseRequest.status == PRStatus.APPROVED).order_by(PurchaseRequest.approved_at)
        elif view == "all" and sees_all:
            stmt = stmt.order_by(PurchaseRequest.id.desc())
        elif view == "signed":
            stmt = (stmt.join(PurchaseApproval).where(PurchaseApproval.approver_id == user.id,
                                                      PurchaseApproval.decided_at.is_not(None))
                    .distinct().order_by(PurchaseRequest.id.desc()))
        else:
            view = "mine"
            stmt = stmt.where(PurchaseRequest.requester_id == user.id).order_by(PurchaseRequest.id.desc())
        if q.strip():
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(PurchaseRequest.number.ilike(like), PurchaseRequest.vendor_name.ilike(like),
                                  PurchaseRequest.project_number.ilike(like), PurchaseRequest.po_number.ilike(like),
                                  PurchaseRequest.requester_name.ilike(like)))
        rows = db.scalars(stmt.limit(500)).all()
    return render(request, "ao_list.html", {"rows": rows, "view": view, "q": q, "sees_all": sees_all,
                                            "money": ao.money}, user=user)


# --- Create / edit ---------------------------------------------------------------------------------

@router.get("/abelorder/new", response_class=HTMLResponse)
def new_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(database.get_db)):
    form = {"department_id": str(user.department_id or ""), "supervisor_id": str(user.supervisor_id or ""),
            "urgency": "normal", "vendor_mode": "existing"}
    return render(request, "ao_form.html", _form_context(db, user, None, form), user=user)


@router.post("/abelorder/new")
async def create(request: Request, background: BackgroundTasks, user: User = Depends(get_current_user),
                 db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    form = await request.form()
    submitting = form.get("action") == "submit"
    data = ao.parse_form(form)
    errors = ao.validate_for_submit(db, data) if submitting else data.errors
    pr = None
    if not errors:
        try:
            pr = ao.create_request(db, user, data)
            errors = await _save_uploads(db, pr, user, form)
            if not errors and submitting:
                ao.submit(db, pr, user)
        except ao.OrderError as exc:
            errors = [str(exc)]
    if errors:
        _discard_new_files(db)
        db.rollback()
        for err in errors:
            flash(request, err, "error")
        return render(request, "ao_form.html", _form_context(db, user, None, _form_values(form)), status_code=400,
                      user=user)
    db.commit()
    db.info.pop("ao_new_files", None)
    background.add_task(deliver_pending)
    flash(request, f"{pr.number} " + ("submitted for approval." if submitting else "saved as a draft."))
    return redirect(f"/abelorder/{pr.number}")


@router.get("/abelorder/{number}/edit", response_class=HTMLResponse)
def edit_page(request: Request, number: str, user: User = Depends(get_current_user),
              db: Session = Depends(database.get_db)):
    pr = _get(db, number, user)
    if pr.status != PRStatus.DRAFT or pr.requester_id != user.id:
        flash(request, "Only the requester can edit a draft.", "error")
        return redirect(f"/abelorder/{pr.number}")
    form = {"justification": pr.justification, "urgency": pr.urgency.value,
            "needed_by": pr.needed_by.isoformat() if pr.needed_by else "", "project_number": pr.project_number,
            "department_id": str(pr.department_id or ""), "supervisor_id": str(pr.supervisor_id or ""),
            "vendor_mode": "new" if pr.vendor_is_new else "existing", "vendor_id": str(pr.vendor_id or ""),
            "vendor_name": pr.vendor_name, "vendor_contact_name": pr.vendor_contact_name,
            "vendor_contact_email": pr.vendor_contact_email, "notes": pr.notes or ""}
    return render(request, "ao_form.html", _form_context(db, user, pr, form), user=user)


@router.post("/abelorder/{number}/edit")
async def edit(request: Request, number: str, background: BackgroundTasks, user: User = Depends(get_current_user),
               db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    pr = _get(db, number, user)
    form = await request.form()
    submitting = form.get("action") == "submit"
    data = ao.parse_form(form)
    errors = ao.validate_for_submit(db, data) if submitting else data.errors
    if not errors:
        try:
            ao.update_request(db, pr, user, data)
            errors = await _save_uploads(db, pr, user, form)
            if not errors and submitting:
                ao.submit(db, pr, user)
        except ao.OrderError as exc:
            errors = [str(exc)]
    if errors:
        _discard_new_files(db)
        db.rollback()
        for err in errors:
            flash(request, err, "error")
        return render(request, "ao_form.html", _form_context(db, user, pr, _form_values(form)), status_code=400,
                      user=user)
    db.commit()
    db.info.pop("ao_new_files", None)
    background.add_task(deliver_pending)
    flash(request, "Submitted for approval." if submitting else "Draft saved.")
    return redirect(f"/abelorder/{pr.number}")


# --- Detail & actions ------------------------------------------------------------------------------

@router.get("/abelorder/{number}", response_class=HTMLResponse)
def detail(request: Request, number: str, user: User = Depends(get_current_user),
           db: Session = Depends(database.get_db)):
    pr = _get(db, number, user)
    return render(request, "ao_detail.html", {
        "pr": pr, "money": ao.money, "unit_money": ao.unit_money, "qty_text": ao.qty_text,
        "can_decide": ao.can_decide(user, pr), "can_attach": ao.can_attach(user, pr),
        "is_procurement": user.is_procurement or user.role == Role.MANAGER,
        "policy": ao.policy(db),
    }, user=user)


def _action(request: Request, background: BackgroundTasks, db: Session, pr: PurchaseRequest, fn, ok: str):
    try:
        fn()
    except ao.OrderError as exc:
        db.rollback()
        flash(request, str(exc), "error")
    else:
        db.commit()
        background.add_task(deliver_pending)
        flash(request, ok)
    return redirect(f"/abelorder/{pr.number}")


@router.post("/abelorder/{number}/submit")
def submit(request: Request, number: str, background: BackgroundTasks, user: User = Depends(get_current_user),
           db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    pr = _get(db, number, user)

    def go():
        data = ao.input_from_request(pr)
        errors = ao.validate_for_submit(db, data)
        if errors:
            raise ao.OrderError(" ".join(errors))
        ao.submit(db, pr, user)
    return _action(request, background, db, pr, go, "Submitted for approval.")


@router.post("/abelorder/{number}/decide")
def decide(request: Request, number: str, background: BackgroundTasks, decision: str = Form(...),
           comment: str = Form(""), user: User = Depends(get_current_user), db: Session = Depends(database.get_db),
           _: None = Depends(verify_csrf)):
    pr = _get(db, number, user)
    approve = decision == "approve"
    return _action(request, background, db, pr, lambda: ao.decide(db, pr, user, approve, comment),
                   "Approved - thank you." if approve else "Rejected - the requester has been notified.")


@router.post("/abelorder/{number}/revise")
def revise(request: Request, number: str, background: BackgroundTasks, user: User = Depends(get_current_user),
           db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    pr = _get(db, number, user)
    return _action(request, background, db, pr, lambda: ao.revise(db, pr, user), "Back to draft - edit and resubmit.")


@router.post("/abelorder/{number}/cancel")
def cancel(request: Request, number: str, background: BackgroundTasks, reason: str = Form(""),
           user: User = Depends(get_current_user), db: Session = Depends(database.get_db),
           _: None = Depends(verify_csrf)):
    pr = _get(db, number, user)
    return _action(request, background, db, pr, lambda: ao.cancel(db, pr, user, reason), "Request cancelled.")


@router.post("/abelorder/{number}/po")
def issue_po(request: Request, number: str, background: BackgroundTasks, po_number: str = Form(""),
             user: User = Depends(get_current_user), db: Session = Depends(database.get_db),
             _: None = Depends(verify_csrf)):
    pr = _get(db, number, user)
    return _action(request, background, db, pr, lambda: ao.issue_po(db, pr, user, po_number),
                   "PO number saved - the requester has been emailed.")


@router.get("/abelorder/{number}/pdf")
def download_pdf(number: str, user: User = Depends(get_current_user), db: Session = Depends(database.get_db)):
    pr = _get(db, number, user)
    return Response(render_pdf(pr), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{pr.number}.pdf"'})


# --- Attachments -----------------------------------------------------------------------------------------

@router.post("/abelorder/{number}/attachments")
async def upload(request: Request, number: str, user: User = Depends(get_current_user),
                 db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    pr = _get(db, number, user)
    if not ao.can_attach(user, pr):
        raise Forbidden()
    form = await request.form()
    errors = await _save_uploads(db, pr, user, form)
    for err in errors:
        flash(request, err, "error")
    db.commit()
    db.info.pop("ao_new_files", None)
    if not errors:
        flash(request, "File attached.")
    return redirect(f"/abelorder/{pr.number}#attachments")


@router.get("/abelorder/{number}/attachments/{att_id:int}")
def download(number: str, att_id: int, user: User = Depends(get_current_user),
             db: Session = Depends(database.get_db)):
    pr = _get(db, number, user)
    att = db.get(PurchaseAttachment, att_id)
    if att is None or att.request_id != pr.id or not ao.attachment_path(att).exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(ao.attachment_path(att), media_type=att.content_type, filename=att.filename,
                        content_disposition_type="attachment")


@router.post("/abelorder/{number}/attachments/{att_id:int}/delete")
def delete_attachment(request: Request, number: str, att_id: int, user: User = Depends(get_current_user),
                      db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    pr = _get(db, number, user)
    att = db.get(PurchaseAttachment, att_id)
    if att is None or att.request_id != pr.id:
        raise HTTPException(status_code=404, detail="File not found")
    if not (pr.status == PRStatus.DRAFT and user.id == pr.requester_id):
        flash(request, "Attachments can only be removed while the request is a draft.", "error")
        return redirect(f"/abelorder/{pr.number}#attachments")
    ao.attachment_path(att).unlink(missing_ok=True)
    db.delete(att)
    db.commit()
    flash(request, "Attachment removed.")
    return redirect(f"/abelorder/{pr.number}#attachments")


# --- Settings (IT manager / administrator) ------------------------------------------------------------------

@router.get("/admin/abelorder", response_class=HTMLResponse)
def settings_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(database.get_db)):
    # Managers see everything; Procurement sees (and maintains) the vendor list only.
    if not (user.role == Role.MANAGER or user.is_procurement):
        raise Forbidden()
    users = db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name)).all()
    return render(request, "ao_settings.html", {
        "policy": ao.policy(db), "users": users, "money": ao.money, "full_admin": user.role == Role.MANAGER,
        "departments": db.scalars(select(Department).order_by(Department.name)).all(),
        "vendors": db.scalars(select(Vendor).order_by(Vendor.name)).all(),
    }, user=user)


@router.post("/admin/abelorder/policy")
def save_policy(request: Request, procurement_email: str = Form(...), dept_head_threshold: str = Form(...),
                cso_threshold: str = Form(...), cso_user_id: str = Form(""), user: User = Depends(require_manager),
                db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    from decimal import Decimal, InvalidOperation
    try:
        dept, cso = Decimal(dept_head_threshold.replace(",", "")), Decimal(cso_threshold.replace(",", ""))
    except InvalidOperation:
        flash(request, "Thresholds must be numbers.", "error")
        return redirect("/admin/abelorder")
    if dept <= 0 or cso <= dept:
        flash(request, "The CSO threshold must be higher than the department-head threshold.", "error")
        return redirect("/admin/abelorder")
    if not ao._EMAIL_RE.match(procurement_email.strip()):
        flash(request, "Enter a valid procurement email.", "error")
        return redirect("/admin/abelorder")
    ao.set_setting(db, "abelorder.procurement_email", procurement_email.strip().lower())
    ao.set_setting(db, "abelorder.dept_head_threshold", str(dept))
    ao.set_setting(db, "abelorder.cso_threshold", str(cso))
    ao.set_setting(db, "abelorder.cso_user_id", cso_user_id if cso_user_id.isdigit() else "")
    db.commit()
    flash(request, "AbelOrder policy saved.")
    return redirect("/admin/abelorder")


@router.post("/admin/abelorder/departments")
def save_department(request: Request, name: str = Form(...), head_id: str = Form(""), dept_id: str = Form(""),
                    user: User = Depends(require_manager), db: Session = Depends(database.get_db),
                    _: None = Depends(verify_csrf)):
    name = name.strip()
    dept = db.get(Department, int(dept_id)) if dept_id.isdigit() else Department()
    clash = db.scalar(select(Department).where(func.lower(Department.name) == name.lower()))
    if not name or (clash and clash is not dept):
        flash(request, "Enter a unique department name.", "error")
        return redirect("/admin/abelorder#departments")
    dept.name, dept.head = name, db.get(User, int(head_id)) if head_id.isdigit() else None
    db.add(dept)
    db.commit()
    flash(request, f"Saved department {name}.")
    return redirect("/admin/abelorder#departments")


@router.post("/admin/abelorder/people/{user_id:int}")
def save_person(request: Request, user_id: int, supervisor_id: str = Form(""), department_id: str = Form(""),
                is_procurement: bool = Form(False), user: User = Depends(require_manager),
                db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    person = db.get(User, user_id)
    if person is None:
        raise HTTPException(status_code=404, detail="User not found")
    if supervisor_id == str(person.id):
        flash(request, "A person can't be their own supervisor.", "error")
        return redirect("/admin/abelorder#people")
    person.supervisor = db.get(User, int(supervisor_id)) if supervisor_id.isdigit() else None
    person.department_ref = db.get(Department, int(department_id)) if department_id.isdigit() else None
    person.is_procurement = is_procurement
    db.commit()
    flash(request, f"Saved {person.full_name}.")
    return redirect("/admin/abelorder#people")


@router.post("/admin/abelorder/vendors")
def save_vendor(request: Request, name: str = Form(...), contact_name: str = Form(""), contact_email: str = Form(""),
                vendor_id: str = Form(""), user: User = Depends(get_current_user),
                db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    if not (user.is_procurement or user.role == Role.MANAGER):
        raise Forbidden()
    name = name.strip()
    vendor = db.get(Vendor, int(vendor_id)) if vendor_id.isdigit() else Vendor()
    clash = db.scalar(select(Vendor).where(func.lower(Vendor.name) == name.lower()))
    if not name or (clash and clash is not vendor):
        flash(request, "Enter a unique vendor name.", "error")
        return redirect("/admin/abelorder#vendors")
    vendor.name = name
    vendor.contact_name = contact_name.strip() or None
    vendor.contact_email = contact_email.strip().lower() or None
    db.add(vendor)
    db.commit()
    flash(request, f"Saved vendor {name}.")
    return redirect("/admin/abelorder#vendors")

