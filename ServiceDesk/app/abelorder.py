"""AbelOrder: employee purchase-order requests with a signature (approval) chain.

Routing by total:  always Direct Supervisor;  + Department Head if total > $10,000;
                   + CSO if total > $50,000  (thresholds and CSO configurable).
Approvals are sequential. Nobody approves their own request: a step whose approver is the
requester escalates to the next level (supervisor -> department head -> CSO). If one person
holds two consecutive roles, their single approval signs both.
When every step is approved, the signed PDF (plus supplier quotes) is emailed to Procurement
and a copy to the requester. Procurement then records the PO number.
"""

import mimetypes
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .emailer import queue_email
from .models import (
    AppSetting, ApprovalRole, Department, PRStatus, PurchaseActivity, PurchaseApproval, PurchaseAttachment,
    PurchaseItem, PurchaseRequest, Role, StepState, Urgency, User, Vendor, utcnow,
)

CENT = Decimal("0.01")
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt",
                      ".msg", ".eml", ".zip"}
UOM_CHOICES = ["EA", "BX", "CS", "PK", "PR", "SET", "LOT", "KIT", "ROLL", "FT", "M", "LB", "KG", "GAL", "L", "HR",
               "DAY", "MO", "YR", "LIC"]
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class OrderError(ValueError):
    """Not allowed / invalid; message is safe to show."""


# --- Settings ----------------------------------------------------------------------------

_DEFAULTS = {
    "abelorder.procurement_email": "abztprocurement@abelzeta.com",
    "abelorder.dept_head_threshold": "10000",
    "abelorder.cso_threshold": "50000",
    "abelorder.cso_user_id": "",
}


def get_setting(db: Session, key: str) -> str:
    row = db.get(AppSetting, key)
    return row.value if row else _DEFAULTS[key]


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


@dataclass
class OrderPolicy:
    procurement_email: str
    dept_head_threshold: Decimal
    cso_threshold: Decimal
    cso: Optional[User]


def policy(db: Session) -> OrderPolicy:
    cso_id = get_setting(db, "abelorder.cso_user_id")
    return OrderPolicy(
        procurement_email=get_setting(db, "abelorder.procurement_email"),
        dept_head_threshold=Decimal(get_setting(db, "abelorder.dept_head_threshold")),
        cso_threshold=Decimal(get_setting(db, "abelorder.cso_threshold")),
        cso=db.get(User, int(cso_id)) if cso_id.isdigit() else None,
    )


def money(value) -> str:
    """Format a Decimal/str as $12,345.67."""
    return f"${Decimal(value).quantize(CENT, ROUND_HALF_UP):,.2f}"


def unit_money(value) -> str:
    """Unit prices keep up to 4 decimals ($0.0125), but always show at least 2."""
    d = Decimal(value)
    return f"${d.quantize(CENT):,.2f}" if d == d.quantize(CENT) else f"${d.normalize():,f}"


def qty_text(value) -> str:
    return f"{Decimal(value).normalize():f}"


# --- Parsing the form ------------------------------------------------------------------------

@dataclass
class ItemInput:
    quantity: Decimal
    uom: str
    description: str
    catalog_number: str
    unit_cost: Decimal

    @property
    def line_total(self) -> Decimal:
        return (self.quantity * self.unit_cost).quantize(CENT, ROUND_HALF_UP)


@dataclass
class OrderInput:
    justification: str = ""
    urgency: Urgency = Urgency.NORMAL
    needed_by: Optional[date] = None
    project_number: str = ""
    department_id: Optional[int] = None
    supervisor_id: Optional[int] = None
    vendor_is_new: bool = False
    vendor_id: Optional[int] = None
    vendor_name: str = ""
    vendor_contact_name: str = ""
    vendor_contact_email: str = ""
    notes: str = ""
    items: list[ItemInput] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((i.line_total for i in self.items), Decimal("0.00"))


def _dec(raw: str, what: str, line: int, errors: list[str], max_places: int) -> Optional[Decimal]:
    text = (raw or "").replace(",", "").replace("$", "").strip()
    try:
        value = Decimal(text)
    except InvalidOperation:
        errors.append(f"Line {line}: {what} '{raw}' is not a number.")
        return None
    if not value.is_finite() or value <= 0:
        errors.append(f"Line {line}: {what} must be greater than zero.")
        return None
    if -value.as_tuple().exponent > max_places:
        errors.append(f"Line {line}: {what} allows at most {max_places} decimal places.")
        return None
    if value >= Decimal("1e10"):
        errors.append(f"Line {line}: {what} is too large.")
        return None
    return value


def parse_form(form) -> OrderInput:
    """Read and validate the request form. Totals are always computed here, never trusted from the browser."""
    data = OrderInput()
    e = data.errors
    g = lambda k: str(form.get(k, "") or "").strip()  # noqa: E731

    data.justification = g("justification")
    try:
        data.urgency = Urgency(g("urgency") or "normal")
    except ValueError:
        e.append("Choose a valid urgency.")
    if g("needed_by"):
        try:
            data.needed_by = date.fromisoformat(g("needed_by"))
        except ValueError:
            e.append("Needed-by date must be a valid date.")
    data.project_number = g("project_number")
    data.department_id = int(g("department_id")) if g("department_id").isdigit() else None
    data.supervisor_id = int(g("supervisor_id")) if g("supervisor_id").isdigit() else None
    data.vendor_is_new = g("vendor_mode") == "new"
    data.vendor_id = None if data.vendor_is_new else (int(g("vendor_id")) if g("vendor_id").isdigit() else None)
    data.vendor_name = g("vendor_name")
    data.vendor_contact_name = g("vendor_contact_name")
    data.vendor_contact_email = g("vendor_contact_email").lower()
    data.notes = g("notes")

    cols = [form.getlist(k) for k in ("item_qty", "item_uom", "item_desc", "item_catalog", "item_unit_cost")]
    for n, (qty, uom, desc, cat, cost) in enumerate(zip(*cols), start=1):
        qty, uom, desc, cat, cost = (str(x or "").strip() for x in (qty, uom, desc, cat, cost))
        if not any((qty, desc, cat, cost)):
            continue  # blank row
        if not desc:
            e.append(f"Line {n}: description is required.")
        if not uom:
            e.append(f"Line {n}: unit of measure is required.")
        q = _dec(qty, "quantity", n, e, 3)
        c = _dec(cost, "cost per UOM", n, e, 4)
        if q is not None and c is not None and desc and uom:
            data.items.append(ItemInput(q, uom.upper()[:20], desc, cat, c))
    return data


def input_from_request(pr: PurchaseRequest) -> OrderInput:
    """Rebuild the validated input from a saved draft (for 'Submit' on the detail page)."""
    return OrderInput(
        justification=pr.justification, urgency=pr.urgency, needed_by=pr.needed_by,
        project_number=pr.project_number, department_id=pr.department_id, supervisor_id=pr.supervisor_id,
        vendor_is_new=pr.vendor_is_new, vendor_id=pr.vendor_id, vendor_name=pr.vendor_name,
        vendor_contact_name=pr.vendor_contact_name, vendor_contact_email=pr.vendor_contact_email,
        notes=pr.notes or "",
        items=[ItemInput(Decimal(i.quantity), i.uom, i.description, i.catalog_number or "", Decimal(i.unit_cost))
               for i in pr.items],
    )


def validate_for_submit(db: Session, data: OrderInput) -> list[str]:
    e = list(data.errors)
    if not data.justification:
        e.append("Reason / justification is required.")
    if not data.project_number:
        e.append("Project or work order number is required (enter N/A if none).")
    if not data.vendor_is_new and (data.vendor_id is None or db.get(Vendor, data.vendor_id) is None):
        e.append("Choose an existing vendor, or select 'New vendor' and enter its name.")
    if data.vendor_is_new and not data.vendor_name:
        e.append("Vendor name is required.")
    if not data.vendor_contact_name:
        e.append("Vendor contact name is required.")
    if not _EMAIL_RE.match(data.vendor_contact_email):
        e.append("A valid vendor contact email is required.")
    if not data.items:
        e.append("Add at least one item.")
    if data.vendor_is_new and data.vendor_name and db.scalar(
            select(Vendor).where(func.lower(Vendor.name) == data.vendor_name.lower())):
        e.append(f"{data.vendor_name} is already an existing vendor - choose it from the list.")
    return e


# --- Create / update ----------------------------------------------------------------------------

def _log(db: Session, pr: PurchaseRequest, actor: Optional[User], message: str) -> None:
    db.add(PurchaseActivity(request=pr, actor=actor, message=message[:1000]))


def apply_input(db: Session, pr: PurchaseRequest, requester: User, data: OrderInput) -> None:
    pr.justification = data.justification
    pr.urgency = data.urgency
    pr.needed_by = data.needed_by
    pr.project_number = data.project_number
    pr.department = db.get(Department, data.department_id) if data.department_id else None
    # An administrator-assigned supervisor can't be overridden on the form.
    if requester.supervisor is not None:
        pr.supervisor, pr.supervisor_from_profile = requester.supervisor, True
    else:
        pr.supervisor = db.get(User, data.supervisor_id) if data.supervisor_id else None
        pr.supervisor_from_profile = False
    pr.vendor_is_new = data.vendor_is_new
    vendor = db.get(Vendor, data.vendor_id) if data.vendor_id else None
    pr.vendor = vendor
    pr.vendor_name = vendor.name if vendor else data.vendor_name
    pr.vendor_contact_name = data.vendor_contact_name
    pr.vendor_contact_email = data.vendor_contact_email
    pr.notes = data.notes or None
    pr.items = [
        PurchaseItem(line_no=n, quantity=str(i.quantity), uom=i.uom, description=i.description,
                     catalog_number=i.catalog_number or None, unit_cost=str(i.unit_cost),
                     line_total=str(i.line_total))
        for n, i in enumerate(data.items, start=1)
    ]
    total = data.total.quantize(CENT, ROUND_HALF_UP)
    pr.total, pr.total_cents = str(total), int(total * 100)


def create_request(db: Session, requester: User, data: OrderInput) -> PurchaseRequest:
    pr = PurchaseRequest(requester=requester, requester_name=requester.full_name, request_date=date.today(),
                         status=PRStatus.DRAFT, justification="", project_number="", vendor_name="",
                         vendor_contact_name="", vendor_contact_email="")
    db.add(pr)
    apply_input(db, pr, requester, data)
    db.flush()
    pr.number = f"AO-{pr.request_date.year}-{pr.id:05d}"
    _log(db, pr, requester, "Request created")
    return pr


def update_request(db: Session, pr: PurchaseRequest, actor: User, data: OrderInput) -> None:
    if pr.status != PRStatus.DRAFT:
        raise OrderError("Only draft requests can be edited.")
    if actor.id != pr.requester_id:
        raise OrderError("Only the requester can edit this request.")
    apply_input(db, pr, actor, data)
    _log(db, pr, actor, "Request updated")


# --- Routing -------------------------------------------------------------------------------------

def build_route(db: Session, pr: PurchaseRequest) -> list[tuple[ApprovalRole, User, Optional[str]]]:
    """The signature chain for this request: [(role, approver, note)]."""
    pol = policy(db)
    total = Decimal(pr.total)
    roles = [ApprovalRole.SUPERVISOR]
    if total > pol.dept_head_threshold:
        roles.append(ApprovalRole.DEPT_HEAD)
    if total > pol.cso_threshold:
        roles.append(ApprovalRole.CSO)

    def holder(role: ApprovalRole) -> Optional[User]:
        if role == ApprovalRole.SUPERVISOR:
            return pr.supervisor
        if role == ApprovalRole.DEPT_HEAD:
            return pr.department.head if pr.department else None
        return pol.cso

    def missing(role: ApprovalRole) -> str:
        if role == ApprovalRole.SUPERVISOR:
            return "Choose your direct supervisor."
        if role == ApprovalRole.DEPT_HEAD:
            return ("Orders over " + money(pol.dept_head_threshold) + " need a department head: choose a department "
                    "that has a head (ask an administrator to set one).")
        return "Orders over " + money(pol.cso_threshold) + " need CSO approval, but no CSO is configured."

    route: list[tuple[ApprovalRole, User, Optional[str]]] = []
    order = [ApprovalRole.SUPERVISOR, ApprovalRole.DEPT_HEAD, ApprovalRole.CSO]
    for role in roles:
        person, note, current = holder(role), None, role
        # Escalate when the approver is the requester (nobody signs their own request).
        while person is not None and person.id == pr.requester_id:
            nxt = order.index(current) + 1
            if nxt >= len(order):
                raise OrderError("You hold the highest approval role for this request, so it can't be self-approved."
                                 " Ask an administrator to assign a different CSO approver.")
            note = f"{current.label} is the requester - escalated to {order[nxt].label}"
            current = order[nxt]
            person = holder(current)
        if person is None:
            raise OrderError(missing(current))
        if not person.is_active:
            raise OrderError(f"{person.full_name} ({current.label}) is no longer active - ask an administrator.")
        route.append((role, person, note))
    return route


# --- Workflow actions ------------------------------------------------------------------------------

def submit(db: Session, pr: PurchaseRequest, actor: User) -> None:
    if pr.status != PRStatus.DRAFT:
        raise OrderError("Only draft requests can be submitted.")
    if actor.id != pr.requester_id:
        raise OrderError("Only the requester can submit this request.")
    route = build_route(db, pr)
    pr.approvals = [
        PurchaseApproval(step_order=n, role=role, approver=person, note=note,
                         state=StepState.PENDING if n == 1 else StepState.WAITING)
        for n, (role, person, note) in enumerate(route, start=1)
    ]
    pr.status = PRStatus.PENDING_APPROVAL
    pr.submitted_at = utcnow()
    pr.request_date = date.today()
    db.flush()  # populate approver_id etc. so later checks in this transaction see them
    _log(db, pr, actor, f"Submitted for approval ({money(pr.total)}): "
                        + " -> ".join(f"{r.label}: {p.full_name}" for r, p, _ in route))
    _notify_approver(db, pr)
    queue_email(db, pr.requester.email, f"[{pr.number}] Purchase request submitted",
                f"Hello {pr.requester.full_name},\n\nYour purchase request was submitted for approval.\n"
                f"Approval route: " + " -> ".join(f"{r.label} ({p.full_name})" for r, p, _ in route)
                + f"\n\n{summary(pr)}{_signature()}")


def can_view(user: User, pr: PurchaseRequest) -> bool:
    return (
        user.id == pr.requester_id
        or user.role == Role.MANAGER
        or user.is_procurement
        or any(_approver_id(a) == user.id for a in pr.approvals)  # anyone on the signature route
    )


def _approver_id(step: PurchaseApproval) -> int:
    # Use the related object: approver_id is only filled in when the session flushes.
    return step.approver.id if step.approver is not None else step.approver_id


def can_decide(user: User, pr: PurchaseRequest) -> bool:
    step = pr.current_step
    return pr.status == PRStatus.PENDING_APPROVAL and step is not None and _approver_id(step) == user.id


def decide(db: Session, pr: PurchaseRequest, approver: User, approve: bool, comment: str) -> None:
    comment = (comment or "").strip()
    if not can_decide(approver, pr):
        raise OrderError("This request is not waiting for your approval.")
    if not approve and not comment:
        raise OrderError("Please give a reason for rejecting.")
    step = pr.current_step
    now = utcnow()
    step.state, step.comment, step.decided_at = (StepState.APPROVED if approve else StepState.REJECTED,
                                                 comment or None, now)
    if not approve:
        pr.status = PRStatus.REJECTED
        _log(db, pr, approver, f"Rejected by {step.role.label}: {comment}")
        queue_email(db, pr.requester.email, f"[{pr.number}] Purchase request rejected",
                    f"Hello {pr.requester.full_name},\n\n{approver.full_name} ({step.role.label}) rejected your "
                    f"purchase request:\n\n{comment}\n\nYou can revise and resubmit it.\n\n{summary(pr)}{_signature()}")
        return

    _log(db, pr, approver, f"Approved as {step.role.label}" + (f": {comment}" if comment else ""))
    # Same person holding the next role(s) signs those too.
    for nxt in pr.approvals:
        if nxt.state == StepState.WAITING and _approver_id(nxt) == approver.id:
            nxt.state, nxt.decided_at = StepState.APPROVED, now
            nxt.comment = f"Signed together with the {step.role.label} approval."
            _log(db, pr, approver, f"Also approved as {nxt.role.label}")
        elif nxt.state == StepState.WAITING:
            nxt.state = StepState.PENDING
            break
    if pr.current_step is None:
        _fully_approved(db, pr)
    else:
        _notify_approver(db, pr)


def _fully_approved(db: Session, pr: PurchaseRequest) -> None:
    from .abelorder_pdf import write_pdf

    pr.status = PRStatus.APPROVED
    pr.approved_at = utcnow()
    _log(db, pr, None, "All signatures complete - sent to Procurement")
    db.flush()
    pdf = write_pdf(pr, "approved")
    files = [pdf] + [attachment_file(a) for a in pr.attachments]
    pol = policy(db)
    queue_email(db, pol.procurement_email, f"[{pr.number}] Approved purchase request - {pr.vendor_name} - "
                f"{money(pr.total)}",
                f"Hello Procurement,\n\nThe attached purchase request has been fully approved and is ready for a PO."
                f"\n\n{summary(pr)}\n\nSignatures:\n{signatures_text(pr)}\n\n"
                f"Record the PO number here: {request_url(pr)}{_signature()}", attachments=files)
    queue_email(db, pr.requester.email, f"[{pr.number}] Purchase request approved",
                f"Hello {pr.requester.full_name},\n\nYour purchase request has all required signatures and was sent "
                f"to Procurement ({pol.procurement_email}). A copy of the signed request is attached. You'll get "
                f"another email when the PO number is issued.\n\n{summary(pr)}{_signature()}", attachments=[pdf])
    if pr.vendor_is_new and not db.scalar(select(Vendor).where(func.lower(Vendor.name) == pr.vendor_name.lower())):
        pr.vendor = Vendor(name=pr.vendor_name, contact_name=pr.vendor_contact_name,
                           contact_email=pr.vendor_contact_email)
        _log(db, pr, None, f"New vendor {pr.vendor_name} added to the vendor list")


def issue_po(db: Session, pr: PurchaseRequest, actor: User, po_number: str) -> None:
    po_number = (po_number or "").strip()
    if not (actor.is_procurement or actor.role == Role.MANAGER):
        raise OrderError("Only Procurement can record PO numbers.")
    if pr.status not in (PRStatus.APPROVED, PRStatus.PO_ISSUED):
        raise OrderError("A PO number can only be added after the request is fully approved.")
    if not po_number:
        raise OrderError("Enter the PO number.")
    clash = db.scalar(select(PurchaseRequest).where(PurchaseRequest.po_number == po_number,
                                                    PurchaseRequest.id != pr.id))
    if clash:
        raise OrderError(f"PO {po_number} is already used on {clash.number}.")
    previous = pr.po_number
    pr.po_number, pr.status = po_number, PRStatus.PO_ISSUED
    pr.po_issued_at, pr.po_issued_by = utcnow(), actor
    _log(db, pr, actor, f"PO number {'changed from ' + previous + ' to ' if previous else ''}{po_number}")
    db.flush()
    from .abelorder_pdf import write_pdf
    pdf = write_pdf(pr, "po")
    queue_email(db, pr.requester.email, f"[{pr.number}] PO {po_number} issued",
                f"Hello {pr.requester.full_name},\n\nProcurement issued PO number {po_number} for your request. "
                f"The final copy is attached.\n\n{summary(pr)}{_signature()}", attachments=[pdf])


def revise(db: Session, pr: PurchaseRequest, actor: User) -> None:
    if pr.status != PRStatus.REJECTED:
        raise OrderError("Only rejected requests can be revised.")
    if actor.id != pr.requester_id:
        raise OrderError("Only the requester can revise this request.")
    pr.status = PRStatus.DRAFT
    pr.approvals = []
    _log(db, pr, actor, "Reopened as draft for revision (signatures are collected again on resubmit)")


def cancel(db: Session, pr: PurchaseRequest, actor: User, reason: str) -> None:
    reason = (reason or "").strip()
    if pr.status not in (PRStatus.DRAFT, PRStatus.PENDING_APPROVAL, PRStatus.REJECTED, PRStatus.APPROVED):
        raise OrderError("This request can no longer be cancelled.")
    allowed = actor.id == pr.requester_id or actor.is_procurement or actor.role == Role.MANAGER
    if not allowed:
        raise OrderError("You can't cancel this request.")
    if not reason:
        raise OrderError("Please give a reason for cancelling.")
    step = pr.current_step
    was_approved = pr.status == PRStatus.APPROVED
    pr.status = PRStatus.CANCELLED
    _log(db, pr, actor, f"Cancelled: {reason}")
    if step is not None:
        queue_email(db, step.approver.email, f"[{pr.number}] Purchase request cancelled - no action needed",
                    f"Hello {step.approver.full_name},\n\n{actor.full_name} cancelled this request: {reason}"
                    f"\n\n{summary(pr)}{_signature()}")
    if was_approved:
        queue_email(db, policy(db).procurement_email, f"[{pr.number}] CANCELLED - do not issue a PO",
                    f"Hello Procurement,\n\n{actor.full_name} cancelled this approved request: {reason}"
                    f"\n\n{summary(pr)}{_signature()}")


# --- Attachments ----------------------------------------------------------------------------------

def upload_root() -> Path:
    root = Path(settings.upload_dir) / "abelorder"
    root.mkdir(parents=True, exist_ok=True)
    return root


def add_attachment(db: Session, pr: PurchaseRequest, user: User, filename: str, data: bytes) -> PurchaseAttachment:
    name = Path(filename or "").name.strip() or "quote"
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise OrderError(f"{name}: file type not allowed (use PDF, image, Word, Excel, email or ZIP).")
    if len(data) > MAX_UPLOAD_BYTES:
        raise OrderError(f"{name}: larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if not data:
        raise OrderError(f"{name}: the file is empty.")
    stored = f"{uuid.uuid4().hex}{ext}"
    (upload_root() / stored).write_bytes(data)
    db.info.setdefault("ao_new_files", []).append(upload_root() / stored)  # removed again on rollback
    att = PurchaseAttachment(request=pr, filename=name[:255], stored_name=stored, size=len(data), uploaded_by=user,
                             content_type=mimetypes.guess_type(name)[0] or "application/octet-stream")
    db.add(att)
    _log(db, pr, user, f"Attached {name}")
    return att


def attachment_path(att: PurchaseAttachment) -> Path:
    return upload_root() / att.stored_name


def attachment_file(att: PurchaseAttachment) -> dict:
    return {"path": str(attachment_path(att)), "filename": att.filename, "content_type": att.content_type}


def can_attach(user: User, pr: PurchaseRequest) -> bool:
    if user.is_procurement or user.role == Role.MANAGER:
        return pr.status != PRStatus.CANCELLED
    return user.id == pr.requester_id and pr.status in (PRStatus.DRAFT, PRStatus.PENDING_APPROVAL, PRStatus.REJECTED)


# --- Queues ------------------------------------------------------------------------------------------

def pending_for(db: Session, user: User) -> list[PurchaseRequest]:
    return list(db.scalars(
        select(PurchaseRequest).join(PurchaseApproval)
        .where(PurchaseRequest.status == PRStatus.PENDING_APPROVAL, PurchaseApproval.approver_id == user.id,
               PurchaseApproval.state == StepState.PENDING)
        .order_by(PurchaseRequest.submitted_at)
    ))


# --- Text helpers ---------------------------------------------------------------------------------------

def request_url(pr: PurchaseRequest) -> str:
    return f"{settings.base_url.rstrip('/')}/abelorder/{pr.number}"


def _signature() -> str:
    return f"\n\n-- \nAbelOrder ({settings.app_name})\nThis is an automated message."


def summary(pr: PurchaseRequest) -> str:
    lines = [
        f"Request:      {pr.number}",
        f"Requester:    {pr.requester_name}" + (f" ({pr.department.name})" if pr.department else ""),
        f"Date:         {pr.request_date.isoformat()}",
        f"Urgency:      {pr.urgency.label}" + (f", needed by {pr.needed_by.isoformat()}" if pr.needed_by else ""),
        f"Project / WO: {pr.project_number}",
        f"Vendor:       {pr.vendor_name}{' (NEW VENDOR)' if pr.vendor_is_new else ''} - {pr.vendor_contact_name} "
        f"<{pr.vendor_contact_email}>",
    ]
    if pr.po_number:
        lines.append(f"PO number:    {pr.po_number}")
    lines.append("Items:")
    for i in pr.items:
        cat = f" [{i.catalog_number}]" if i.catalog_number else ""
        lines.append(f"  {qty_text(i.quantity)} {i.uom} x {i.description}{cat} @ {unit_money(i.unit_cost)} = "
                     f"{money(i.line_total)}")
    lines += [f"TOTAL:        {money(pr.total)}", f"Justification: {pr.justification}"]
    if pr.notes:
        lines.append(f"Notes:        {pr.notes}")
    lines.append(f"Link:         {request_url(pr)}")
    return "\n".join(lines)


def signatures_text(pr: PurchaseRequest) -> str:
    return "\n".join(
        f"  {a.role.label}: {a.approver.full_name} - {a.state.label}"
        + (f" {a.decided_at.strftime('%Y-%m-%d %H:%M')} UTC" if a.decided_at else "")
        for a in pr.approvals
    )


def _notify_approver(db: Session, pr: PurchaseRequest) -> None:
    step = pr.current_step
    if step is None:
        return
    queue_email(db, step.approver.email, f"[{pr.number}] Approval needed: {money(pr.total)} - {pr.vendor_name}",
                f"Hello {step.approver.full_name},\n\nA purchase request needs your approval as "
                f"{step.role.label}.\nApprove or reject it here: {request_url(pr)}\n\n{summary(pr)}{_signature()}")


def form_items(pr: Optional[PurchaseRequest]) -> list[dict]:
    """Items for pre-filling the form's line-item grid."""
    if pr is None:
        return []
    return [{"qty": qty_text(i.quantity), "uom": i.uom, "desc": i.description,
             "catalog": i.catalog_number or "", "cost": f"{Decimal(i.unit_cost).normalize():f}"}
            for i in pr.items]
