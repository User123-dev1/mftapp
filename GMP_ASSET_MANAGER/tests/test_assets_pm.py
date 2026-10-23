from fastapi.testclient import TestClient

from tests.conftest import ADMIN_TEMP, PASSWORDS, Api

PUMP = {"description": "Transfer pump", "category": "equipment", "gmp_impact": "none", "location_code": "WS-1"}
BIOREACTOR = {"description": "50 L bioreactor", "category": "equipment", "gmp_impact": "direct", "location_code": "WS-1"}


def add_locations(api):
    api.ok("admin", "post", "/api/locations", status=201, json={"code": "WS-1", "name": "Workshop"})
    api.ok("admin", "post", "/api/locations", status=201, json={"code": "SUITE-2", "name": "Upstream suite 2"})


# ---- Company setup -----------------------------------------------------------

def test_company_setup_required_before_assets(app):
    api = Api(TestClient(app))
    assert api.client.get("/api/company/public").json() == {"configured": False, "name": None, "site_name": None}
    api.login("admin", ADMIN_TEMP)
    api.ok("admin", "post", "/api/auth/change-password", json={"old_password": ADMIN_TEMP, "new_password": PASSWORDS["admin"]})
    api.ok("admin", "post", "/api/users", status=201, json={
        "username": "tech1", "full_name": "Tech One", "role": "technician", "temporary_password": "Temp-Pass-123"})
    api.login("tech1", "Temp-Pass-123")
    api.ok("tech1", "post", "/api/auth/change-password", json={"old_password": "Temp-Pass-123", "new_password": PASSWORDS["tech1"]})

    r = api.call("tech1", "post", "/api/assets", json=PUMP | {"location_code": None})
    assert r.status_code == 409 and "Company setup" in r.json()["detail"]
    assert api.call("tech1", "put", "/api/company", json={"name": "X", "site_name": "Y", "asset_prefix": "X"}).status_code == 403
    assert api.call("admin", "put", "/api/company", json={"name": "X", "site_name": "Y", "asset_prefix": "AC-ME"}).status_code == 422

    api.ok("admin", "put", "/api/company", json={"name": "Acme Biologics", "site_name": "Accra", "asset_prefix": "acme"})
    assert api.client.get("/api/company/public").json()["name"] == "Acme Biologics"
    asset = api.ok("tech1", "post", "/api/assets", status=201, json=PUMP | {"location_code": None})
    assert asset["asset_tag"] == "ACME-000001"


def test_asset_ids_generated_and_prefix_change(api):
    add_locations(api)
    first = api.ok("tech1", "post", "/api/assets", status=201, json=PUMP)
    second = api.ok("tech1", "post", "/api/assets", status=201, json=PUMP)
    assert (first["asset_tag"], second["asset_tag"]) == ("ACME-000001", "ACME-000002")
    legacy = api.ok("tech1", "post", "/api/assets", status=201, json=PUMP | {"asset_tag": "old-pump-7"})
    assert legacy["asset_tag"] == "OLD-PUMP-7"

    r = api.call("admin", "put", "/api/company", json={"name": "Acme Biologics", "site_name": "Accra Plant 1", "asset_prefix": "ACB"})
    assert r.status_code == 422  # changing settings after setup needs a reason
    api.ok("admin", "put", "/api/company", json={"name": "Acme Biologics", "site_name": "Accra Plant 1",
                                                "asset_prefix": "ACB", "reason": "Group naming standard"})
    assert api.ok("tech1", "post", "/api/assets", status=201, json=PUMP)["asset_tag"] == "ACB-000001"
    assert api.ok("tech1", "get", "/api/assets/ACME-000001")["asset_tag"] == "ACME-000001"  # never renumbered

    svg = api.ok("tech1", "get", "/api/assets/ACME-000001/label.svg")
    assert "ACME-000001" in svg and "Acme Biologics" in svg


# ---- Active / inactive ---------------------------------------------------------

def test_active_inactive(api):
    add_locations(api)
    pump = api.ok("tech1", "post", "/api/assets", status=201, json=PUMP | {"initial_state": "active"})
    assert pump["status"] == "in_service" and pump["fitness"]["fit_for_use"]

    r = api.call("tech1", "post", "/api/assets", json=BIOREACTOR | {"initial_state": "active"})
    assert r.status_code == 422 and "released by QA" in r.json()["detail"]

    tag = pump["asset_tag"]
    off = api.ok("op1", "post", f"/api/assets/{tag}/status", json={"status": "out_of_service", "reason": "Not needed this campaign"})
    assert off["status"] == "out_of_service" and not off["fitness"]["fit_for_use"]
    # non-GMP: technician re-activates without QA signature; operator may not
    assert api.call("op1", "post", f"/api/assets/{tag}/status", json={"status": "in_service", "reason": "Back"}).status_code == 403
    on = api.ok("tech1", "post", f"/api/assets/{tag}/status", json={"status": "in_service", "reason": "Campaign restart"})
    assert on["status"] == "in_service"

    bio = api.ok("tech1", "post", "/api/assets", status=201, json=BIOREACTOR)
    assert bio["status"] == "commissioning"
    r = api.call("tech1", "post", f"/api/assets/{bio['asset_tag']}/status", json={"status": "in_service", "reason": "Try"})
    assert r.status_code == 403  # GMP asset needs QA release


# ---- PM schedules and work orders -------------------------------------------------

def test_time_based_pm_generates_and_advances(api, app, fake_clock):
    app.state.settings.password_max_age_days = 3650
    add_locations(api)
    tag = api.ok("tech1", "post", "/api/assets", status=201, json=PUMP | {"initial_state": "active"})["asset_tag"]
    sch = api.ok("tech1", "post", f"/api/assets/{tag}/pm-schedules", status=201, json={
        "title": "Monthly PM", "interval_value": 1, "interval_unit": "months", "lead_days": 7,
        "instructions": "Check seals, lubricate bearings"})
    assert sch["next_due_on"] == "2026-11-15"
    assert api.ok("tech1", "get", f"/api/assets/{tag}")["pm_due_on"] == "2026-11-15"
    assert api.ok("tech1", "get", "/api/work-orders?status=open") == []

    fake_clock.advance(days=24)  # 2026-11-08: inside the 7-day lead window
    for u in ("tech1", "op1", "qa1"):
        api.login(u)
    wos = api.ok("tech1", "get", "/api/work-orders?status=open")
    assert len(wos) == 1 and wos[0]["due_on"] == "2026-11-15" and wos[0]["trigger"] == "schedule"
    assert len(api.ok("tech1", "get", "/api/work-orders?status=open")) == 1  # not duplicated
    assert api.ok("op1", "get", f"/api/assets/{tag}/fitness")["fit_for_use"]  # not yet overdue

    fake_clock.advance(days=10)  # overdue
    for u in ("tech1", "op1"):
        api.login(u)
    fit = api.ok("op1", "get", f"/api/assets/{tag}/fitness")
    assert not fit["fit_for_use"] and "Monthly PM overdue" in fit["reasons"][0]

    r = api.call("tech1", "post", f"/api/work-orders/{wos[0]['number']}/complete",
                 json={"performed_on": "2026-11-18", "notes": "Seals replaced", "signature": {"password": "nope"}})
    assert r.status_code == 401
    done = api.ok("tech1", "post", f"/api/work-orders/{wos[0]['number']}/complete",
                  json={"performed_on": "2026-11-18", "notes": "Seals replaced", "signature": api.sig("tech1")})
    assert done["status"] == "completed" and done["signatures"][0]["meaning"] == "performed"
    asset = api.ok("tech1", "get", f"/api/assets/{tag}")
    assert asset["pm_schedules"][0]["next_due_on"] == "2026-12-18"
    assert asset["last_pm_on"] == "2026-11-18" and asset["fitness"]["fit_for_use"]


def test_event_trigger_blocks_use_until_done(api):
    add_locations(api)
    tag = api.ok("tech1", "post", "/api/assets", status=201, json=PUMP | {"initial_state": "active"})["asset_tag"]
    api.ok("tech1", "post", f"/api/assets/{tag}/pm-schedules", status=201, json={
        "title": "Post-move leak test", "work_type": "inspection", "trigger_type": "event", "event": "asset_moved",
        "interval_value": 2, "interval_unit": "days", "blocks_use": True})
    r = api.call("tech1", "post", f"/api/assets/{tag}/pm-schedules", json={
        "title": "Bad", "trigger_type": "event", "event": "full_moon", "interval_value": 1})
    assert r.status_code == 422

    api.ok("op1", "post", f"/api/assets/{tag}/move", json={"location_code": "SUITE-2"})
    wos = api.ok("op1", "get", f"/api/work-orders?asset={tag}&status=open")
    assert len(wos) == 1 and wos[0]["trigger"] == "event:asset_moved" and wos[0]["due_on"] == "2026-10-17"
    fit = api.ok("op1", "get", f"/api/assets/{tag}/fitness")
    assert not fit["fit_for_use"] and "must be completed before use" in fit["reasons"][0]

    api.ok("tech1", "post", f"/api/work-orders/{wos[0]['number']}/complete",
           json={"performed_on": "2026-10-15", "notes": "Leak test passed", "signature": api.sig("tech1")})
    assert api.ok("op1", "get", f"/api/assets/{tag}/fitness")["fit_for_use"]


def test_manual_work_order_cancel_and_schedule_pause(api):
    add_locations(api)
    tag = api.ok("tech1", "post", "/api/assets", status=201,
                 json=PUMP | {"initial_state": "active", "pm_required": True, "pm_interval_days": 1})["asset_tag"]
    asset = api.ok("tech1", "get", f"/api/assets/{tag}")
    sch = asset["pm_schedules"][0]
    assert sch["title"] == "Preventive maintenance" and sch["next_due_on"] == "2026-10-16"

    wo = api.ok("tech1", "post", f"/api/assets/{tag}/work-orders", status=201, json={
        "work_type": "cleaning", "title": "Deep clean after spill", "due_on": "2026-10-20"})
    assert wo["trigger"] == "manual" and wo["number"].startswith("WO-2026-")
    assert api.call("op1", "post", f"/api/work-orders/{wo['number']}/cancel", json={"reason": "dup"}).status_code == 403
    cancelled = api.ok("qa1", "post", f"/api/work-orders/{wo['number']}/cancel", json={"reason": "Raised in error"})
    assert cancelled["status"] == "cancelled"

    paused = api.ok("tech1", "patch", f"/api/pm-schedules/{sch['id']}", json={"changes": {"active": False}, "reason": "Pump mothballed"})
    assert paused["active"] == 0
    assert api.ok("tech1", "get", f"/api/assets/{tag}")["pm_required"] == 0
    entries = api.ok("qa1", "get", "/api/audit?entity_type=pm_schedule")
    assert any(e["action"] == "PM_SCHEDULE_MODIFIED" and e["reason"] == "Pump mothballed" for e in entries)
    assert api.ok("qa1", "get", "/api/audit/verify")["valid"]


def test_admin_can_register_and_plan_but_not_release(api):
    add_locations(api)
    asset = api.ok("admin", "post", "/api/assets", status=201, json=BIOREACTOR)
    tag = asset["asset_tag"]
    api.ok("admin", "post", f"/api/assets/{tag}/pm-schedules", status=201,
           json={"title": "Quarterly PM", "interval_value": 3, "interval_unit": "months"})
    # admin may not release GMP equipment to service or approve QA records
    r = api.call("admin", "post", f"/api/assets/{tag}/status",
                 json={"status": "in_service", "reason": "Try", "signature": api.sig("admin")})
    assert r.status_code == 403
