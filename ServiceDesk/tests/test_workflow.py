import re

from sqlalchemy import select

from app import database
from app.models import EmailLog, Status, Ticket, User


def emails_to(address):
    with database.SessionLocal() as db:
        return db.scalars(select(EmailLog).where(EmailLog.to_address == address).order_by(EmailLog.id)).all()


def get_ticket():
    with database.SessionLocal() as db:
        t = db.scalars(select(Ticket)).one()
        return t.number, t.status, t.assignee_id


def register(client, name, email, password="Password1!"):
    b = client()
    r = b.post("/register", {"full_name": name, "email": email, "password": password, "department": "Finance"})
    assert r.status_code == 200
    return b


def make_agent(manager, email="agent@example.com"):
    r = manager.post("/admin/users", {"full_name": "Alice Agent", "email": email, "password": "Password1!", "role": "agent"})
    assert "Created Service Desk Agent" in r.text
    with database.SessionLocal() as db:
        return db.scalar(select(User).where(User.email == email)).id


def test_login_required(client):
    r = client().get("/tickets/new", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


def test_csrf_enforced(client):
    b = client()
    r = b.client.post("/login", data={"email": "x@example.com", "password": "x"})
    assert r.status_code == 403


def test_full_ticket_lifecycle(client, manager):
    # This test covers the basic flow; change control (on for firewall by default) is in test_changes.py.
    from app import changes
    with database.SessionLocal() as db:
        changes.set_setting(db, "change.categories", "[]")
        db.commit()
    agent_id = make_agent(manager)
    requester = register(client, "Rita Requester", "rita@example.com")

    # 1. Requester submits a firewall request.
    r = requester.post(
        "/tickets/new",
        {
            "category": "firewall",
            "priority": "high",
            "title": "Open 443 to payroll server",
            "description": "Need HTTPS from finance VLAN to payroll.",
            "detail_firewall_source": "10.1.2.0/24",
            "detail_firewall_destination": "payroll01",
            "detail_firewall_ports": "TCP 443",
            "detail_software_software_name": "should be ignored",
        },
    )
    assert r.status_code == 200
    number, status, assignee = get_ticket()
    assert re.fullmatch(r"REQ\d{7}", number)
    assert status == Status.NEW and assignee is None
    assert "10.1.2.0/24" in r.text and "should be ignored" not in r.text

    # IT manager is notified, requester gets a receipt.
    assert any("awaiting assignment" in e.subject for e in emails_to("manager@example.com"))
    assert any("We received your request" in e.subject for e in emails_to("rita@example.com"))
    assert all(e.status == "logged" for e in emails_to("rita@example.com"))

    # 2. Ticket appears on the manager's dashboard; requester cannot assign.
    assert number in manager.get("/dashboard").text
    assert requester.post(f"/tickets/{number}/assign", {"assignee_id": agent_id}).status_code == 403

    # 3. Manager assigns to the agent.
    r = manager.post(f"/tickets/{number}/assign", {"assignee_id": agent_id})
    assert "Assigned to Alice Agent" in r.text
    _, status, assignee = get_ticket()
    assert status == Status.ASSIGNED and assignee == agent_id
    assert any("Assigned to you" in e.subject for e in emails_to("agent@example.com"))

    # 4. Agent works the ticket.
    agent = client().login("agent@example.com", "Password1!")
    assert number in agent.get("/tickets?assignee=me").text
    agent.post(f"/tickets/{number}/status", {"status": "in_progress"})
    agent.post(f"/tickets/{number}/comment", {"body": "Checked with network team", "internal": "true"})
    assert "Checked with network team" not in requester.get(f"/tickets/{number}").text

    # Resolution notes are mandatory.
    r = agent.post(f"/tickets/{number}/status", {"status": "resolved", "resolution_notes": ""})
    assert "Resolution notes are required" in r.text
    assert get_ticket()[1] == Status.IN_PROGRESS

    # 5. Agent resolves -> requester is emailed with the resolution.
    agent.post(f"/tickets/{number}/status", {"status": "resolved", "resolution_notes": "Rule FW-221 added."})
    assert get_ticket()[1] == Status.RESOLVED
    resolved = [e for e in emails_to("rita@example.com") if "Resolved" in e.subject]
    assert len(resolved) == 1 and "Rule FW-221 added." in resolved[0].body

    # 6. Requester can reopen; agent is notified.
    requester.post(f"/tickets/{number}/reopen", {"reason": "Still blocked"})
    assert get_ticket()[1] == Status.IN_PROGRESS
    assert any("Reopened" in e.subject for e in emails_to("agent@example.com"))


def test_incident_numbering_and_isolation(client, manager):
    a = register(client, "Ann", "ann@example.com")
    b = register(client, "Bob", "bob@example.com")
    a.post("/tickets/new", {"category": "incident", "priority": "critical", "title": "Printer on fire", "description": "Really."})
    number = get_ticket()[0]
    assert number.startswith("INC")
    assert b.get(f"/tickets/{number}").status_code == 403
    assert number not in b.get("/tickets?status=all").text
    assert number in a.get("/tickets?status=all").text


def test_agent_cannot_work_unassigned_ticket(client, manager):
    make_agent(manager)
    requester = register(client, "Rita", "rita@example.com")
    requester.post("/tickets/new", {"category": "software", "priority": "low", "title": "Need Visio", "description": "Please"})
    number = get_ticket()[0]
    agent = client().login("agent@example.com", "Password1!")
    r = agent.post(f"/tickets/{number}/status", {"status": "resolved", "resolution_notes": "x"})
    assert "Cannot move ticket" in r.text
    assert get_ticket()[1] == Status.NEW


def test_smtp_delivery(client, manager, monkeypatch):
    from app import emailer
    from app.config import settings

    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context=None):
            sent.append(("starttls",))

        def login(self, user, password):
            sent.append(("login", user))

        def send_message(self, msg):
            sent.append(("send", msg["To"], msg["Subject"]))

    monkeypatch.setattr(emailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_user", "svc")

    requester = register(client, "Rita", "rita@example.com")
    requester.post("/tickets/new", {"category": "general", "priority": "low", "title": "Question", "description": "Hi"})

    assert ("connect", "smtp.example.com", 587) in sent
    assert ("starttls",) in sent and ("login", "svc") in sent
    assert any(s[0] == "send" and s[1] == "rita@example.com" for s in sent)
    assert all(e.status == "sent" for e in emails_to("rita@example.com"))


def test_manager_assigns_from_ticket_list(client, manager):
    agent_id = make_agent(manager)
    requester = register(client, "Rita", "rita@example.com")
    requester.post("/tickets/new", {"category": "general", "priority": "low", "title": "Question", "description": "Hi"})
    number = get_ticket()[0]

    page = manager.get("/tickets?status=new").text
    assert f'action="/tickets/{number}/assign"' in page and "Choose agent" in page
    r = manager.post(f"/tickets/{number}/assign", {"assignee_id": agent_id, "next": "/tickets?status=new"},
                     follow=False)
    assert r.headers["location"] == "/tickets?status=new"  # back to the list, not the ticket
    assert get_ticket()[2] == agent_id
    # Unsafe redirect targets are ignored.
    r = manager.post(f"/tickets/{number}/assign", {"assignee_id": agent_id, "next": "//evil.example"}, follow=False)
    assert r.headers["location"] == "/"
    # Agents and requesters don't get the assign column.
    agent = client().login("agent@example.com", "Password1!")
    assert "assign-form" not in agent.get("/tickets").text
