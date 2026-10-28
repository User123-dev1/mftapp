"""Service notices: announce outages, planned maintenance and service changes to end users.

Notices are emailed to mail distribution lists (the mail server expands them), shown as a
banner in the app while active, and listed on the Service Status page. Follow-up updates,
the resolution and cancellations go to the same recipients.
"""

import re
from datetime import datetime
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import settings
from .emailer import queue_email
from .models import (
    ChangeRequest, ConfigItem, DistributionList, NoticeKind, NoticeSeverity, NoticeStatus, NoticeUpdate,
    ServiceNotice, Ticket, User, utcnow,
)

_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


class NoticeError(ValueError):
    pass


def parse_addresses(raw: str) -> tuple[list[str], list[str]]:
    """Split 'a@x.com; b@y.com, c' into (valid, invalid)."""
    parts = [p.strip().lower() for p in re.split(r"[,;\s]+", raw or "") if p.strip()]
    valid = [p for p in parts if _EMAIL_RE.match(p)]
    return sorted(set(valid)), [p for p in parts if not _EMAIL_RE.match(p)]


def recipients(notice: ServiceNotice) -> list[str]:
    addresses = {dl.email.lower() for dl in notice.lists if dl.active}
    addresses.update(parse_addresses(notice.extra_recipients or "")[0])
    return sorted(addresses)


# --- Create / edit -------------------------------------------------------------------------

def apply(db: Session, notice: ServiceNotice, data: dict, list_ids: list[int], item_ids: list[int]) -> None:
    notice.kind = data["kind"]
    notice.severity = data["severity"]
    notice.title = (data.get("title") or "").strip()
    notice.message = (data.get("message") or "").strip()
    notice.impact = (data.get("impact") or "").strip() or None
    notice.user_action = (data.get("user_action") or "").strip() or None
    notice.start_at, notice.end_at = data.get("start_at"), data.get("end_at")
    notice.show_banner = bool(data.get("show_banner"))
    extra = data.get("extra_recipients") or ""
    valid, invalid = parse_addresses(extra)
    if invalid:
        raise NoticeError("Not valid email addresses: " + ", ".join(invalid))
    notice.extra_recipients = ", ".join(valid) or None
    notice.lists = list(db.scalars(select(DistributionList).where(DistributionList.id.in_(list_ids)))) if list_ids else []
    notice.items = list(db.scalars(select(ConfigItem).where(ConfigItem.id.in_(item_ids)))) if item_ids else []
    if not notice.title or not notice.message:
        raise NoticeError("A title and message are required.")
    if notice.start_at and notice.end_at and notice.end_at <= notice.start_at:
        raise NoticeError("The end time must be after the start time.")


def create(db: Session, author: User, data: dict, list_ids: list[int], item_ids: list[int],
           change: Optional[ChangeRequest] = None, ticket: Optional[Ticket] = None) -> ServiceNotice:
    if not author.is_staff:
        raise NoticeError("Only IT staff can create service notices.")
    notice = ServiceNotice(created_by=author, status=NoticeStatus.DRAFT, change=change, ticket=ticket,
                           kind=data["kind"], title="", message="")
    db.add(notice)
    apply(db, notice, data, list_ids, item_ids)
    db.flush()
    notice.number = f"NTC{100000 + notice.id}"
    return notice


def update_draft(db: Session, notice: ServiceNotice, actor: User, data: dict, list_ids: list[int],
                 item_ids: list[int]) -> None:
    if notice.status != NoticeStatus.DRAFT:
        raise NoticeError("Only drafts can be edited. Post an update instead.")
    if not actor.is_staff:
        raise NoticeError("Only IT staff can edit notices.")
    apply(db, notice, data, list_ids, item_ids)


# --- Sending -------------------------------------------------------------------------------------

def _require_sender(actor: User) -> None:
    if not actor.may_send_notices:
        raise NoticeError("Only IT managers, or staff allowed to send notices, can email distribution lists.")


def _fmt(dt: Optional[datetime]) -> str:
    return dt.strftime("%a %d %b %Y, %H:%M") if dt else ""


def window_text(notice: ServiceNotice) -> str:
    if notice.start_at and notice.end_at:
        return f"{_fmt(notice.start_at)} to {_fmt(notice.end_at)}"
    if notice.start_at:
        return f"From {_fmt(notice.start_at)}"
    if notice.end_at:
        return f"Until {_fmt(notice.end_at)}"
    return ""


def status_url() -> str:
    return f"{settings.base_url.rstrip('/')}/status"


def _body(notice: ServiceNotice, heading: str, extra: str = "") -> str:
    lines = [heading, ""]
    if extra:
        lines += [extra, ""]
    lines += [notice.message, ""]
    window = window_text(notice)
    if window:
        lines.append(f"When:      {window}")
    if notice.items:
        lines.append("Affected:  " + ", ".join(ci.name for ci in notice.items))
    lines.append(f"Severity:  {notice.severity.label}")
    if notice.impact:
        lines += ["", "Impact:", notice.impact]
    if notice.user_action:
        lines += ["", "What you need to do:", notice.user_action]
    lines += ["", f"Current service status: {status_url()}",
              f"Reference: {notice.number}",
              "", "-- ", f"{settings.app_name}", "Please contact the IT Service Desk with any questions."]
    return "\n".join(lines)


def _broadcast(db: Session, notice: ServiceNotice, subject: str, body: str) -> int:
    to = recipients(notice)
    for address in to:
        queue_email(db, address, subject, body, notice.ticket_id)
    return len(to)


def send(db: Session, notice: ServiceNotice, actor: User) -> int:
    _require_sender(actor)
    if notice.status != NoticeStatus.DRAFT:
        raise NoticeError("This notice has already been sent.")
    if not recipients(notice):
        raise NoticeError("Choose at least one distribution list (or add recipient addresses).")
    notice.status, notice.sent_at, notice.sent_by = NoticeStatus.ACTIVE, utcnow(), actor
    notice.updates.append(NoticeUpdate(kind="sent", message="Notice sent", author=actor))
    count = _broadcast(db, notice, f"[{notice.kind.tag}] {notice.title}",
                       _body(notice, f"{notice.kind.label.upper()}: {notice.title}"))
    return count


def post_update(db: Session, notice: ServiceNotice, actor: User, message: str) -> int:
    _require_sender(actor)
    message = (message or "").strip()
    if notice.status != NoticeStatus.ACTIVE:
        raise NoticeError("Updates can only be posted on an active notice.")
    if not message:
        raise NoticeError("Write the update first.")
    notice.updates.append(NoticeUpdate(kind="update", message=message, author=actor))
    n = sum(1 for u in notice.updates if u.kind == "update")
    return _broadcast(db, notice, f"[{notice.kind.tag} - UPDATE {n}] {notice.title}",
                      _body(notice, f"UPDATE {n}: {notice.title}", f"Update ({_fmt(datetime.now())}): {message}"))


def resolve(db: Session, notice: ServiceNotice, actor: User, message: str) -> int:
    _require_sender(actor)
    message = (message or "").strip()
    if notice.status != NoticeStatus.ACTIVE:
        raise NoticeError("Only an active notice can be resolved.")
    if not message:
        raise NoticeError("Say what was resolved / completed.")
    notice.status, notice.closed_at = NoticeStatus.RESOLVED, utcnow()
    notice.updates.append(NoticeUpdate(kind="resolved", message=message, author=actor))
    done = "COMPLETED" if notice.kind in (NoticeKind.MAINTENANCE, NoticeKind.CHANGE) else "RESOLVED"
    return _broadcast(db, notice, f"[{done}] {notice.title}",
                      _body(notice, f"{done}: {notice.title}", message))


def cancel(db: Session, notice: ServiceNotice, actor: User, message: str) -> int:
    message = (message or "").strip()
    if notice.status == NoticeStatus.DRAFT:
        if not actor.is_staff:
            raise NoticeError("Only IT staff can discard drafts.")
        notice.status, notice.closed_at = NoticeStatus.CANCELLED, utcnow()
        notice.updates.append(NoticeUpdate(kind="cancelled", message=message or "Draft discarded", author=actor))
        return 0
    _require_sender(actor)
    if notice.status != NoticeStatus.ACTIVE:
        raise NoticeError("This notice is already closed.")
    if not message:
        raise NoticeError("Say why it's cancelled (e.g. maintenance postponed).")
    notice.status, notice.closed_at = NoticeStatus.CANCELLED, utcnow()
    notice.updates.append(NoticeUpdate(kind="cancelled", message=message, author=actor))
    return _broadcast(db, notice, f"[CANCELLED] {notice.title}",
                      _body(notice, f"CANCELLED: {notice.title}", message))


# --- What end users see ---------------------------------------------------------------------------

def banner_notices(db: Session) -> list[ServiceNotice]:
    """Active notices to show on every page (hidden once a scheduled window has ended)."""
    now = datetime.now()
    rows = db.scalars(select(ServiceNotice).where(
        ServiceNotice.status == NoticeStatus.ACTIVE, ServiceNotice.show_banner.is_(True),
        or_(ServiceNotice.end_at.is_(None), ServiceNotice.end_at > now),
    )).all()
    order = {NoticeSeverity.CRITICAL: 0, NoticeSeverity.WARNING: 1, NoticeSeverity.INFO: 2}
    return sorted(rows, key=lambda n: (order[n.severity], n.start_at or n.sent_at))


def defaults_from_change(change: ChangeRequest) -> dict:
    items = ", ".join(ci.name for ci in change.items)
    return {
        "kind": "maintenance", "severity": "warning", "title": change.title,
        "message": f"IT will carry out planned work: {change.title}." + (f" Affected: {items}." if items else ""),
        "impact": "Services listed may be unavailable or interrupted during the window.",
        "start_at": change.planned_start.strftime("%Y-%m-%dT%H:%M") if change.planned_start else "",
        "end_at": change.planned_end.strftime("%Y-%m-%dT%H:%M") if change.planned_end else "",
        "item_ids": [str(ci.id) for ci in change.items],
    }


def defaults_from_ticket(ticket: Ticket) -> dict:
    return {
        "kind": "outage", "severity": "critical", "title": f"{ticket.ci.name} unavailable" if ticket.ci else ticket.title,
        "message": "We are aware of an issue affecting this service and are working to restore it.",
        "user_action": "No need to log a new ticket - we'll send an update when it's fixed.",
        "item_ids": [str(ticket.ci_id)] if ticket.ci_id else [],
    }
