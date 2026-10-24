"""Ticket workflow: create -> IT manager assigns -> agent works -> resolved -> requester notified."""

import json
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .emailer import queue_email
from .models import Activity, Category, Comment, Priority, Role, Status, Ticket, User, utcnow


class WorkflowError(ValueError):
    """A ticket action that is not allowed in the ticket's current state or for this user."""


# Category-specific fields shown on the request form and stored in Ticket.details.
CATEGORY_FIELDS: dict[Category, list[tuple[str, str]]] = {
    Category.INCIDENT: [
        ("impact", "Who is affected? (just me / my team / whole organization)"),
        ("started", "When did it start?"),
        ("error_message", "Error message (if any)"),
    ],
    Category.SOFTWARE: [
        ("software_name", "Software name"),
        ("version", "Version"),
        ("device", "Computer name / asset tag"),
    ],
    Category.HARDWARE: [
        ("device", "Device type / asset tag"),
        ("location", "Location"),
    ],
    Category.FIREWALL: [
        ("source", "Source IP / host / network"),
        ("destination", "Destination IP / host / network"),
        ("ports", "Port(s) and protocol (e.g. TCP 443)"),
        ("duration", "Permanent or temporary? (end date)"),
        ("justification", "Business justification"),
    ],
    Category.NETWORK: [
        ("location", "Location / office"),
        ("connection", "Connection type (wired / Wi-Fi / VPN)"),
    ],
    Category.ACCOUNT: [
        ("request_type", "Request type (new account / access / password reset / disable)"),
        ("for_user", "For which user?"),
        ("system", "System or application"),
        ("access_level", "Access level needed"),
    ],
    Category.EMAIL: [
        ("mailbox", "Mailbox / distribution list"),
    ],
}


def ticket_url(ticket: Ticket) -> str:
    return f"{settings.base_url.rstrip('/')}/tickets/{ticket.number}"


def _log(db: Session, ticket: Ticket, actor: Optional[User], message: str) -> None:
    db.add(Activity(ticket=ticket, actor=actor, message=message))


def active_users_with_role(db: Session, role: Role) -> list[User]:
    return list(db.scalars(select(User).where(User.role == role, User.is_active.is_(True)).order_by(User.full_name)))


def _signature() -> str:
    return f"\n\n-- \n{settings.app_name}\nThis is an automated message."


def _summary(ticket: Ticket) -> str:
    return (
        f"Ticket:    {ticket.number}\n"
        f"Title:     {ticket.title}\n"
        f"Category:  {ticket.category.label}\n"
        f"Priority:  {ticket.priority.label}\n"
        f"Requester: {ticket.requester.full_name} <{ticket.requester.email}>\n"
        f"Link:      {ticket_url(ticket)}"
    )


def parse_details(ticket: Ticket) -> list[tuple[str, str]]:
    """Return category details as (label, value) pairs for display."""
    if not ticket.details:
        return []
    try:
        data = json.loads(ticket.details)
    except ValueError:
        return []
    labels = dict(CATEGORY_FIELDS.get(ticket.category, []))
    return [(labels.get(k, k.replace("_", " ").title()), v) for k, v in data.items() if v]


# --- Actions ----------------------------------------------------------------

def create_ticket(
    db: Session,
    requester: User,
    title: str,
    description: str,
    category: Category,
    priority: Priority,
    details: Optional[dict] = None,
) -> Ticket:
    title, description = title.strip(), description.strip()
    if not title or not description:
        raise WorkflowError("Title and description are required.")

    clean_details = {k: v.strip() for k, v in (details or {}).items() if v and v.strip()}
    ticket = Ticket(
        title=title,
        description=description,
        category=category,
        priority=priority,
        status=Status.NEW,
        requester=requester,
        details=json.dumps(clean_details) if clean_details else None,
    )
    db.add(ticket)
    db.flush()  # get the id for the ticket number
    prefix = "INC" if category == Category.INCIDENT else "REQ"
    ticket.number = f"{prefix}{1000000 + ticket.id}"
    _log(db, ticket, requester, "Ticket submitted")

    # Route to IT managers for triage / assignment.
    for manager in active_users_with_role(db, Role.MANAGER):
        queue_email(
            db,
            manager.email,
            f"[{ticket.number}] New {ticket.category.label} awaiting assignment: {ticket.title}",
            f"Hello {manager.full_name},\n\nA new ticket has been submitted and needs to be assigned.\n\n"
            f"{_summary(ticket)}\n\nDescription:\n{ticket.description}{_signature()}",
            ticket.id,
        )

    queue_email(
        db,
        requester.email,
        f"[{ticket.number}] We received your request: {ticket.title}",
        f"Hello {requester.full_name},\n\nThank you - your ticket has been logged and will be assigned "
        f"to a service desk technician shortly.\n\n{_summary(ticket)}{_signature()}",
        ticket.id,
    )
    return ticket


def assign_ticket(db: Session, ticket: Ticket, agent: User, manager: User) -> None:
    if manager.role != Role.MANAGER:
        raise WorkflowError("Only an IT manager can assign tickets.")
    if not agent.is_staff or not agent.is_active:
        raise WorkflowError("Tickets can only be assigned to active service desk staff.")
    if not ticket.status.is_open:
        raise WorkflowError("Reopen the ticket before assigning it.")
    if ticket.assignee_id == agent.id:
        return

    previous = ticket.assignee
    ticket.assignee = agent
    ticket.assigned_by = manager
    ticket.assigned_at = utcnow()
    if ticket.status == Status.NEW:
        ticket.status = Status.ASSIGNED
    if previous:
        _log(db, ticket, manager, f"Reassigned from {previous.full_name} to {agent.full_name}")
    else:
        _log(db, ticket, manager, f"Assigned to {agent.full_name}")

    queue_email(
        db,
        agent.email,
        f"[{ticket.number}] Assigned to you: {ticket.title}",
        f"Hello {agent.full_name},\n\n{manager.full_name} has assigned you a ticket.\n\n"
        f"{_summary(ticket)}\n\nDescription:\n{ticket.description}{_signature()}",
        ticket.id,
    )
    queue_email(
        db,
        ticket.requester.email,
        f"[{ticket.number}] Your ticket has been assigned",
        f"Hello {ticket.requester.full_name},\n\nYour ticket is now being handled by {agent.full_name}.\n\n"
        f"{_summary(ticket)}{_signature()}",
        ticket.id,
    )


def can_work(user: User, ticket: Ticket) -> bool:
    """Managers can work any ticket; agents only tickets assigned to them."""
    return user.role == Role.MANAGER or (user.role == Role.AGENT and ticket.assignee_id == user.id)


STAFF_TRANSITIONS = {
    Status.NEW: {Status.CANCELLED},
    Status.ASSIGNED: {Status.IN_PROGRESS, Status.ON_HOLD, Status.RESOLVED, Status.CANCELLED},
    Status.IN_PROGRESS: {Status.ON_HOLD, Status.RESOLVED, Status.CANCELLED},
    Status.ON_HOLD: {Status.IN_PROGRESS, Status.RESOLVED, Status.CANCELLED},
    Status.RESOLVED: {Status.IN_PROGRESS, Status.CLOSED},
    Status.CLOSED: set(),
    Status.CANCELLED: set(),
}


def allowed_statuses(user: User, ticket: Ticket) -> list[Status]:
    if not can_work(user, ticket):
        return []
    return [s for s in Status if s in STAFF_TRANSITIONS[ticket.status]]


def change_status(
    db: Session, ticket: Ticket, new_status: Status, actor: User, resolution_notes: str = ""
) -> None:
    if new_status not in allowed_statuses(actor, ticket):
        raise WorkflowError(f"Cannot move ticket from {ticket.status.label} to {new_status.label}.")

    old = ticket.status
    resolution_notes = (resolution_notes or "").strip()
    if new_status == Status.RESOLVED:
        if not resolution_notes:
            raise WorkflowError("Resolution notes are required to resolve a ticket.")
        ticket.resolution_notes = resolution_notes
        ticket.resolved_at = utcnow()
    elif old == Status.RESOLVED and new_status == Status.IN_PROGRESS:
        ticket.resolved_at = None

    ticket.status = new_status
    _log(db, ticket, actor, f"Status changed from {old.label} to {new_status.label}")

    requester = ticket.requester
    if new_status == Status.RESOLVED:
        queue_email(
            db,
            requester.email,
            f"[{ticket.number}] Resolved: {ticket.title}",
            f"Hello {requester.full_name},\n\nGood news - your ticket has been resolved by {actor.full_name}.\n\n"
            f"Resolution:\n{resolution_notes}\n\n{_summary(ticket)}\n\n"
            f"If the problem is not fixed, you can reopen the ticket from the link above.{_signature()}",
            ticket.id,
        )
    elif new_status == Status.CANCELLED:
        queue_email(
            db,
            requester.email,
            f"[{ticket.number}] Cancelled: {ticket.title}",
            f"Hello {requester.full_name},\n\nYour ticket has been cancelled by {actor.full_name}.\n\n"
            f"{_summary(ticket)}{_signature()}",
            ticket.id,
        )


def update_fields(db: Session, ticket: Ticket, actor: User, category: Category, priority: Priority) -> None:
    if not can_work(actor, ticket):
        raise WorkflowError("You are not allowed to update this ticket.")
    if category != ticket.category:
        _log(db, ticket, actor, f"Category changed from {ticket.category.label} to {category.label}")
        ticket.category = category
    if priority != ticket.priority:
        _log(db, ticket, actor, f"Priority changed from {ticket.priority.label} to {priority.label}")
        ticket.priority = priority


def requester_reopen(db: Session, ticket: Ticket, requester: User, reason: str) -> None:
    if ticket.requester_id != requester.id:
        raise WorkflowError("Only the requester can reopen this ticket.")
    if ticket.status != Status.RESOLVED:
        raise WorkflowError("Only resolved tickets can be reopened.")
    reason = reason.strip()
    if not reason:
        raise WorkflowError("Please say why the ticket should be reopened.")
    ticket.status = Status.IN_PROGRESS if ticket.assignee_id else Status.NEW
    ticket.resolved_at = None
    db.add(Comment(ticket=ticket, author=requester, body=f"Reopened: {reason}", internal=False))
    _log(db, ticket, requester, "Reopened by requester")
    for user in _staff_to_notify(db, ticket):
        queue_email(
            db,
            user.email,
            f"[{ticket.number}] Reopened by requester: {ticket.title}",
            f"Hello {user.full_name},\n\n{requester.full_name} reopened the ticket:\n\n{reason}\n\n"
            f"{_summary(ticket)}{_signature()}",
            ticket.id,
        )


def requester_cancel(db: Session, ticket: Ticket, requester: User) -> None:
    if ticket.requester_id != requester.id:
        raise WorkflowError("Only the requester can cancel this ticket.")
    if not ticket.status.is_open:
        raise WorkflowError("This ticket is already closed.")
    ticket.status = Status.CANCELLED
    _log(db, ticket, requester, "Cancelled by requester")
    for user in _staff_to_notify(db, ticket):
        queue_email(
            db,
            user.email,
            f"[{ticket.number}] Cancelled by requester: {ticket.title}",
            f"Hello {user.full_name},\n\n{requester.full_name} cancelled this ticket; no further work is needed.\n\n"
            f"{_summary(ticket)}{_signature()}",
            ticket.id,
        )


def _staff_to_notify(db: Session, ticket: Ticket) -> list[User]:
    if ticket.assignee and ticket.assignee.is_active:
        return [ticket.assignee]
    return active_users_with_role(db, Role.MANAGER)


def add_comment(db: Session, ticket: Ticket, author: User, body: str, internal: bool = False) -> None:
    body = body.strip()
    if not body:
        raise WorkflowError("Comment cannot be empty.")
    if internal and not author.is_staff:
        raise WorkflowError("Only IT staff can add work notes.")
    db.add(Comment(ticket=ticket, author=author, body=body, internal=internal))
    _log(db, ticket, author, "Work note added" if internal else "Comment added")

    if internal:
        return
    if author.id == ticket.requester_id:
        recipients = _staff_to_notify(db, ticket)
    else:
        recipients = [ticket.requester]
    for user in recipients:
        if user.id == author.id:
            continue
        queue_email(
            db,
            user.email,
            f"[{ticket.number}] New comment from {author.full_name}",
            f"Hello {user.full_name},\n\n{author.full_name} commented on {ticket.number}:\n\n{body}\n\n"
            f"{_summary(ticket)}{_signature()}",
            ticket.id,
        )
