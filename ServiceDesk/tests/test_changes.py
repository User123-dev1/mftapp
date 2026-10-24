import re

from sqlalchemy import select

from app import database
from app.models import ChangeRequest, ChangeState, ConfigItem, EmailLog, Status, Ticket, User

from .test_cmdb import upload
from .test_workflow import register

PW = "Password1!"


def user_id(email):
    with database.SessionLocal() as db:
        return db.scalar(select(User).where(User.email == email)).id


def add_staff(manager, name, email, approver=False):
    manager.post("/admin/users", {"full_name": name, "email": email, "password": PW, "role": "agent"})
    if approver:
        manager.post(f"/admin/users/{user_id(email)}", {"role": "agent", "is_active": "true",
                                                        "is_change_approver": "true"})


def setup(client, manager):
    """Rita (requester), Alice (agent + approver), Bob (agent), firewall CI; returns browsers."""
    add_staff(manager, "Alice Agent", "alice@example.com", approver=True)
    add_staff(manager, "Bob Agent", "bob@example.com")
    rita = register(client, "Rita Requester", "rita@example.com")
    upload(manager, "name,type,asset_tag,support_group,change_controlled,assigned_to_email\n"
                    "Core Firewall 1,network,FW-01,Network,yes,\nPayroll,application,,Apps,,\n"
                    "Laptop X,laptop,LT-9,Desktop,,rita@example.com\n")
    return rita, client().login("alice@example.com", PW), client().login("bob@example.com", PW)


def ticket(number=None):
    with database.SessionLocal() as db:
        stmt = select(Ticket).where(Ticket.number == number) if number else select(Ticket).order_by(Ticket.id.desc())
        return db.scalars(stmt).first()


def change(number=None):
    with database.SessionLocal() as db:
        stmt = (select(ChangeRequest).where(ChangeRequest.number == number) if number
                else select(ChangeRequest).order_by(ChangeRequest.id.desc()))
        c = db.scalars(stmt).first()
        if c is not None:
            c.approve_count  # load approvals while the session is open
        return c


def ci_id(name):
    with database.SessionLocal() as db:
        return str(db.scalar(select(ConfigItem).where(ConfigItem.name == name)).id)


def emails(to):
    with database.SessionLocal() as db:
        return db.scalars(select(EmailLog).where(EmailLog.to_address == to).order_by(EmailLog.id)).all()


def firewall_ticket(rita, manager, assignee_email="bob@example.com"):
    rita.post("/tickets/new", {"category": "firewall", "priority": "medium", "title": "Open 443 to ERP",
                               "description": "Warehouse scanners", "detail_firewall_source": "10.20.0.0/24",
                               "detail_firewall_ports": "TCP 443"})
    t = ticket()
    manager.post(f"/tickets/{t.number}/assign", {"assignee_id": user_id(assignee_email)})
    return t.number


CHANGE_FORM = {
    "title": "Allow TCP 443 warehouse -> ERP", "description": "Add firewall rule",
    "change_type": "normal", "risk": "medium", "impact": "low",
    "implementation_plan": "1. Add rule 2. Save", "backout_plan": "Delete rule",
    "planned_start": "2026-11-05T19:00", "planned_end": "2026-11-05T20:00",
}


def raise_change(browser, ticket_number=None, items=("Core Firewall 1",), action="submit", **overrides):
    data = dict(CHANGE_FORM, action=action, **overrides)
    if ticket_number:
        data["ticket"] = ticket_number
    data["csrf_token"] = browser.csrf()
    data["item_ids"] = [ci_id(n) for n in items]
    return browser.client.post("/changes/new", data=data)


def test_firewall_ticket_needs_completed_change(client, manager):
    rita, alice, bob = setup(client, manager)
    number = firewall_ticket(rita, manager)

    # Bob can't resolve: option hidden and server refuses.
    page = bob.get(f"/tickets/{number}").text
    assert "Firewall Change Request tickets require an approved change" in page
    assert 'value="resolved"' not in page and "Raise change request" in page
    r = bob.post(f"/tickets/{number}/status", {"status": "resolved", "resolution_notes": "done",
                                                 "no_change_reason": "trying to skip"})  # not allowed for policy
    assert "Raise a change request" in r.text and ticket(number).status == Status.ASSIGNED

    # Raise form is pre-filled from the ticket.
    form = bob.get(f"/changes/new?ticket={number}").text
    assert "Open 443 to ERP" in form and "10.20.0.0/24" in form

    # Bob raises + submits; ticket waits on the change; Alice and the manager are asked to approve.
    r = raise_change(bob, number)
    c = change()
    assert c.state == ChangeState.PENDING_APPROVAL and c.approvals_required == 1
    assert ticket(number).status == Status.PENDING_CHANGE
    assert any("Approval needed" in e.subject for e in emails("alice@example.com"))
    assert any("Approval needed" in e.subject for e in emails("manager@example.com"))
    assert not any("Approval needed" in e.subject for e in emails("bob@example.com"))
    assert "Waiting on change" in manager.get(f"/tickets/{number}").text
    rita_view = rita.get(f"/tickets/{number}").text
    assert c.number in rita_view and "Pending Approval" in rita_view

    # Bob (not an approver, and the requester) can't approve.
    r = bob.post(f"/changes/{c.number}/decide", {"decision": "approve"})
    assert "can&#39;t approve or reject a change you requested" in r.text

    # Alice approves -> approved; requester of the ticket told the schedule.
    assert ">1</span>" in alice.get("/changes").text  # approvals badge
    alice.post(f"/changes/{c.number}/decide", {"decision": "approve", "comment": "Looks good"})
    assert change(c.number).state == ChangeState.APPROVED
    approved = [e for e in emails("rita@example.com") if "was approved" in e.subject]
    assert approved and "2026-11-05 19:00" in approved[0].body

    # Implement and complete -> ticket back to Bob, who can now resolve it.
    bob.post(f"/changes/{c.number}/start")
    r = bob.post(f"/changes/{c.number}/finish", {"outcome": "completed", "notes": ""})
    assert "Closure notes are required" in r.text
    bob.post(f"/changes/{c.number}/finish", {"outcome": "completed", "notes": "Rule 221 added, tested"})
    assert change(c.number).state == ChangeState.COMPLETED
    assert ticket(number).status == Status.IN_PROGRESS
    page = bob.get(f"/tickets/{number}").text
    assert "Change completed" in page and 'value="resolved"' in page
    bob.post(f"/tickets/{number}/status", {"status": "resolved", "resolution_notes": "Rule added under " + c.number})
    assert ticket(number).status == Status.RESOLVED


def test_submit_validation_and_drafts(client, manager):
    rita, alice, bob = setup(client, manager)
    r = raise_change(bob, backout_plan="", planned_start="")
    assert r.status_code == 400 and "backout plan" in r.text and "planned start and end" in r.text
    assert change() is None  # nothing saved on failure

    raise_change(bob, action="save", backout_plan="")
    c = change()
    assert c.state == ChangeState.DRAFT
    r = bob.post(f"/changes/{c.number}/submit")
    assert "backout plan" in r.text
    r = raise_change(bob, planned_start="2026-11-05T21:00", planned_end="2026-11-05T20:00")
    assert "end must be after" in r.text


def test_high_risk_needs_two_approvals_and_one_vote_each(client, manager):
    rita, alice, bob = setup(client, manager)
    raise_change(bob, risk="high")
    c = change()
    assert c.approvals_required == 2
    alice.post(f"/changes/{c.number}/decide", {"decision": "approve"})
    r = alice.post(f"/changes/{c.number}/decide", {"decision": "approve"})
    assert "already responded" in r.text
    assert change(c.number).state == ChangeState.PENDING_APPROVAL
    manager.post(f"/changes/{c.number}/decide", {"decision": "approve"})
    assert change(c.number).state == ChangeState.APPROVED


def test_reject_revise_resubmit(client, manager):
    rita, alice, bob = setup(client, manager)
    number = firewall_ticket(rita, manager)
    raise_change(bob, number)
    c = change()
    r = alice.post(f"/changes/{c.number}/decide", {"decision": "reject"})
    assert "give a reason" in r.text
    alice.post(f"/changes/{c.number}/decide", {"decision": "reject", "comment": "Use the proxy instead"})
    assert change(c.number).state == ChangeState.REJECTED
    assert ticket(number).status == Status.IN_PROGRESS
    assert any("Use the proxy instead" in e.body for e in emails("rita@example.com"))
    assert any("Rejected" in e.subject for e in emails("bob@example.com"))

    bob.post(f"/changes/{c.number}/revise")
    assert change(c.number).state == ChangeState.DRAFT
    r = bob.client.post(f"/changes/{c.number}/edit", data=dict(
        CHANGE_FORM, csrf_token=bob.csrf(), action="submit", item_ids=[ci_id("Core Firewall 1")],
        description="Narrowed to one host"))
    c2 = change(c.number)
    assert c2.state == ChangeState.PENDING_APPROVAL and c2.approve_count == 0
    assert ticket(number).status == Status.PENDING_CHANGE
    page = alice.get(f"/changes/{c.number}").text
    assert "Earlier votes" in page and 'value="approve"' in page  # Alice can vote again


def test_standard_change_is_pre_approved(client, manager):
    rita, alice, bob = setup(client, manager)
    raise_change(bob, change_type="standard", implementation_plan="", backout_plan="")
    c = change()
    assert c.state == ChangeState.APPROVED and c.approvals_required == 0


def test_not_enough_approvers(client, manager):
    # Only the manager can approve, and the manager raises the change.
    register(client, "Rita", "rita@example.com")
    upload(manager, "name,type\nSRV1,server\n")
    r = raise_change(manager, items=("SRV1",))
    assert "only 0 other person(s) can approve" in r.text


def test_change_controlled_item_blocks_resolution(client, manager):
    rita, alice, bob = setup(client, manager)
    # Payroll isn't controlled; flag it via the item form.
    pid = ci_id("Payroll")
    manager.post(f"/cmdb/{pid}", {"name": "Payroll", "ci_type": "application", "status": "in_use",
                                  "change_controlled": "true"})
    rita.post("/tickets/new", {"category": "software", "priority": "low", "title": "Add payroll user",
                               "description": "x", "ci_id": pid})
    t = ticket()
    manager.post(f"/tickets/{t.number}/assign", {"assignee_id": user_id("bob@example.com")})
    r = bob.post(f"/tickets/{t.number}/status", {"status": "resolved", "resolution_notes": "done"})
    assert "Payroll is change-controlled" in r.text and ticket(t.number).status == Status.ASSIGNED
    assert "No change was needed because" in bob.get(f"/tickets/{t.number}").text
    # Agent records why no change was needed -> allowed, and logged.
    bob.post(f"/tickets/{t.number}/status", {"status": "resolved", "resolution_notes": "Re-sent invite",
                                             "no_change_reason": "User mailbox issue; Payroll not modified"})
    assert ticket(t.number).status == Status.RESOLVED
    assert "Resolved without a change request: User mailbox issue" in manager.get(f"/tickets/{t.number}").text
    # A non-controlled item resolves normally.
    rita.post("/tickets/new", {"category": "hardware", "priority": "low", "title": "Mouse", "description": "x",
                               "ci_id": ci_id("Laptop X")})
    t2 = ticket()
    manager.post(f"/tickets/{t2.number}/assign", {"assignee_id": user_id("bob@example.com")})
    bob.post(f"/tickets/{t2.number}/status", {"status": "resolved", "resolution_notes": "swapped"})
    assert ticket(t2.number).status == Status.RESOLVED


def test_policy_settings(client, manager):
    rita, alice, bob = setup(client, manager)
    page = manager.get("/admin/change-settings").text
    assert "Core Firewall 1" in page and "Alice Agent" in page
    manager.client.post("/admin/change-settings", data={
        "csrf_token": manager.csrf(), "categories": ["account"], "approvals_normal": "2", "approvals_high_risk": "3"})
    number = firewall_ticket(rita, manager)
    bob.post(f"/tickets/{number}/status", {"status": "resolved", "resolution_notes": "ok"})
    assert ticket(number).status == Status.RESOLVED  # firewall no longer gated
    raise_change(bob)
    assert change().approvals_required == 2
    assert bob.get("/admin/change-settings").status_code == 403


def test_impact_and_conflicts(client, manager):
    rita, alice, bob = setup(client, manager)
    upload(manager, "name,type\nSQL01,server\nERP,application\n")
    # ERP depends on SQL01, SQL01 depends on Core Firewall 1.
    manager.post(f"/cmdb/{ci_id('ERP')}/relationships", {"other_id": ci_id("SQL01"), "direction": "depends_on"})
    r = manager.post(f"/cmdb/{ci_id('Core Firewall 1')}/relationships",
                     {"other_id": ci_id("SQL01"), "direction": "used_by"})
    assert "SQL01 now depends on Core Firewall 1" in r.text
    r = manager.post(f"/cmdb/{ci_id('ERP')}/relationships", {"other_id": ci_id("SQL01"), "direction": "depends_on"})
    assert "already exists" in r.text

    raise_change(bob)
    first = change().number
    page = alice.get(f"/changes/{first}").text
    assert re.search(r"SQL01</a> <span class=\"muted small\">Server · via Core Firewall 1", page)
    assert re.search(r"ERP</a> <span class=\"muted small\">Application · via SQL01", page)

    # Overlapping window on the same firewall -> conflict warning both ways.
    raise_change(alice, planned_start="2026-11-05T19:30", planned_end="2026-11-05T21:00", title="Upgrade firmware")
    second = change().number
    assert "Schedule conflict" in alice.get(f"/changes/{second}").text and "same item: Core Firewall 1" in alice.get(f"/changes/{second}").text
    assert first in alice.get(f"/changes/{second}").text
    sched = manager.get("/changes?view=schedule").text
    assert "Thursday 05 November 2026" in sched and "conflict" in sched

    # Different item, but it depends on the firewall being changed -> dependency conflict.
    raise_change(alice, items=("ERP",), planned_start="2026-11-05T19:45", planned_end="2026-11-05T20:30",
                 title="ERP upgrade")
    page = alice.get(f"/changes/{change().number}").text
    assert "ERP depends on Core Firewall 1 (via SQL01)" in page
    # Non-overlapping window -> no conflict.
    raise_change(alice, items=("ERP",), planned_start="2026-11-06T19:45", planned_end="2026-11-06T20:30",
                 title="ERP follow-up")
    assert "Schedule conflict" not in alice.get(f"/changes/{change().number}").text


def test_cancel_returns_ticket_and_access_rules(client, manager):
    rita, alice, bob = setup(client, manager)
    number = firewall_ticket(rita, manager)
    raise_change(bob, number)
    c = change()
    r = bob.post(f"/changes/{c.number}/cancel", {"reason": ""})
    assert "reason" in r.text
    bob.post(f"/changes/{c.number}/cancel", {"reason": "Requirement withdrawn"})
    assert change(c.number).state == ChangeState.CANCELLED
    assert ticket(number).status == Status.IN_PROGRESS
    assert rita.get("/changes").status_code == 403
    assert rita.get(f"/changes/{c.number}").status_code == 403
    # Only requester/implementer/manager may act on a change.
    raise_change(bob, action="save")
    assert "can&#39;t submit" in alice.post(f"/changes/{change().number}/submit").text
