import re

from sqlalchemy import select

from app import database
from app.models import CIType, ConfigItem, EmailLog, SupportGroup, Ticket, User

from .test_workflow import make_agent, register

CSV = """name,type,status,asset_tag,serial_number,manufacturer,model,version,location,assigned_to_email,support_group,warranty_expires
Rita's laptop,laptop,in_use,LT-001,SN1,Dell,Latitude 7440,Windows 11,HQ,rita@example.com,Desktop Support,2020-01-01
Bob's laptop,Laptop,,LT-002,SN2,HP,EliteBook,,HQ,bob@example.com,Desktop Support,
Payroll,application,,,,,,,,,Business Apps,
VPN,Business Service,,,,,,,,,Network,
Lobby printer,printer,,PR-01,,,,,Lobby,,Desktop Support,
Old desktop,desktop,retired,DT-009,,,,,,rita@example.com,,
Broken row,spaceship,,,,,,,,,,
,laptop,,,,,,,,,,
"""


def upload(manager, body, name="items.csv"):
    data = {"csrf_token": manager.csrf()}
    return manager.client.post("/cmdb/import", data=data, files={"file": (name, body.encode(), "text/csv")})


def ci(name):
    with database.SessionLocal() as db:
        return db.scalar(select(ConfigItem).where(ConfigItem.name == name))


def ticket():
    with database.SessionLocal() as db:
        t = db.scalars(select(Ticket)).one()
        return t.number, t.ci_id


def setup_people(client, manager):
    rita = register(client, "Rita Requester", "rita@example.com")
    register(client, "Bob", "bob@example.com")
    return rita


def test_import_creates_updates_and_reports(client, manager):
    setup_people(client, manager)
    r = upload(manager, CSV)
    assert r.status_code == 200
    assert "Created 6, updated 0" in r.text
    assert "Row 8: unknown CIType" in r.text and "Row 9: name is required" in r.text

    laptop = ci("Rita's laptop")
    assert laptop.ci_type == CIType.LAPTOP and laptop.asset_tag == "LT-001" and laptop.warranty_expired
    with database.SessionLocal() as db:
        assert {g.name for g in db.scalars(select(SupportGroup))} == {"Desktop Support", "Business Apps", "Network"}
        assert db.get(ConfigItem, laptop.id).assigned_user.email == "rita@example.com"

    # Re-import: matched by asset tag / name, blanks don't erase, nothing duplicated.
    r = upload(manager, "name,type,asset_tag,location,model\nRita's laptop,laptop,LT-001,Branch Office,\nPayroll,application,,,\n")
    assert "Created 0, updated 2" in r.text
    assert ci("Rita's laptop").location == "Branch Office" and ci("Rita's laptop").model == "Latitude 7440"
    with database.SessionLocal() as db:
        assert db.query(ConfigItem).count() == 6


def test_import_serial_clash_warns(client, manager):
    upload(manager, "name,type,asset_tag,serial_number\nA,laptop,TAG-A,SER-A\nB,laptop,TAG-B,SER-B\n")
    # Asset tag wins the match (updates A), but the serial belongs to B.
    r = upload(manager, "name,type,asset_tag,serial_number\nA2,laptop,TAG-A,SER-B\n")
    assert "serial SER-B is also on B [TAG-B]" in r.text


def test_import_failed_row_is_rolled_back_alone(client, manager, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    from app import cmdb

    real = cmdb._apply_row

    def flaky(db, row, *args):
        outcome = real(db, row, *args)  # writes the row, then fails like a DB constraint would
        if row["name"] == "Bad":
            raise IntegrityError("insert", {}, Exception("constraint"))
        return outcome

    monkeypatch.setattr(cmdb, "_apply_row", flaky)
    r = upload(manager, "name,type,asset_tag,support_group\nGood1,laptop,G1,Team A\nBad,laptop,B1,Team B\nGood2,laptop,G2,Team A\n")
    assert "Created 2" in r.text and "Row 3: conflicts with an existing item" in r.text
    assert ci("Good1") and ci("Good2") and ci("Bad") is None
    with database.SessionLocal() as db:
        assert {g.name for g in db.scalars(select(SupportGroup))} == {"Team A"}


def test_import_unknown_user_warns(client, manager):
    r = upload(manager, "name,type,asset_tag,assigned_to_email\nX,laptop,T1,ghost@example.com\n")
    assert "no user ghost@example.com" in r.text
    assert ci("X").assigned_user_id is None


def test_import_requires_manager(client, manager):
    make_agent(manager)
    from .conftest import Browser  # noqa
    agent = client().login("agent@example.com", "Password1!")
    assert upload(agent, CSV).status_code == 403
    assert agent.get("/cmdb").status_code == 200
    rita = register(client, "Rita", "rita@example.com")
    assert rita.get("/cmdb").status_code == 403


def test_requester_picks_own_device(client, manager):
    rita = setup_people(client, manager)
    upload(manager, CSV)
    page = rita.get("/tickets/new").text
    assert "Rita&#39;s laptop" in page and "Payroll" in page and "VPN" in page and "Lobby printer" in page
    assert "Bob&#39;s laptop" not in page and "Old desktop" not in page

    # Portal shows the device with a shortcut that preselects it.
    portal = rita.get("/").text
    laptop_id = ci("Rita's laptop").id
    assert f"/tickets/new?ci={laptop_id}" in portal
    form = rita.get(f"/tickets/new?ci={laptop_id}").text
    assert re.search(rf'<option value="{laptop_id}" selected', form)
    assert re.search(r'<option value="hardware" selected', form)

    r = rita.post("/tickets/new", {"category": "hardware", "priority": "medium", "title": "Screen flickers",
                                   "description": "Since update", "ci_id": str(laptop_id)})
    assert r.status_code == 200
    number, ci_id = ticket()
    assert ci_id == laptop_id
    assert "Latitude 7440" in r.text
    with database.SessionLocal() as db:
        alert = db.scalar(select(EmailLog).where(EmailLog.to_address == "manager@example.com"))
        assert "Affected:  Rita's laptop [LT-001] (Laptop)" in alert.body


def test_requester_cannot_pick_someone_elses_device(client, manager):
    rita = setup_people(client, manager)
    upload(manager, CSV)
    r = rita.post("/tickets/new", {"category": "hardware", "priority": "low", "title": "x", "description": "y",
                                   "ci_id": str(ci("Bob's laptop").id)})
    assert r.status_code == 400 and "cannot raise a ticket against that item" in r.text
    r = rita.post("/tickets/new", {"category": "hardware", "priority": "low", "title": "x", "description": "y",
                                   "ci_id": str(ci("Old desktop").id)})
    assert r.status_code == 400


def test_support_group_suggests_least_busy_member(client, manager):
    rita = setup_people(client, manager)
    alice = make_agent(manager, "alice@example.com")
    manager.post("/admin/users", {"full_name": "Zed Agent", "email": "zed@example.com", "password": "Password1!", "role": "agent"})
    with database.SessionLocal() as db:
        zed = db.scalar(select(User).where(User.email == "zed@example.com")).id
    upload(manager, CSV)
    with database.SessionLocal() as db:
        group_id = db.scalar(select(SupportGroup).where(SupportGroup.name == "Desktop Support")).id

    # Manager adds both agents to Desktop Support.
    r = manager.client.post(f"/admin/groups/{group_id}", data={
        "csrf_token": manager.csrf(), "name": "Desktop Support", "member_ids": [str(alice), str(zed)]})
    assert r.status_code == 200 and "Saved Desktop Support" in r.text

    laptop_id = str(ci("Rita's laptop").id)
    rita.post("/tickets/new", {"category": "hardware", "priority": "low", "title": "One", "description": "d", "ci_id": laptop_id})
    first = ticket()[0]
    page = manager.get(f"/tickets/{first}").text
    assert "Suggested: <strong>Alice Agent</strong>" in page  # tie -> alphabetical
    assert re.search(rf'<option value="{alice}" selected>Alice Agent', page)
    assert "★ suggested" in manager.get("/dashboard").text
    manager.post(f"/tickets/{first}/assign", {"assignee_id": alice})

    # Alice now has one open ticket, so Zed is suggested for the next.
    rita.post("/tickets/new", {"category": "hardware", "priority": "low", "title": "Two", "description": "d", "ci_id": laptop_id})
    with database.SessionLocal() as db:
        second = db.scalar(select(Ticket).where(Ticket.title == "Two")).number
    assert "Suggested: <strong>Zed Agent</strong>" in manager.get(f"/tickets/{second}").text


def test_staff_change_affected_item_and_history(client, manager):
    rita = setup_people(client, manager)
    upload(manager, CSV)
    rita.post("/tickets/new", {"category": "software", "priority": "low", "title": "Payslip", "description": "d"})
    number = ticket()[0]
    payroll = ci("Payroll").id
    r = manager.post(f"/tickets/{number}/update", {"category": "software", "priority": "low", "ci_id": str(payroll)})
    assert "Affected item changed from none to Payroll" in r.text
    assert ticket()[1] == payroll
    assert number in manager.get(f"/cmdb/{payroll}").text
    assert "1 open / 1" in manager.get("/cmdb").text


def test_create_and_edit_item_form(client, manager):
    setup_people(client, manager)
    r = manager.post("/cmdb/new", {"name": "Core switch", "ci_type": "network", "status": "in_use",
                                   "asset_tag": "SW-1", "ip_address": "10.0.0.2", "assigned_to_email": ""})
    assert "Added Core switch" in r.text
    r = manager.post("/cmdb/new", {"name": "Dup", "ci_type": "network", "status": "in_use", "asset_tag": "SW-1"})
    assert r.status_code == 400 and "already used by Core switch" in r.text
    r = manager.post("/cmdb/new", {"name": "X", "ci_type": "laptop", "status": "in_use", "assigned_to_email": "nobody@x.com"})
    assert "No user with email" in r.text

    sw = ci("Core switch")
    r = manager.post(f"/cmdb/{sw.id}", {"name": "Core switch", "ci_type": "network", "status": "maintenance",
                                         "asset_tag": "SW-1", "assigned_to_email": "rita@example.com",
                                         "warranty_expires": "2030-01-31"})
    assert "Saved Core switch" in r.text
    assert ci("Core switch").status.value == "maintenance"


def test_export_round_trip(client, manager):
    setup_people(client, manager)
    upload(manager, CSV)
    csv_text = manager.get("/cmdb/export.csv").text
    assert csv_text.splitlines()[0].startswith("name,type,status,asset_tag")
    assert "LT-001" in csv_text and "rita@example.com" in csv_text
    r = upload(manager, csv_text)
    assert "Created 0, updated 6" in r.text
    assert manager.get("/cmdb/template.csv").status_code == 200
