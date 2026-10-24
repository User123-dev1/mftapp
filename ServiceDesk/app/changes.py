"""Change management: policy, approval workflow, impact analysis and scheduling conflicts.

Lifecycle:  Draft -> Pending approval -> Approved -> In progress -> Completed / Failed
            (Rejected -> revise -> Draft; Cancelled from any state before implementation)
Standard changes are pre-approved; Normal changes need 1 approval (2 for high risk, configurable);
Emergency changes need 1. Nobody may approve a change they requested.
"""

import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .emailer import queue_email
from .models import (
    AppSetting, Category, ChangeActivity, ChangeApproval, ChangeRequest, ChangeState, ChangeType, CIRelationship,
    ConfigItem, Level, Role, Status, Ticket, User, utcnow,
)


class ChangeError(ValueError):
    """A change action not allowed in the current state or for this user."""


# --- Settings / policy --------------------------------------------------------------

_DEFAULTS = {
    "change.categories": json.dumps([Category.FIREWALL.value]),
    "change.approvals_normal": "1",
    "change.approvals_high_risk": "2",
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
class ChangePolicy:
    categories: set[Category]
    approvals_normal: int
    approvals_high_risk: int


def policy(db: Session) -> ChangePolicy:
    cats = set()
    for v in json.loads(get_setting(db, "change.categories")):
        try:
            cats.add(Category(v))
        except ValueError:
            pass
    return ChangePolicy(
        categories=cats,
        approvals_normal=max(1, int(get_setting(db, "change.approvals_normal"))),
        approvals_high_risk=max(1, int(get_setting(db, "change.approvals_high_risk"))),
    )


def requires_change(db: Session, ticket: Ticket) -> Optional[str]:
    """Why this ticket needs an approved change before it can be resolved, or None."""
    if ticket.category in policy(db).categories:
        return f"{ticket.category.label.split(' (')[0]} tickets require an approved change."
    if ticket.ci is not None and ticket.ci.change_controlled:
        return f"{ticket.ci.display_name} is change-controlled."
    return None


def change_is_mandatory(db: Session, ticket: Ticket) -> bool:
    """Request-type policy is a hard rule. For a change-controlled item the agent may instead
    record why no change to the item was needed (e.g. a user-side fix)."""
    return ticket.category in policy(db).categories


def has_completed_change(ticket: Ticket) -> bool:
    return any(c.state == ChangeState.COMPLETED for c in ticket.changes)


def required_approvals(db: Session, change: ChangeRequest) -> int:
    if change.change_type == ChangeType.STANDARD:
        return 0
    if change.change_type == ChangeType.EMERGENCY:
        return 1
    p = policy(db)
    return p.approvals_high_risk if change.risk == Level.HIGH else p.approvals_normal


# --- Helpers --------------------------------------------------------------------------

def _log(db: Session, change: ChangeRequest, actor: Optional[User], message: str) -> None:
    db.add(ChangeActivity(change=change, actor=actor, message=message[:1000]))


def change_url(change: ChangeRequest) -> str:
    return f"{settings.base_url.rstrip('/')}/changes/{change.number}"


def _fmt(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "not scheduled"


def _summary(change: ChangeRequest) -> str:
    items = ", ".join(ci.display_name for ci in change.items) or "none"
    ticket = f"Ticket:      {change.ticket.number}\n" if change.ticket else ""
    return (
        f"Change:      {change.number}\n"
        f"Title:       {change.title}\n"
        f"Type:        {change.change_type.label}\n"
        f"Risk/Impact: {change.risk.label} / {change.impact.label}\n"
        f"Window:      {_fmt(change.planned_start)} -> {_fmt(change.planned_end)}\n"
        f"Items:       {items}\n"
        f"{ticket}"
        f"Requested by {change.requested_by.full_name}\n"
        f"Link:        {change_url(change)}"
    )


def _email(db: Session, user: Optional[User], subject: str, intro: str, change: ChangeRequest) -> None:
    if user is None or not user.is_active:
        return
    from .services import _signature
    queue_email(db, user.email, f"[{change.number}] {subject}",
                f"Hello {user.full_name},\n\n{intro}\n\n{_summary(change)}{_signature()}",
                change.ticket_id)


def approvers(db: Session, change: ChangeRequest) -> list[User]:
    """Everyone allowed to vote on this change (never its requester)."""
    users = db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name))
    return [u for u in users if u.can_approve_changes and u.id != change.requested_by_id]


def can_edit(user: User, change: ChangeRequest) -> bool:
    return user.role == Role.MANAGER or user.id in (change.requested_by_id, change.implementer_id)


def can_vote(user: User, change: ChangeRequest) -> bool:
    return (
        change.state == ChangeState.PENDING_APPROVAL
        and user.can_approve_changes
        and user.id != change.requested_by_id
        and not any(a.approver_id == user.id and a.current for a in change.approvals)
    )


def pending_for(db: Session, user: User) -> list[ChangeRequest]:
    if not user.can_approve_changes:
        return []
    pending = db.scalars(select(ChangeRequest).where(ChangeRequest.state == ChangeState.PENDING_APPROVAL)
                         .order_by(ChangeRequest.planned_start)).all()
    return [c for c in pending if can_vote(user, c)]


def _ticket_back_to_work(db: Session, change: ChangeRequest, actor: Optional[User], why: str) -> None:
    from .services import _log as ticket_log
    ticket = change.ticket
    if ticket is not None and ticket.status == Status.PENDING_CHANGE:
        ticket.status = Status.IN_PROGRESS if ticket.assignee else Status.NEW
        ticket_log(db, ticket, actor, f"{change.number} {why}; ticket back to {ticket.status.label}")


# --- Actions ------------------------------------------------------------------------------

_TEXT_FIELDS = ("title", "description", "justification", "implementation_plan", "backout_plan", "test_plan")


def apply_fields(db: Session, change: ChangeRequest, data: dict, items: list[ConfigItem],
                 implementer: Optional[User]) -> None:
    for f in _TEXT_FIELDS:
        if f in data:
            value = (data.get(f) or "").strip()
            setattr(change, f, value or (None if f not in ("title", "description") else ""))
    change.change_type = data.get("change_type", change.change_type)
    change.risk = data.get("risk", change.risk)
    change.impact = data.get("impact", change.impact)
    change.planned_start = data.get("planned_start")
    change.planned_end = data.get("planned_end")
    change.items = list(items)
    if implementer is not None and not implementer.is_staff:
        raise ChangeError("The implementer must be IT staff.")
    change.implementer = implementer
    if not change.title or not change.description:
        raise ChangeError("Title and description are required.")
    if change.planned_start and change.planned_end and change.planned_end <= change.planned_start:
        raise ChangeError("The planned end must be after the planned start.")


def create_change(db: Session, requester: User, data: dict, items: list[ConfigItem],
                  implementer: Optional[User], ticket: Optional[Ticket] = None) -> ChangeRequest:
    if not requester.is_staff:
        raise ChangeError("Only IT staff can raise change requests.")
    change = ChangeRequest(requested_by=requester, ticket=ticket, state=ChangeState.DRAFT, title="", description="")
    db.add(change)
    apply_fields(db, change, data, items, implementer)
    db.flush()
    change.number = f"CHG{1000000 + change.id}"
    _log(db, change, requester, "Change created" + (f" from {ticket.number}" if ticket else ""))
    if ticket is not None:
        from .services import _log as ticket_log
        ticket_log(db, ticket, requester, f"Change {change.number} raised")
    return change


def update_change(db: Session, change: ChangeRequest, actor: User, data: dict, items: list[ConfigItem],
                  implementer: Optional[User]) -> None:
    if change.state != ChangeState.DRAFT:
        raise ChangeError("Only draft changes can be edited. Revise a rejected change to edit it.")
    if not can_edit(actor, change):
        raise ChangeError("You can't edit this change.")
    apply_fields(db, change, data, items, implementer)
    _log(db, change, actor, "Change updated")


def submit(db: Session, change: ChangeRequest, actor: User) -> None:
    if change.state != ChangeState.DRAFT:
        raise ChangeError("Only draft changes can be submitted.")
    if not can_edit(actor, change):
        raise ChangeError("You can't submit this change.")
    missing = []
    if not change.planned_start or not change.planned_end:
        missing.append("planned start and end")
    if change.change_type != ChangeType.STANDARD:
        if not change.implementation_plan:
            missing.append("implementation plan")
        if not change.backout_plan:
            missing.append("backout plan")
    if not change.items:
        missing.append("at least one affected item")
    if missing:
        raise ChangeError("Before submitting, add: " + ", ".join(missing) + ".")

    for a in change.approvals:
        a.current = False
    change.submitted_at = utcnow()
    change.approvals_required = required_approvals(db, change)

    if change.ticket is not None and change.ticket.status.is_open:
        from .services import _log as ticket_log
        change.ticket.status = Status.PENDING_CHANGE
        ticket_log(db, change.ticket, actor, f"Waiting on change {change.number}")

    if change.approvals_required == 0:
        change.state = ChangeState.APPROVED
        _log(db, change, actor, "Submitted - standard change, pre-approved")
        _on_approved(db, change, actor)
        return

    voters = approvers(db, change)
    if len(voters) < change.approvals_required:
        raise ChangeError(
            f"This change needs {change.approvals_required} approval(s) but only {len(voters)} other person(s) can "
            f"approve. Mark more staff as change approvers on the Users page."
        )
    change.state = ChangeState.PENDING_APPROVAL
    _log(db, change, actor, f"Submitted for approval ({change.approvals_required} needed)")
    for u in voters:
        _email(db, u, f"Approval needed: {change.title}",
               f"{actor.full_name} submitted a {change.change_type.label.lower()} change for your approval "
               f"({change.approvals_required} approval(s) needed).", change)


def decide(db: Session, change: ChangeRequest, approver: User, approve: bool, comment: str) -> None:
    comment = (comment or "").strip()
    if change.state != ChangeState.PENDING_APPROVAL:
        raise ChangeError("This change is not waiting for approval.")
    if approver.id == change.requested_by_id:
        raise ChangeError("You can't approve or reject a change you requested.")
    if not approver.can_approve_changes:
        raise ChangeError("You are not a change approver.")
    if not can_vote(approver, change):
        raise ChangeError("You have already responded to this change.")
    if not approve and not comment:
        raise ChangeError("Please give a reason for rejecting.")

    change.approvals.append(ChangeApproval(approver=approver, approved=approve, comment=comment or None, current=True))
    if not approve:
        change.state = ChangeState.REJECTED
        _log(db, change, approver, f"Rejected: {comment}")
        for u in {change.requested_by, change.implementer} - {None}:
            _email(db, u, f"Rejected: {change.title}", f"{approver.full_name} rejected this change:\n\n{comment}", change)
        _ticket_back_to_work(db, change, approver, "was rejected")
        if change.ticket is not None:
            from .services import _signature
            r = change.ticket.requester
            queue_email(db, r.email, f"[{change.ticket.number}] Update on your request",
                        f"Hello {r.full_name},\n\nYour request {change.ticket.number} ({change.ticket.title}) went "
                        f"through IT change review and was not approved as submitted:\n\n{comment}\n\n"
                        f"The service desk will follow up with you.{_signature()}", change.ticket_id)
        return

    _log(db, change, approver, "Approved" + (f": {comment}" if comment else ""))
    if change.approve_count >= change.approvals_required:
        change.state = ChangeState.APPROVED
        _log(db, change, None, "All required approvals received")
        _on_approved(db, change, approver)


def _on_approved(db: Session, change: ChangeRequest, actor: User) -> None:
    window = f"{_fmt(change.planned_start)} to {_fmt(change.planned_end)}"
    for u in {change.requested_by, change.implementer} - {None}:
        _email(db, u, f"Approved: {change.title}", f"The change is approved and scheduled for {window}.", change)
    if change.ticket is not None:
        from .services import _signature, ticket_url
        r = change.ticket.requester
        queue_email(db, r.email, f"[{change.ticket.number}] Your request was approved",
                    f"Hello {r.full_name},\n\nYour request {change.ticket.number} ({change.ticket.title}) was "
                    f"approved by IT change control and is scheduled for {window}.\n\n"
                    f"Link: {ticket_url(change.ticket)}{_signature()}", change.ticket_id)


def revise(db: Session, change: ChangeRequest, actor: User) -> None:
    if change.state != ChangeState.REJECTED:
        raise ChangeError("Only rejected changes can be revised.")
    if not can_edit(actor, change):
        raise ChangeError("You can't revise this change.")
    change.state = ChangeState.DRAFT
    _log(db, change, actor, "Sent back to draft for revision (previous approvals cleared on resubmit)")


def start(db: Session, change: ChangeRequest, actor: User) -> None:
    if change.state != ChangeState.APPROVED:
        raise ChangeError("Only approved changes can be started.")
    if not can_edit(actor, change):
        raise ChangeError("Only the requester, implementer or an IT manager can start this change.")
    change.state = ChangeState.IN_PROGRESS
    change.actual_start = datetime.now()
    early = change.planned_start and change.actual_start < change.planned_start
    _log(db, change, actor, "Implementation started" + (" (before the planned window)" if early else ""))


def finish(db: Session, change: ChangeRequest, actor: User, success: bool, notes: str) -> None:
    notes = (notes or "").strip()
    if change.state != ChangeState.IN_PROGRESS:
        raise ChangeError("Only changes in progress can be completed.")
    if not can_edit(actor, change):
        raise ChangeError("Only the requester, implementer or an IT manager can close this change.")
    if not notes:
        raise ChangeError("Closure notes are required.")
    change.state = ChangeState.COMPLETED if success else ChangeState.FAILED
    change.actual_end = datetime.now()
    change.close_notes = notes
    _log(db, change, actor, f"{change.state.label}: {notes}")
    _ticket_back_to_work(db, change, actor, "completed" if success else "failed")
    ticket = change.ticket
    recipients = {change.requested_by, change.implementer, ticket.assignee if ticket else None} - {None, actor}
    what = ("was completed successfully" if success else "failed and was backed out")
    follow = (f"\n\nTicket {ticket.number} can now be resolved." if success and ticket else "")
    for u in recipients:
        _email(db, u, f"{change.state.label}: {change.title}", f"{actor.full_name}: the change {what}.\n\n{notes}{follow}",
               change)


def cancel(db: Session, change: ChangeRequest, actor: User, reason: str) -> None:
    reason = (reason or "").strip()
    if change.state not in (ChangeState.DRAFT, ChangeState.PENDING_APPROVAL, ChangeState.APPROVED,
                            ChangeState.REJECTED):
        raise ChangeError("This change can no longer be cancelled.")
    if not can_edit(actor, change):
        raise ChangeError("You can't cancel this change.")
    if not reason:
        raise ChangeError("Please give a reason for cancelling.")
    was_pending = change.state == ChangeState.PENDING_APPROVAL
    change.state = ChangeState.CANCELLED
    _log(db, change, actor, f"Cancelled: {reason}")
    _ticket_back_to_work(db, change, actor, "was cancelled")
    if was_pending:
        for u in approvers(db, change):
            _email(db, u, f"Cancelled: {change.title}", f"No approval needed any more - {actor.full_name} "
                   f"cancelled this change: {reason}", change)


# --- Impact & conflicts ---------------------------------------------------------------------

def depends_on(db: Session, ci: ConfigItem) -> list[CIRelationship]:
    return list(db.scalars(select(CIRelationship).where(CIRelationship.source_id == ci.id)))


def used_by(db: Session, ci: ConfigItem) -> list[CIRelationship]:
    return list(db.scalars(select(CIRelationship).where(CIRelationship.target_id == ci.id)))


def add_relationship(db: Session, source: ConfigItem, target: ConfigItem) -> None:
    if source.id == target.id:
        raise ChangeError("An item can't depend on itself.")
    exists = db.scalar(select(CIRelationship).where(CIRelationship.source_id == source.id,
                                                    CIRelationship.target_id == target.id))
    if exists:
        raise ChangeError("That relationship already exists.")
    db.add(CIRelationship(source=source, target=target))


def impacted(db: Session, items: list[ConfigItem], max_depth: int = 5) -> list[tuple[ConfigItem, ConfigItem]]:
    """Items that depend (directly or indirectly) on any of `items`, as (impacted, via) pairs."""
    start = {ci.id for ci in items}
    seen = set(start)
    queue = deque((ci, 0) for ci in items)
    result = []
    while queue:
        ci, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for rel in used_by(db, ci):
            if rel.source_id not in seen:
                seen.add(rel.source_id)
                result.append((rel.source, ci))
                queue.append((rel.source, depth + 1))
    return result


def _dep_reason(dependent: ConfigItem, changed: list[ConfigItem], via: ConfigItem) -> str:
    names = ", ".join(ci.name for ci in changed)
    indirect = via.id not in {ci.id for ci in changed}
    return f"{dependent.name} depends on {names}" + (f" (via {via.name})" if indirect else "")


@dataclass
class Conflict:
    change: ChangeRequest
    reason: str


def conflicts(db: Session, change: ChangeRequest) -> list[Conflict]:
    """Other pending/scheduled changes in an overlapping window that touch the same items,
    or items that depend on each other (e.g. patching a server while its firewall changes)."""
    if not change.planned_start or not change.planned_end or not change.items:
        return []
    others = db.scalars(select(ChangeRequest).where(
        ChangeRequest.id != change.id,
        ChangeRequest.state.in_([ChangeState.PENDING_APPROVAL, ChangeState.APPROVED, ChangeState.IN_PROGRESS]),
        ChangeRequest.planned_start < change.planned_end,
        ChangeRequest.planned_end > change.planned_start,
    )).all()
    if not others:
        return []
    mine = {ci.id: ci for ci in change.items}
    my_impact = {ci.id: via for ci, via in impacted(db, change.items)}
    found = []
    for other in others:
        theirs = {ci.id: ci for ci in other.items}
        shared = mine.keys() & theirs.keys()
        if shared:
            found.append(Conflict(other, "same item: " + ", ".join(theirs[i].name for i in shared)))
            continue
        hit = next((i for i in theirs if i in my_impact), None)
        if hit is not None:
            found.append(Conflict(other, _dep_reason(theirs[hit], change.items, my_impact[hit])))
            continue
        their_impact = {ci.id: via for ci, via in impacted(db, other.items)}
        hit = next((i for i in mine if i in their_impact), None)
        if hit is not None:
            found.append(Conflict(other, _dep_reason(mine[hit], other.items, their_impact[hit])))
    return found
