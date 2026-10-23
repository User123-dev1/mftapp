import sqlite3

from gmp_assets.barcode import GS

BALANCE = {
    "asset_tag": "bal-0001", "description": "Analytical balance XS205", "category": "instrument",
    "gmp_impact": "direct", "manufacturer": "Mettler Toledo", "model": "XS205", "serial_number": "B123456",
    "gtin": "09501101530003", "location_code": "QC-LAB-1", "calibration_required": True,
    "calibration_interval_days": 180, "pm_required": True, "pm_interval_days": 365,
}
WEIGHTS = {
    "asset_tag": "STD-W-001", "description": "E2 reference weight set", "category": "reference_standard",
    "gmp_impact": "indirect", "is_reference_standard": True, "calibration_required": True,
    "calibration_interval_days": 365, "location_code": "QC-LAB-1",
}


def release(api, tag, performed_on="2026-10-14", standards=(), certificate="EXT-CERT-1"):
    """Calibrate (tech1), approve (qa1), qualify and release an asset."""
    cal = api.ok("tech1", "post", f"/api/assets/{tag}/calibrations", status=201, json={
        "performed_on": performed_on, "as_found": "not_applicable", "as_left": "pass",
        "standards": list(standards), "certificate_ref": certificate if not standards else None,
        "signature": api.sig("tech1")})
    api.ok("qa1", "post", f"/api/calibrations/{cal['id']}/review",
           json={"decision": "approve", "comment": "Reviewed", "signature": api.sig("qa1")})
    api.ok("qa1", "post", f"/api/assets/{tag}/qualification",
           json={"qualification_status": "qualified", "reason": "IQ/OQ/PQ protocols approved", "signature": api.sig("qa1")})
    return api.ok("qa1", "post", f"/api/assets/{tag}/status",
                  json={"status": "in_service", "reason": "Released after qualification", "signature": api.sig("qa1")})


def setup_site(api):
    api.ok("admin", "post", "/api/locations", status=201, json={"code": "QC-LAB-1", "name": "QC Laboratory 1", "area_class": "CNC"})
    api.ok("admin", "post", "/api/locations", status=201, json={"code": "WH-Q", "name": "Quarantine cage"})
    api.ok("tech1", "post", "/api/assets", status=201, json=WEIGHTS)
    api.ok("tech1", "post", "/api/assets", status=201, json=BALANCE)
    release(api, "STD-W-001")


def test_new_asset_is_not_fit_for_use(api):
    setup_site(api)
    scan = api.ok("op1", "post", "/api/scan", json={"data": "BAL-0001\r\n"})
    assert scan["match_type"] == "asset"
    assert not scan["fitness"]["fit_for_use"]
    assert any("commissioning" in r for r in scan["fitness"]["reasons"])
    assert any("No approved calibration" in r for r in scan["fitness"]["reasons"])


def test_full_release_and_scan_paths(api):
    setup_site(api)
    asset = release(api, "BAL-0001", standards=["STD-W-001"])
    assert asset["status"] == "in_service"
    assert asset["calibration_due_on"] == "2027-04-12"
    assert asset["fitness"]["fit_for_use"], asset["fitness"]
    # e-signature manifest: name, meaning, timestamp
    sig = asset["signatures"][-1]
    assert sig["full_name"] == "QA1 Person" and sig["meaning"] == "released" and sig["signed_at"]

    # Plain Code 128 tag, GS1 DataMatrix (GTIN + serial), GS1-128 GIAI all resolve
    for raw in ("]C0BAL-0001", f"]d20109501101530003{GS}21B123456", "]C18004BAL-0001"):
        scan = api.ok("op1", "post", "/api/scan", json={"data": raw})
        assert scan["match_type"] == "asset" and scan["asset"]["asset_tag"] == "BAL-0001", raw
        assert scan["fitness"]["fit_for_use"]

    loc = api.ok("op1", "post", "/api/scan", json={"data": "WH-Q"})
    assert loc["match_type"] == "location"
    gs1 = api.ok("op1", "post", "/api/scan", json={"data": "(01)09501101530003(17)250101(10)LOT9"})
    assert gs1["match_type"] == "gs1_item" and gs1["expired"] is True
    bad = api.ok("op1", "post", "/api/scan", json={"data": "]d20109501101530009"})
    assert bad["match_type"] == "unknown" and not bad["parsed"]["valid"]

    # Scan-driven move
    moved = api.ok("op1", "post", "/api/assets/BAL-0001/move", json={"location_code": "WH-Q"})
    assert moved["location_code"] == "WH-Q"

    svg = api.ok("op1", "get", "/api/assets/BAL-0001/label.svg")
    assert svg.startswith("<svg") and "Cal due: 2027-04-12" in svg


def test_out_of_tolerance_quarantines_and_opens_deviation(api):
    setup_site(api)
    release(api, "BAL-0001", standards=["STD-W-001"])
    cal = api.ok("tech2", "post", "/api/assets/BAL-0001/calibrations", status=201, json={
        "performed_on": "2026-10-15", "as_found": "fail", "as_left": "pass", "standards": ["STD-W-001"],
        "signature": api.sig("tech2")})
    assert cal["deviation_number"].startswith("DEV-2026-")
    asset = api.ok("op1", "get", "/api/assets/BAL-0001")
    assert asset["status"] == "quarantine" and not asset["fitness"]["fit_for_use"]

    # Cannot release while deviation is open
    r = api.call("qa1", "post", "/api/assets/BAL-0001/status",
                 json={"status": "in_service", "reason": "Try release", "signature": api.sig("qa1")})
    assert r.status_code == 409 and "Open deviation" in r.json()["detail"]

    api.ok("qa1", "post", f"/api/calibrations/{cal['id']}/review",
           json={"decision": "approve", "comment": "Adjusted, as-left in tolerance", "signature": api.sig("qa1")})
    api.ok("qa1", "post", f"/api/deviations/{cal['deviation_number']}/close",
           json={"comment": "Impact assessment: no batches affected", "signature": api.sig("qa1")})
    asset = api.ok("qa1", "post", "/api/assets/BAL-0001/status",
                   json={"status": "in_service", "reason": "Deviation closed", "signature": api.sig("qa1")})
    assert asset["fitness"]["fit_for_use"]


def test_segregation_of_duties_and_permissions(api):
    setup_site(api)
    cal = api.ok("tech1", "post", "/api/assets/BAL-0001/calibrations", status=201, json={
        "performed_on": "2026-10-15", "as_found": "pass", "as_left": "pass", "standards": ["STD-W-001"],
        "signature": api.sig("tech1")})
    # technician cannot approve, operator cannot release, admin cannot approve GMP records
    for user in ("tech1", "tech2", "admin", "op1"):
        r = api.call(user, "post", f"/api/calibrations/{cal['id']}/review",
                     json={"decision": "approve", "signature": api.sig(user)})
        assert r.status_code == 403, user
    r = api.call("op1", "post", "/api/assets/BAL-0001/status",
                 json={"status": "in_service", "reason": "x" * 5, "signature": api.sig("op1")})
    assert r.status_code == 403
    r = api.call("op1", "post", "/api/assets", json=BALANCE | {"asset_tag": "X-1"})
    assert r.status_code == 403


def test_qa_cannot_approve_own_work():
    # QA has no calibration.record permission, so self-approval is blocked by role;
    # the explicit performer check is covered at service level.
    from gmp_assets.security import has_permission
    assert not has_permission("qa", "calibration.record")
    assert not has_permission("admin", "calibration.approve")


def test_reference_standard_traceability(api):
    setup_site(api)
    api.ok("tech1", "post", "/api/assets", status=201, json=WEIGHTS | {"asset_tag": "STD-W-002"})
    # STD-W-002 is still commissioning / uncalibrated -> not acceptable as a standard
    r = api.call("tech1", "post", "/api/assets/BAL-0001/calibrations", json={
        "performed_on": "2026-10-15", "as_found": "pass", "as_left": "pass", "standards": ["STD-W-002"],
        "signature": api.sig("tech1")})
    assert r.status_code == 422 and "not in service" in r.json()["detail"]
    # The balance is not a reference standard
    r = api.call("tech1", "post", "/api/assets/STD-W-001/calibrations", json={
        "performed_on": "2026-10-15", "as_found": "pass", "as_left": "pass", "standards": ["BAL-0001"],
        "signature": api.sig("tech1")})
    assert r.status_code == 422
    # Future-dated records rejected
    r = api.call("tech1", "post", "/api/assets/BAL-0001/calibrations", json={
        "performed_on": "2026-10-16", "as_found": "pass", "as_left": "pass", "certificate_ref": "C",
        "signature": api.sig("tech1")})
    assert r.status_code == 422


def test_calibration_expiry_makes_asset_unfit(api, app, fake_clock):
    app.state.settings.password_max_age_days = 365  # isolate from password expiry
    setup_site(api)
    release(api, "BAL-0001", standards=["STD-W-001"])
    fake_clock.advance(days=181)
    api.login("op1")
    fit = api.ok("op1", "get", "/api/assets/BAL-0001/fitness")
    assert not fit["fit_for_use"] and "Calibration overdue" in fit["reasons"][0]
    due = api.ok("op1", "get", "/api/reports/due?days=30")
    assert any(d["asset_tag"] == "BAL-0001" and d["overdue"] for d in due["calibration"])


def test_reason_required_and_optimistic_locking(api):
    setup_site(api)
    r = api.call("tech1", "patch", "/api/assets/BAL-0001", json={"changes": {"model": "XS205DU"}, "reason": ""})
    assert r.status_code == 422
    r = api.call("tech1", "patch", "/api/assets/BAL-0001", json={"changes": {"status": "in_service"}, "reason": "hack"})
    assert r.status_code == 422
    version = api.ok("tech1", "get", "/api/assets/BAL-0001")["version"]
    asset = api.ok("tech1", "patch", "/api/assets/BAL-0001",
                   json={"changes": {"model": "XS205DU"}, "reason": "Nameplate correction", "expected_version": version})
    assert asset["version"] == version + 1
    r = api.call("tech1", "patch", "/api/assets/BAL-0001",
                 json={"changes": {"model": "Other"}, "reason": "Stale edit", "expected_version": version})
    assert r.status_code == 409
    entry = api.ok("qa1", "get", "/api/audit?entity_type=asset&entity_id=BAL-0001")[0]
    assert entry["action"] == "ASSET_MODIFIED" and '"XS205"' in entry["old_value"] and entry["reason"] == "Nameplate correction"


def test_maintenance_updates_pm_and_requalification(api):
    setup_site(api)
    release(api, "BAL-0001", standards=["STD-W-001"])
    asset = api.ok("tech1", "post", "/api/assets/BAL-0001/maintenance", status=201, json={
        "maintenance_type": "corrective", "performed_on": "2026-10-15", "description": "Replaced load cell",
        "requalification_required": True, "signature": api.sig("tech1")})
    assert asset["qualification_status"] == "requalification_required"
    assert not asset["fitness"]["fit_for_use"]


def test_issue_report_takes_asset_out_of_service(api):
    setup_site(api)
    release(api, "BAL-0001", standards=["STD-W-001"])
    dev = api.ok("op1", "post", "/api/assets/BAL-0001/issues", status=201, json={"description": "Display flickering"})
    asset = api.ok("op1", "get", "/api/assets/BAL-0001")
    assert asset["status"] == "out_of_service" and dev["status"] == "open"


def test_signature_failures_lock_account(api):
    setup_site(api)
    for _ in range(4):
        r = api.call("tech1", "post", "/api/assets/BAL-0001/calibrations", json={
            "performed_on": "2026-10-15", "as_found": "pass", "as_left": "pass", "certificate_ref": "C",
            "signature": {"password": "wrong"}})
        assert r.status_code == 401
    r = api.call("tech1", "post", "/api/assets/BAL-0001/calibrations", json={
        "performed_on": "2026-10-15", "as_found": "pass", "as_left": "pass", "certificate_ref": "C",
        "signature": {"password": "wrong"}})
    assert "locked" in r.json()["detail"]
    assert api.call("tech1", "get", "/api/assets").status_code == 401  # session invalidated
    r = api.client.post("/api/auth/login", json={"username": "tech1", "password": api.sig("tech1")["password"]})
    assert r.status_code == 401
    users = api.ok("admin", "get", "/api/users")
    tech1 = next(u for u in users if u["username"] == "tech1")
    api.ok("admin", "patch", f"/api/users/{tech1['id']}", json={"unlock": True, "reason": "Identity verified by phone"})
    api.login("tech1")


def test_session_idle_timeout(api, fake_clock):
    fake_clock.advance(minutes=14)
    assert api.call("op1", "get", "/api/assets").status_code == 200  # activity refreshes the session
    fake_clock.advance(minutes=16)
    assert api.call("op1", "get", "/api/assets").status_code == 401


def test_password_expiry_forces_change(api, fake_clock):
    fake_clock.advance(days=91)
    assert api.login("op1")["must_change_password"]


def test_must_change_password_blocks_access(api):
    api.ok("admin", "post", "/api/users", status=201, json={
        "username": "view1", "full_name": "Viewer One", "role": "viewer", "temporary_password": "Temp-Pass-123"})
    api.login("view1", "Temp-Pass-123")
    assert api.call("view1", "get", "/api/assets").status_code == 403
    r = api.call("view1", "post", "/api/auth/change-password", json={"old_password": "Temp-Pass-123", "new_password": "weak"})
    assert r.status_code == 422
    r = api.call("view1", "post", "/api/auth/change-password",
                 json={"old_password": "Temp-Pass-123", "new_password": "Temp-Pass-123"})
    assert r.status_code == 422  # re-use
    api.ok("view1", "post", "/api/auth/change-password", json={"old_password": "Temp-Pass-123", "new_password": "Viewer-Pass-99"})
    assert api.call("view1", "get", "/api/assets").status_code == 200


def test_audit_trail_append_only_and_tamper_evident(api, app):
    setup_site(api)
    assert api.ok("qa1", "get", "/api/audit/verify")["valid"]
    db_path = app.state.settings.db_path
    conn = sqlite3.connect(db_path)
    for sql in ("UPDATE audit_trail SET reason = 'x' WHERE seq = 3", "DELETE FROM audit_trail WHERE seq = 3",
                "DELETE FROM assets WHERE asset_tag = 'BAL-0001'"):
        try:
            conn.execute(sql)
            raise AssertionError(f"should have been blocked: {sql}")
        except sqlite3.DatabaseError:
            pass
    # A determined attacker drops the trigger and edits history: verification detects it
    conn.execute("DROP TRIGGER audit_trail_no_update")
    conn.execute("UPDATE audit_trail SET username = 'someone_else' WHERE seq = 5")
    conn.commit()
    conn.close()
    result = api.ok("qa1", "get", "/api/audit/verify")
    assert not result["valid"] and result["broken_at_seq"] == 5

    csv_text = api.ok("qa1", "get", "/api/audit/export.csv")
    assert csv_text.startswith("seq,timestamp,username,action")


def test_no_user_id_reuse_and_role_validation(api):
    r = api.call("admin", "post", "/api/users", json={
        "username": "qa1", "full_name": "Dup", "role": "qa", "temporary_password": "Temp-Pass-123"})
    assert r.status_code == 409
    r = api.call("admin", "post", "/api/users", json={
        "username": "boss", "full_name": "Boss", "role": "superuser", "temporary_password": "Temp-Pass-123"})
    assert r.status_code == 422
