import json

import pytest

from gmp_assets import directory, notifications
from tests.conftest import PASSWORDS

PUMP = {"description": "Transfer pump", "category": "equipment", "gmp_impact": "none"}
READER = {"description": "Plate reader", "category": "instrument", "gmp_impact": "direct"}


def ids(api):
    users = api.ok("admin", "get", "/api/users")
    return {u["username"]: u["id"] for u in users}


@pytest.fixture
def people(api):
    """Second QA user, e-mail addresses on everyone."""
    api.ok("admin", "post", "/api/users", status=201, json={
        "username": "qa2", "full_name": "QA Two", "role": "qa", "temporary_password": "Temp-Pass-123",
        "email": "qa2@abelzeta.com"})
    api.login("qa2", "Temp-Pass-123")
    PASSWORDS["qa2"] = "Quality2-Pw-2026"
    api.ok("qa2", "post", "/api/auth/change-password", json={"old_password": "Temp-Pass-123", "new_password": PASSWORDS["qa2"]})
    uid = ids(api)
    for name in ("qa1", "tech1", "tech2"):
        api.ok("admin", "patch", f"/api/users/{uid[name]}", json={"email": f"{name}@AbelZeta.com", "reason": "Add e-mail"})
    return ids(api)


def test_induction_approval_activates_asset(api, people):
    tag = api.ok("tech1", "post", "/api/assets", status=201, json=READER)["asset_tag"]
    # GMP equipment cannot be activated directly any more
    r = api.call("qa1", "post", f"/api/assets/{tag}/status",
                 json={"status": "in_service", "reason": "x" * 5, "signature": api.sig("qa1")})
    assert r.status_code == 409 and "induction approval" in r.json()["detail"]

    assert api.call("tech1", "post", f"/api/assets/{tag}/induction", json={"approver_id": people["tech2"]}).status_code == 422
    req = api.ok("tech1", "post", f"/api/assets/{tag}/induction", status=201,
                 json={"approver_id": people["qa1"], "comment": "IQ/OQ executed, see VAL-123"})
    assert req["status"] == "pending" and req["approver_email"] == "qa1@abelzeta.com"
    assert api.call("tech1", "post", f"/api/assets/{tag}/induction", json={"approver_id": people["qa2"]}).status_code == 409

    # appears in the approver's task list, and an e-mail was queued
    tasks = api.ok("qa1", "get", "/api/my-tasks")
    assert [a["number"] for a in tasks["approvals"]] == [req["number"]]
    outbox = api.ok("admin", "get", "/api/notifications")
    assert outbox[0]["email"] == "qa1@abelzeta.com" and req["number"] in outbox[0]["subject"]

    # only the named approver may decide
    r = api.call("qa2", "post", f"/api/approvals/{req['number']}/decide",
                 json={"decision": "approve", "signature": api.sig("qa2")})
    assert r.status_code == 403
    r = api.call("qa1", "post", f"/api/approvals/{req['number']}/decide", json={"decision": "reject", "signature": api.sig("qa1")})
    assert r.status_code == 422  # rejection needs a comment
    rejected = api.ok("qa1", "post", f"/api/approvals/{req['number']}/decide",
                      json={"decision": "reject", "comment": "OQ report unsigned", "signature": api.sig("qa1")})
    assert rejected["status"] == "rejected" and rejected["signatures"][0]["meaning"] == "rejected"
    assert api.ok("tech1", "get", f"/api/assets/{tag}")["status"] == "commissioning"

    req2 = api.ok("tech1", "post", f"/api/assets/{tag}/induction", status=201, json={"approver_id": people["qa1"]})
    reassigned = api.ok("tech1", "post", f"/api/approvals/{req2['number']}/reassign",
                        json={"approver_id": people["qa2"], "reason": "qa1 on leave"})
    assert reassigned["approver_name"] == "QA Two"
    done = api.ok("qa2", "post", f"/api/approvals/{req2['number']}/decide",
                  json={"decision": "approve", "comment": "Documents complete", "signature": api.sig("qa2")})
    assert done["status"] == "approved"
    asset = api.ok("tech1", "get", f"/api/assets/{tag}")
    assert asset["status"] == "in_service" and asset["qualification_status"] == "qualified"
    assert asset["fitness"]["fit_for_use"] and asset["signatures"][-1]["meaning"] == "released"
    assert [a["status"] for a in asset["approvals"]] == ["approved", "rejected"]


def test_induction_blocked_until_calibrated(api, people):
    tag = api.ok("tech1", "post", "/api/assets", status=201,
                 json=READER | {"calibration_required": True, "calibration_interval_days": 365})["asset_tag"]
    req = api.ok("tech1", "post", f"/api/assets/{tag}/induction", status=201, json={"approver_id": people["qa1"]})
    assert any("calibration" in b.lower() for b in req["blockers"])
    r = api.call("qa1", "post", f"/api/approvals/{req['number']}/decide",
                 json={"decision": "approve", "signature": api.sig("qa1")})
    assert r.status_code == 409 and "Cannot approve yet" in r.json()["detail"]
    api.ok("tech1", "post", f"/api/approvals/{req['number']}/cancel", json={"reason": "Calibrate first"})


def test_work_order_assignment(api, people):
    tag = api.ok("tech1", "post", "/api/assets", status=201, json=PUMP | {"initial_state": "active"})["asset_tag"]
    r = api.call("tech1", "post", f"/api/assets/{tag}/work-orders",
                 json={"title": "Replace belt", "due_on": "2026-10-20", "assigned_to": people["op1"]})
    assert r.status_code == 422  # operators do not perform maintenance
    wo = api.ok("tech1", "post", f"/api/assets/{tag}/work-orders", status=201,
                json={"title": "Replace belt", "due_on": "2026-10-20", "assigned_to": people["tech2"]})
    assert wo["assignee_name"] == "TECH2 Person"
    assert [w["number"] for w in api.ok("tech2", "get", "/api/my-tasks")["work_orders"]] == [wo["number"]]

    body = {"performed_on": "2026-10-15", "notes": "Belt replaced", "signature": api.sig("tech1")}
    r = api.call("tech1", "post", f"/api/work-orders/{wo['number']}/complete", json=body)
    assert r.status_code == 403 and "assigned to TECH2 Person" in r.json()["detail"]
    api.ok("tech1", "post", f"/api/work-orders/{wo['number']}/assign", json={"assignee_id": people["tech1"], "reason": "tech2 off"})
    assert api.ok("tech1", "post", f"/api/work-orders/{wo['number']}/complete", json=body)["status"] == "completed"


def test_work_order_qa_approval_and_rework(api, people):
    tag = api.ok("tech1", "post", "/api/assets", status=201, json=PUMP | {"initial_state": "active"})["asset_tag"]
    api.ok("tech1", "post", f"/api/assets/{tag}/pm-schedules", status=201, json={
        "title": "Post-move leak test", "trigger_type": "event", "event": "asset_moved", "interval_value": 1,
        "default_assignee": people["tech2"], "requires_approval": True})
    api.ok("admin", "post", "/api/locations", status=201, json={"code": "SUITE-9", "name": "Suite 9"})
    api.ok("tech1", "post", f"/api/assets/{tag}/move", json={"location_code": "SUITE-9"})
    wo = api.ok("tech2", "get", "/api/my-tasks")["work_orders"][0]
    assert wo["requires_approval"] == 1 and wo["assigned_to"] == people["tech2"]

    body = {"performed_on": "2026-10-15", "notes": "No leaks", "signature": api.sig("tech2")}
    assert api.call("tech2", "post", f"/api/work-orders/{wo['number']}/complete", json=body).status_code == 422
    done = api.ok("tech2", "post", f"/api/work-orders/{wo['number']}/complete", json=body | {"approver_id": people["qa1"]})
    assert done["status"] == "completed" and done["approval_state"] == "pending"
    fit = api.ok("op1", "get", f"/api/assets/{tag}/fitness")
    assert not fit["fit_for_use"]  # still blocked until QA approves

    apr = done["approval"]["number"]
    api.ok("qa1", "post", f"/api/approvals/{apr}/decide",
           json={"decision": "reject", "comment": "Attach the leak test print-out", "signature": api.sig("qa1")})
    reopened = api.ok("tech2", "get", f"/api/work-orders?asset={tag}&status=open")[0]
    assert reopened["approval_state"] == "rejected"

    done = api.ok("tech2", "post", f"/api/work-orders/{wo['number']}/complete",
                  json=body | {"notes": "No leaks, print-out attached", "approver_id": people["qa1"]})
    api.ok("qa1", "post", f"/api/approvals/{done['approval']['number']}/decide",
           json={"decision": "approve", "signature": api.sig("qa1")})
    assert api.ok("op1", "get", f"/api/assets/{tag}/fitness")["fit_for_use"]
    assert api.ok("qa1", "get", "/api/audit/verify")["valid"]


def test_email_delivery_via_smtp(api, app, people, monkeypatch):
    api.ok("tech1", "post", f"/api/assets", status=201, json=READER)
    tag = api.ok("tech1", "get", "/api/assets")[0]["asset_tag"]
    api.ok("tech1", "post", f"/api/assets/{tag}/induction", status=201, json={"approver_id": people["qa1"]})
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout): sent.append(("connect", host, port))
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def has_extn(self, name): return True
        def starttls(self): sent.append(("starttls",))
        def login(self, u, p): sent.append(("login", u))
        def send_message(self, msg): sent.append(("send", msg["To"], msg["Subject"]))

    monkeypatch.setattr(notifications.smtplib, "SMTP", FakeSMTP)
    settings = app.state.settings
    settings.smtp_host, settings.smtp_user, settings.smtp_from = "smtp.office365.com", "gmp@abelzeta.com", "gmp@abelzeta.com"
    assert notifications.send_pending(app.state.service.db, settings) == 1
    assert ("starttls",) in sent and sent[-1][1] == "qa1@abelzeta.com"
    assert api.ok("admin", "get", "/api/notifications")[0]["sent_at"]
    assert notifications.send_pending(app.state.service.db, settings) == 0  # not sent twice


def test_directory_csv_and_m365(api, app, monkeypatch):
    csv_text = ("Display name,Department,Title,User principal name\n"
                "Ama Owusu,Quality Assurance,QA Manager,ama.owusu@abelzeta.com\n,,,\n")
    r = api.client.post("/api/directory/csv", content=csv_text, headers={
        "Authorization": f"Bearer {api.tokens['admin']}", "Content-Type": "text/plain"})
    assert r.status_code == 200 and r.json() == [{"full_name": "Ama Owusu", "email": "ama.owusu@abelzeta.com",
                                                  "username": "ama.owusu", "department": "Quality Assurance",
                                                  "job_title": "QA Manager"}]
    assert api.ok("admin", "get", "/api/directory/status") == {"m365_configured": False, "email_configured": False}
    assert api.call("admin", "get", "/api/directory/search?q=ama").status_code == 400

    s = app.state.settings
    s.m365_tenant_id, s.m365_client_id, s.m365_client_secret = "tenant", "client", "secret"
    calls = []

    class Resp:
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(self.payload).encode()

    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        if "oauth2" in req.full_url:
            return Resp({"access_token": "tok", "expires_in": 3600})
        assert req.headers["Authorization"] == "Bearer tok"
        return Resp({"value": [{"displayName": "Kofi Asante", "mail": "kofi.asante@abelzeta.com",
                                "userPrincipalName": "kofi.asante@abelzeta.com", "department": "Engineering",
                                "jobTitle": "Technician", "accountEnabled": True}]})

    monkeypatch.setattr(directory.urllib.request, "urlopen", fake_urlopen)
    directory._token_cache.clear()
    found = api.ok("admin", "get", "/api/directory/search?q=kofi")
    assert found[0]["username"] == "kofi.asante" and found[0]["email"] == "kofi.asante@abelzeta.com"
    assert "login.microsoftonline.com/tenant" in calls[0] and "graph.microsoft.com" in calls[1]
    assert api.call("tech1", "get", "/api/directory/search?q=kofi").status_code == 403
