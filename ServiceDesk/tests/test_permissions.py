"""Permission matrix: every page, opened by every kind of user.

A = allowed (200), D = denied (403), L = sent to the login page.
"""

import pytest
from sqlalchemy import select

from app import database
from app.models import Role, Ticket, User
from app.security import hash_password

from .test_abelorder import ONE_ITEM, form, latest as latest_order, org, post_request

PW = "Password1!"

# path:                         anon requester agent manager procurement(requester role)
MATRIX = {
    "/status":                  "L A A A A",
    "/tickets/new":             "L A A A A",
    "/tickets":                 "L A A A A",   # requesters only get their own (checked below)
    "/abelorder":               "L A A A A",
    "/abelorder/new":           "L A A A A",
    "/profile":                 "L A A A A",
    "/dashboard":               "L D A A D",
    "/reports":                 "L D A A D",
    "/cmdb":                    "L D A A D",
    "/cmdb/new":                "L D A A D",
    "/cmdb/export.csv":         "L D A A D",
    "/changes":                 "L D A A D",
    "/changes/new":             "L D A A D",
    "/notices":                 "L D A A D",
    "/notices/new":             "L D A A D",
    "/admin/abelorder":         "L D D A A",   # manager: settings; procurement: vendor list only
    "/abelorder/emails":        "L D D D A",   # purchasing mail stays with Procurement
    "/cmdb/import":             "L D D A D",
    "/cmdb/sync":               "L D D A D",
    "/admin/users":             "L D D A D",
    "/admin/groups":            "L D D A D",
    "/admin/change-settings":   "L D D A D",
    "/admin/mail":              "L D D A D",
    "/admin/emails":            "L D D A D",
}
WHO = ["anon", "requester", "agent", "manager", "procurement"]


@pytest.fixture()
def people(client, manager):
    def make(email, role, **kw):
        with database.SessionLocal() as db:
            db.add(User(email=email, full_name=email.split("@")[0], role=role, is_active=True,
                        password_hash=hash_password(PW), **kw))
            db.commit()
        return client().login(email, PW)
    return {
        "anon": client(),
        "requester": make("req@example.com", Role.REQUESTER),
        "agent": make("agent@example.com", Role.AGENT),
        "manager": manager,
        "procurement": make("buyer@example.com", Role.REQUESTER, is_procurement=True),
    }


@pytest.mark.parametrize("path", list(MATRIX))
def test_page_access(people, path):
    expected = dict(zip(WHO, MATRIX[path].split()))
    for who, want in expected.items():
        r = people[who].get(path, follow_redirects=False)
        got = {200: "A", 403: "D", 303: "L"}.get(r.status_code, str(r.status_code))
        if got == "L":
            assert r.headers["location"].startswith("/login"), (who, path)
        assert got == want, f"{who} on {path}: expected {want}, got {got}"


def test_requesters_only_see_their_own_tickets(client, people):
    people["requester"].post("/tickets/new", {"category": "general", "priority": "low", "title": "Mine",
                                              "description": "x"})
    other = client()
    other.post("/register", {"full_name": "Other", "email": "other@example.com", "password": PW})
    other.post("/tickets/new", {"category": "general", "priority": "low", "title": "Not yours", "description": "y"})
    with database.SessionLocal() as db:
        theirs = db.scalar(select(Ticket).where(Ticket.title == "Not yours")).number
    listing = people["requester"].get("/tickets?status=all").text
    assert "Mine" in listing and "Not yours" not in listing
    assert people["requester"].get(f"/tickets/{theirs}").status_code == 403
    assert people["manager"].get(f"/tickets/{theirs}").status_code == 200
    # Internal work notes stay hidden from the requester.
    people["manager"].post(f"/tickets/{theirs}/comment", {"body": "internal only", "internal": "true"})
    assert "internal only" in people["manager"].get(f"/tickets/{theirs}").text
    assert "internal only" not in other.get(f"/tickets/{theirs}").text


def test_purchase_requests_visible_only_to_people_involved(client, manager):
    ids, b = org(client, manager)
    post_request(b["emma"], form(ids, ONE_ITEM))
    number = latest_order().number
    assert b["emma"].get(f"/abelorder/{number}").status_code == 200     # requester
    assert b["sam"].get(f"/abelorder/{number}").status_code == 200      # on the signature route
    assert b["pat"].get(f"/abelorder/{number}").status_code == 200      # procurement
    assert manager.get(f"/abelorder/{number}").status_code == 403       # IT manager: not purchasing data
    assert b["olly"].get(f"/abelorder/{number}").status_code == 403     # anyone else
    assert b["hana"].get(f"/abelorder/{number}").status_code == 403     # not on this route (< $10k)
    agent = client()
    with database.SessionLocal() as db:
        db.add(User(email="it.agent@example.com", full_name="IT Agent", role=Role.AGENT, is_active=True,
                    password_hash=hash_password(PW)))
        db.commit()
    assert agent.login("it.agent@example.com", PW).get(f"/abelorder/{number}").status_code == 403


def test_agents_see_only_their_support_groups_tickets(client, manager):
    from .test_cmdb import upload
    from .test_workflow import make_agent, register

    alice = make_agent(manager, "alice@example.com")
    bob = make_agent(manager, "bob@example.com")
    rita = register(client, "Rita", "rita@example.com")
    upload(manager, "name,type,asset_tag,assigned_to_email,support_group\n"
                    "Rita laptop,laptop,LT-1,rita@example.com,Desktop\nPayroll,application,,,Apps\n")
    with database.SessionLocal() as db:
        from app.models import ConfigItem, SupportGroup
        groups = {g.name: g.id for g in db.scalars(select(SupportGroup))}
        items = {c.name: str(c.id) for c in db.scalars(select(ConfigItem))}
    manager.client.post(f"/admin/groups/{groups['Desktop']}", data={
        "csrf_token": manager.csrf(), "name": "Desktop", "member_ids": [str(alice)]})
    rita.post("/tickets/new", {"category": "hardware", "priority": "low", "title": "Laptop fan", "description": "x",
                               "ci_id": items["Rita laptop"]})
    rita.post("/tickets/new", {"category": "software", "priority": "low", "title": "Payslip", "description": "x",
                               "ci_id": items["Payroll"]})
    rita.post("/tickets/new", {"category": "general", "priority": "low", "title": "Question", "description": "x"})
    with database.SessionLocal() as db:
        num = {t.title: t.number for t in db.scalars(select(Ticket))}

    a = client().login("alice@example.com", PW)
    b = client().login("bob@example.com", PW)
    listing = a.get("/tickets?status=all").text
    assert "Laptop fan" in listing and "Payslip" not in listing and "Question" not in listing
    assert a.get(f"/tickets/{num['Laptop fan']}").status_code == 200
    assert a.get(f"/tickets/{num['Payslip']}").status_code == 403
    assert "Laptop fan" not in b.get("/tickets?status=all").text

    # Dashboard counts, reports and CMDB history are limited the same way.
    assert '<span class="num">1</span><span>New</span>' in a.get("/dashboard").text
    assert '<span class="num">1</span><span>Tickets opened' in a.get("/reports").text
    assert '<span class="num">0</span><span>Tickets opened' in b.get("/reports").text
    assert "Payslip" not in a.get(f"/cmdb/{items['Payroll']}").text
    # Pre-filling a change or notice from a ticket they can't see is refused.
    assert a.get(f"/changes/new?ticket={num['Payslip']}").status_code == 403
    assert a.get(f"/notices/new?ticket={num['Payslip']}").status_code == 403

    # A change raised from the Payslip ticket doesn't reveal the ticket title to Alice.
    with database.SessionLocal() as db:
        from app.models import ChangeRequest
        from app import changes as chg
        t = db.scalar(select(Ticket).where(Ticket.number == num["Payslip"]))
        c = chg.create_change(db, db.get(User, bob), {"title": "Payroll config", "description": "d"}, [], None, t)
        db.commit()
        cnum = c.number
    page = a.get(f"/changes/{cnum}").text
    assert num["Payslip"] in page and "Payslip</" not in page and "— Payslip" not in page

    # Assigning a ticket to Bob lets Bob see it, whatever its group.
    manager.post(f"/tickets/{num['Payslip']}/assign", {"assignee_id": bob})
    assert b.get(f"/tickets/{num['Payslip']}").status_code == 200
    # Managers still see everything.
    assert all(t in manager.get("/tickets?status=all").text for t in ("Laptop fan", "Payslip", "Question"))


def test_purchasing_data_stays_with_procurement_and_approvers(client, manager):
    from app.models import EmailLog
    ids, b = org(client, manager)
    post_request(b["emma"], form(ids, ONE_ITEM))
    number = latest_order().number
    b["sam"].post(f"/abelorder/{number}/decide", {"decision": "approve"})

    # Managers: no request list tabs, no PDF, no PO entry, no vendors - but they keep the settings.
    page = manager.get("/abelorder?view=all").text
    assert number not in page and "Ready for PO" not in page
    assert manager.get(f"/abelorder/{number}/pdf").status_code == 403
    assert manager.post(f"/abelorder/{number}/po", {"po_number": "PO-X"}).status_code == 403
    settings_page = manager.get("/admin/abelorder").text
    assert "Departments" in settings_page and 'id="vendors"' not in settings_page
    assert manager.post("/admin/abelorder/vendors", {"name": "Sneaky Co"}).status_code == 403

    # IT Email Log hides AbelOrder mail; Procurement has its own log with it.
    it_log = manager.get("/admin/emails").text
    assert number not in it_log and "abztprocurement" not in it_log
    assert manager.get("/abelorder/emails").status_code == 403
    proc_log = b["pat"].get("/abelorder/emails").text
    assert "AbelOrder email log" in proc_log and f"[{number}] Approved purchase request" in proc_log
    with database.SessionLocal() as db:
        assert {e.source for e in db.scalars(select(EmailLog).where(EmailLog.subject.like(f"[{number}]%")))} == {"abelorder"}
    # Ticket emails still show for IT.
    assert "abelorder" not in {e.source for e in db.scalars(select(EmailLog).where(EmailLog.subject.like("[REQ%")))}


def test_existing_abelorder_emails_are_retagged_on_upgrade(tmp_path):
    import sqlite3
    from app import database as dbmod
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE email_log (id INTEGER PRIMARY KEY, ticket_id INTEGER, to_address VARCHAR(255), "
                "subject VARCHAR(500), body TEXT, status VARCHAR(20), error TEXT, attachments TEXT, "
                "created_at DATETIME, sent_at DATETIME)")
    con.execute("INSERT INTO email_log (to_address, subject, body, status) VALUES "
                "('a@x.com', '[AO-2026-00001] Approval needed', 'b', 'sent'), "
                "('b@x.com', '[REQ1000001] We received your request', 'b', 'sent')")
    con.commit()
    con.close()
    dbmod.configure(f"sqlite:///{path}")
    dbmod.init_db()
    con = sqlite3.connect(path)
    rows = dict(con.execute("SELECT to_address, source FROM email_log").fetchall())
    assert rows == {"a@x.com": "abelorder", "b@x.com": "servicedesk"}
