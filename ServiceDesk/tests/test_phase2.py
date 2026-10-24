import json
import re
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import database, entra_sync, sso
from app.config import settings
from app.models import Category, CIStatus, CIType, ConfigItem, EmailLog, Priority, Status, SupportGroup, SyncRun, Ticket, User

from .fake_graph import FakeGraph, device
from .test_cmdb import upload
from .test_workflow import make_agent, register

TENANT = "tenant-123"


@pytest.fixture()
def graph(client, monkeypatch):
    server = FakeGraph(TENANT, "app-id", "app-secret").start()
    monkeypatch.setattr(settings, "entra_authority", server.base)
    monkeypatch.setattr(settings, "graph_base_url", server.base)
    monkeypatch.setattr(settings, "entra_tenant_id", TENANT)
    monkeypatch.setattr(settings, "entra_client_id", "app-id")
    monkeypatch.setattr(settings, "entra_client_secret", "app-secret")
    yield server
    server.stop()
    monkeypatch.undo()
    sso.reset()


def items():
    with database.SessionLocal() as db:
        return {c.name: c for c in db.scalars(select(ConfigItem))}


def owner_email(name):
    with database.SessionLocal() as db:
        ci = db.scalar(select(ConfigItem).where(ConfigItem.name == name))
        return ci.assigned_user.email if ci.assigned_user else None


# --- Entra device sync --------------------------------------------------------------

def test_sync_creates_links_and_updates(client, manager, graph):
    register(client, "Rita", "rita@example.com")
    # An item already imported from CSV with vendor data, same name as the Entra device.
    upload(manager, "name,type,asset_tag,manufacturer,model,location,support_group\n"
                    "RITA-LT01,laptop,LT-1,Dell,Latitude 7440,HQ,Desktop Support\n")
    graph.devices = [
        device("o1", "RITA-LT01", owner="rita@example.com", manufacturer="Dell Inc.", model="Unknown"),
        device("o2", "BOB-PC", owner="bob@example.com", manufacturer="HP", model="EliteDesk 800"),
        device("o3", "Rita-iPhone", os="iOS", version="18.1", owner="rita@example.com"),
        device("o4", "Personal-Mac", os="MacOS", trust="Workplace"),
        device("o5", "OLD-PC", enabled=False, last_sign_in="2026-01-02T00:00:00Z"),
    ]
    run = entra_sync.run_sync("manual")
    assert run.status == "success", run.error
    summary = json.loads(run.summary)
    assert summary["seen"] == 5 and summary["created"] == 3 and summary["linked"] == 1
    assert summary["skipped_registered"] == 1
    assert len(graph.requests) == 3  # paged 2 + 2 + 1

    ci = items()
    assert "Personal-Mac" not in ci
    lt = ci["RITA-LT01"]
    assert lt.entra_object_id == "o1" and lt.asset_tag == "LT-1" and lt.location == "HQ"
    assert lt.manufacturer == "Dell" and lt.model == "Latitude 7440"  # CSV data kept
    assert lt.version == "Windows 10.0.22631"
    assert ci["Rita-iPhone"].ci_type == CIType.MOBILE
    assert ci["BOB-PC"].ci_type == CIType.LAPTOP and ci["BOB-PC"].model == "EliteDesk 800"
    assert ci["OLD-PC"].status == CIStatus.MAINTENANCE
    assert owner_email("RITA-LT01") == "rita@example.com"
    assert owner_email("BOB-PC") is None  # no account and no SSO -> warning
    assert any("bob@example.com has no account" in w for w in summary["warnings"])

    # Synced devices appear under the requester's My devices.
    rita = client().login("rita@example.com", "Password1!")
    portal = rita.get("/").text
    assert "RITA-LT01" in portal and "Rita-iPhone" in portal

    # Second run: rename + new OS version updates in place, nothing duplicated.
    graph.devices[0] = device("o1", "RITA-LT01-NEW", version="10.0.26100", owner="rita@example.com")
    run = entra_sync.run_sync("manual")
    summary = json.loads(run.summary)
    assert summary["created"] == 0 and summary["updated"] == 4
    ci = items()
    assert "RITA-LT01" not in ci and ci["RITA-LT01-NEW"].version == "Windows 10.0.26100"
    assert len(ci) == 4


def test_sync_missing_devices_reported_or_retired(client, manager, graph, monkeypatch):
    graph.devices = [device("o1", "PC-1"), device("o2", "PC-2")]
    entra_sync.run_sync("manual")
    graph.devices = [device("o1", "PC-1")]
    summary = json.loads(entra_sync.run_sync("manual").summary)
    assert summary["missing"] == 1 and items()["PC-2"].status == CIStatus.IN_USE

    monkeypatch.setattr(settings, "entra_sync_retire_missing", True)
    summary = json.loads(entra_sync.run_sync("manual").summary)
    assert summary["retired"] == 1 and items()["PC-2"].status == CIStatus.RETIRED

    # A device someone retired by hand that's still in Entra is not un-retired.
    with database.SessionLocal() as db:
        db.scalar(select(ConfigItem).where(ConfigItem.name == "PC-1")).status = CIStatus.RETIRED
        db.commit()
    entra_sync.run_sync("manual")
    assert items()["PC-1"].status == CIStatus.RETIRED


def test_sync_creates_sso_placeholder_owner(client, manager, graph, monkeypatch):
    monkeypatch.setattr(settings, "entra_tenant_id", TENANT)
    monkeypatch.setattr(settings, "entra_client_id", "app-id")
    sso.reset()  # Entra SSO now "configured" (metadata is never fetched in this test)
    graph.devices = [device("o1", "NEW-HIRE-LT", owner="new.hire@example.com")]
    summary = json.loads(entra_sync.run_sync("manual").summary)
    assert summary["users_created"] == 1
    with database.SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "new.hire@example.com"))
        assert user.full_name == "New.Hire" and not user.has_password


def test_sync_errors_are_recorded(client, manager, graph, monkeypatch):
    graph.fail_with = 403
    run = entra_sync.run_sync("manual")
    assert run.status == "failed" and "Device.Read.All" in run.error

    graph.fail_with = None
    monkeypatch.setattr(settings, "entra_client_secret", "wrong")
    run = entra_sync.run_sync("manual")
    assert run.status == "failed" and "Invalid client secret" in run.error

    monkeypatch.setattr(settings, "entra_client_secret", "")
    run = entra_sync.run_sync("manual")
    assert run.status == "failed" and "ENTRA_CLIENT_SECRET" in run.error

    monkeypatch.setattr(settings, "entra_client_secret", "app-secret")
    monkeypatch.setattr(settings, "entra_authority", "http://127.0.0.1:1")  # nothing listening
    run = entra_sync.run_sync("manual")
    assert run.status == "failed" and "Could not reach Microsoft" in run.error and "127.0.0.1" in run.error


def test_sync_retries_when_throttled(client, manager, graph):
    graph.throttle_once = True
    graph.devices = [device("o1", "PC-1")]
    run = entra_sync.run_sync("manual")
    assert run.status == "success" and "PC-1" in items()


def test_sync_page_and_button(client, manager, graph):
    graph.devices = [device("o1", "PC-1"), device("o2", "PC-2"), device("o3", "PC-3")]
    page = manager.get("/cmdb/sync").text
    assert "Sync now" in page and "No syncs yet" in page
    r = manager.post("/cmdb/sync")
    assert "Sync started" in r.text
    page = manager.get("/cmdb/sync").text
    assert "3 devices in Entra, 3 created" in page and "success" in page
    assert ">Entra</span>" in manager.get("/cmdb").text

    agent_id = make_agent(manager)
    agent = client().login("agent@example.com", "Password1!")
    assert agent.get("/cmdb/sync").status_code == 403
    assert agent.post("/cmdb/sync").status_code == 403


def test_scheduler_next_run():
    settings_hour = settings.entra_sync_hour
    try:
        settings.entra_sync_hour = 2
        s = entra_sync.DailyScheduler
        assert s.seconds_until_next(datetime(2026, 1, 1, 1, 0)) == 3600
        assert s.seconds_until_next(datetime(2026, 1, 1, 3, 0)) == 23 * 3600
    finally:
        settings.entra_sync_hour = settings_hour


# --- Auto-assignment ------------------------------------------------------------------

def _group_setup(client, manager, auto):
    rita = register(client, "Rita", "rita@example.com")
    alice = make_agent(manager, "alice@example.com")
    upload(manager, "name,type,asset_tag,assigned_to_email,support_group\n"
                    "RITA-LT01,laptop,LT-1,rita@example.com,Desktop Support\n"
                    "Payroll,application,,,Business Apps\n")
    with database.SessionLocal() as db:
        gid = db.scalar(select(SupportGroup).where(SupportGroup.name == "Desktop Support")).id
    data = {"csrf_token": manager.csrf(), "name": "Desktop Support", "member_ids": [str(alice)]}
    if auto:
        data["auto_assign"] = "true"
    r = manager.client.post(f"/admin/groups/{gid}", data=data)
    assert r.status_code == 200
    return rita, alice


def _ci_id(name):
    with database.SessionLocal() as db:
        return str(db.scalar(select(ConfigItem).where(ConfigItem.name == name)).id)


def test_auto_assign_on(client, manager):
    rita, alice = _group_setup(client, manager, auto=True)
    rita.post("/tickets/new", {"category": "hardware", "priority": "high", "title": "Fan noise",
                               "description": "loud", "ci_id": _ci_id("RITA-LT01")})
    with database.SessionLocal() as db:
        t = db.scalars(select(Ticket)).one()
        assert t.status == Status.ASSIGNED and t.assignee_id == alice and t.assigned_by_id is None
        subjects = {(e.to_address, e.subject) for e in db.scalars(select(EmailLog))}
    assert any(to == "alice@example.com" and "Assigned to you" in s for to, s in subjects)
    assert any(to == "manager@example.com" and "auto-assigned to Alice Agent" in s for to, s in subjects)
    assert not any("awaiting assignment" in s for _, s in subjects)
    page = manager.get(f"/tickets/{t.number}").text
    assert "Auto-assignment" in page and "Auto-assigned to Alice Agent (Desktop Support)" in page

    # Items in a group without auto-assign still wait for the manager.
    rita.post("/tickets/new", {"category": "software", "priority": "low", "title": "Payslip",
                               "description": "x", "ci_id": _ci_id("Payroll")})
    with database.SessionLocal() as db:
        assert db.scalar(select(Ticket).where(Ticket.title == "Payslip")).status == Status.NEW


def test_auto_assign_off_by_default(client, manager):
    rita, _ = _group_setup(client, manager, auto=False)
    rita.post("/tickets/new", {"category": "hardware", "priority": "high", "title": "Fan noise",
                               "description": "loud", "ci_id": _ci_id("RITA-LT01")})
    with database.SessionLocal() as db:
        assert db.scalars(select(Ticket)).one().status == Status.NEW


def test_auto_assign_without_members_falls_back(client, manager):
    register(client, "Rita", "rita@example.com")
    upload(manager, "name,type,assigned_to_email,support_group\nPC,laptop,rita@example.com,Empty Team\n")
    with database.SessionLocal() as db:
        gid = db.scalar(select(SupportGroup)).id
    r = manager.client.post(f"/admin/groups/{gid}", data={"csrf_token": manager.csrf(), "name": "Empty Team",
                                                           "auto_assign": "true"})
    assert "has no members" in r.text
    rita = client().login("rita@example.com", "Password1!")
    rita.post("/tickets/new", {"category": "hardware", "priority": "low", "title": "x", "description": "y",
                               "ci_id": _ci_id("PC")})
    with database.SessionLocal() as db:
        assert db.scalars(select(Ticket)).one().status == Status.NEW


# --- Reports ----------------------------------------------------------------------------

def test_reports(client, manager):
    rita, alice = _group_setup(client, manager, auto=True)
    laptop = _ci_id("RITA-LT01")
    for i in range(3):
        rita.post("/tickets/new", {"category": "incident" if i else "hardware", "priority": "high",
                                   "title": f"Issue {i}", "description": "d", "ci_id": laptop})
    agent = client().login("alice@example.com", "Password1!")
    with database.SessionLocal() as db:
        first = db.scalar(select(Ticket).where(Ticket.title == "Issue 0"))
        number = first.number
    agent.post(f"/tickets/{number}/status", {"status": "resolved", "resolution_notes": "Replaced fan"})
    with database.SessionLocal() as db:
        t = db.scalar(select(Ticket).where(Ticket.number == number))
        t.created_at = t.resolved_at - timedelta(hours=5)
        ci = db.get(ConfigItem, int(laptop))
        ci.warranty_expires = date.today() - timedelta(days=1)
        ci.entra_last_sign_in = datetime.now() - timedelta(days=120)
        db.commit()

    page = manager.get("/reports?days=30").text
    assert re.search(r'<span class="num">3</span><span>Tickets opened', page)
    assert re.search(r'<span class="num">5\.0 h</span><span>Avg time to resolve', page)
    assert re.search(r'RITA-LT01 \[LT-1\]</a></td>\s*<td>Laptop</td>\s*<td>Rita</td>\s*<td><strong>3</strong></td>\s*<td>2</td>', page)
    assert "Desktop Support" in page and "Alice Agent" in page
    assert "(expired)" in page and "last sign-in" in page
    assert manager.get("/reports?days=999").status_code == 200  # falls back to 30
    assert rita.get("/reports").status_code == 403


def test_agent_can_work_ticket_in_same_transaction(client, manager):
    """Assign + status change before the session flushes (as auto-assignment and scripts do)."""
    from app import services

    register(client, "Rita", "rita@example.com")
    make_agent(manager)
    with database.SessionLocal() as db:
        rita = db.scalar(select(User).where(User.email == "rita@example.com"))
        agent = db.scalar(select(User).where(User.email == "agent@example.com"))
        boss = db.scalar(select(User).where(User.email == "manager@example.com"))
        t = services.create_ticket(db, rita, "x", "y", Category.GENERAL, Priority.LOW)
        services.assign_ticket(db, t, agent, boss)
        services.change_status(db, t, Status.IN_PROGRESS, agent)
        assert t.status == Status.IN_PROGRESS
