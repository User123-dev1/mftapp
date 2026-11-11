import sqlite3

import pyotp

from conftest import USER_PASSWORD, login

AD_TEMPLATE = 1  # first built-in template: Active Directory Account


def _folder(client, admin, name="IT", parent_id=None):
    r = client.post("/api/folders", headers=admin, json={"name": name, "parent_id": parent_id})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _secret(client, headers, folder_id, name="svc-backup", settings=None, **fields):
    body = {"name": name, "folder_id": folder_id, "template_id": AD_TEMPLATE,
            "fields": {"domain": "corp.example.com", "username": "svc-backup", **fields},
            "generate_password": "password" not in fields, "settings": settings or {}}
    r = client.post("/api/secrets", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _grant_folder(client, admin, folder_id, entries):
    r = client.put(f"/api/folders/{folder_id}/permissions", headers=admin,
                   json={"inherit_permissions": False, "entries": entries})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------- auth

def test_login_lockout_and_audit(client, admin, make_user):
    make_user("alice")
    for _ in range(3):
        r = client.post("/api/auth/login", json={"username": "alice", "password": "nope"})
        assert r.status_code == 401
    r = client.post("/api/auth/login", json={"username": "alice", "password": USER_PASSWORD})
    assert r.status_code == 423  # locked even with the right password

    failures = client.get("/api/audit", headers=admin, params={"action": "LOGIN_FAILED"}).json()
    assert len(failures) == 4


def test_unauthenticated_and_logout(client, admin):
    assert client.get("/api/secrets").status_code == 401
    assert client.post("/api/auth/logout", headers=admin).status_code == 200
    assert client.get("/api/secrets", headers=admin).status_code == 401


def test_totp_mfa(client, make_user):
    _, alice = make_user("alice")
    enroll = client.post("/api/auth/mfa/enroll", headers=alice).json()
    totp = pyotp.TOTP(enroll["secret"])
    assert client.post("/api/auth/mfa/confirm", headers=alice,
                       json={"code": "000000" if totp.now() != "000000" else "111111"}
                       ).status_code == 400
    assert client.post("/api/auth/mfa/confirm", headers=alice,
                       json={"code": totp.now()}).status_code == 200

    step1 = client.post("/api/auth/login",
                        json={"username": "alice", "password": USER_PASSWORD}).json()
    assert step1["mfa_required"] and "access_token" not in step1
    r = client.post("/api/auth/mfa/verify",
                    json={"mfa_token": step1["mfa_token"], "code": totp.now()})
    assert r.status_code == 200 and "access_token" in r.json()
    # MFA token is single-use
    r = client.post("/api/auth/mfa/verify",
                    json={"mfa_token": step1["mfa_token"], "code": totp.now()})
    assert r.status_code == 401


# ---------------------------------------------------------------------- permissions

def test_folder_inheritance_and_secret_acls(client, admin, make_user):
    alice_id, alice = make_user("alice")
    bob_id, bob = make_user("bob")
    group = client.post("/api/groups", headers=admin, json={"name": "Server Admins"}).json()
    client.post(f"/api/groups/{group['id']}/members", headers=admin, json={"user_id": bob_id})

    root = _folder(client, admin, "IT")
    child = _folder(client, admin, "Servers", parent_id=root)
    _grant_folder(client, admin, root, [
        {"principal_type": "user", "principal_id": alice_id, "level": "edit"},
        {"principal_type": "group", "principal_id": group["id"], "level": "view"},
    ])

    # alice (Edit inherited from root) can create in the child folder
    sid = _secret(client, alice, child)
    # bob (View via group) can read but not edit
    assert client.post(f"/api/secrets/{sid}/reveal", headers=bob, json={}).status_code == 200
    assert client.patch(f"/api/secrets/{sid}", headers=bob, json={"name": "x"}).status_code == 403
    # alice is not Owner, so cannot change permissions
    assert client.get(f"/api/secrets/{sid}/permissions", headers=alice).status_code == 403

    # Breaking inheritance on the secret removes bob's access entirely (404, not 403)
    r = client.put(f"/api/secrets/{sid}/permissions", headers=admin, json={
        "inherit_permissions": False,
        "entries": [{"principal_type": "user", "principal_id": alice_id, "level": "owner"}]})
    assert r.status_code == 200
    assert client.get(f"/api/secrets/{sid}", headers=bob).status_code == 404
    assert client.get("/api/secrets", headers=bob).json() == []
    assert client.get(f"/api/secrets/{sid}/permissions", headers=alice).status_code == 200


def test_secret_values_encrypted_at_rest(client, admin, settings):
    folder = _folder(client, admin)
    sid = _secret(client, admin, folder, password="Sup3r-Secret-Value!")
    with sqlite3.connect(settings.db_path) as raw:
        dump = "\n".join(raw.iterdump())
    assert "Sup3r-Secret-Value!" not in dump
    values = client.post(f"/api/secrets/{sid}/reveal", headers=admin, json={}).json()
    assert values["password"] == "Sup3r-Secret-Value!"


def test_require_comment(client, admin):
    folder = _folder(client, admin)
    sid = _secret(client, admin, folder, settings={"require_comment": True})
    assert client.post(f"/api/secrets/{sid}/reveal", headers=admin, json={}).status_code == 428
    r = client.post(f"/api/secrets/{sid}/reveal", headers=admin,
                    json={"comment": "INC-1234 restore", "fields": ["password"]})
    assert r.status_code == 200 and list(r.json()) == ["password"]
    views = client.get(f"/api/secrets/{sid}/audit", headers=admin).json()
    assert any(v["action"] == "SECRET_VIEW" and "INC-1234" in v["details"] for v in views)


# ---------------------------------------------------------------------- check-out / rotation

def test_checkout_is_exclusive_and_rotates_on_checkin(client, admin, make_user):
    alice_id, alice = make_user("alice")
    bob_id, bob = make_user("bob")
    folder = _folder(client, admin)
    _grant_folder(client, admin, folder, [
        {"principal_type": "user", "principal_id": alice_id, "level": "view"},
        {"principal_type": "user", "principal_id": bob_id, "level": "view"}])
    sid = _secret(client, admin, folder, settings={"require_checkout": True,
                                                   "rotate_on_checkin": True})

    assert client.post(f"/api/secrets/{sid}/reveal", headers=alice, json={}).status_code == 409
    assert client.post(f"/api/secrets/{sid}/check-out", headers=alice, json={}).status_code == 200
    assert client.post(f"/api/secrets/{sid}/check-out", headers=bob, json={}).status_code == 409
    before = client.post(f"/api/secrets/{sid}/reveal", headers=alice, json={}).json()["password"]
    assert client.post(f"/api/secrets/{sid}/reveal", headers=bob, json={}).status_code == 409

    r = client.post(f"/api/secrets/{sid}/check-in", headers=alice)
    assert r.json() == {"checked_in": True, "rotated": True}
    client.post(f"/api/secrets/{sid}/check-out", headers=bob, json={})
    after = client.post(f"/api/secrets/{sid}/reveal", headers=bob, json={}).json()["password"]
    assert after != before

    history = client.get(f"/api/secrets/{sid}/history", headers=admin).json()
    assert history[0]["slug"] == "password" and history[0]["value"] == before


def test_manual_rotation_requires_edit(client, admin, make_user):
    alice_id, alice = make_user("alice")
    folder = _folder(client, admin)
    _grant_folder(client, admin, folder, [
        {"principal_type": "user", "principal_id": alice_id, "level": "view"}])
    sid = _secret(client, admin, folder)
    assert client.post(f"/api/secrets/{sid}/rotate", headers=alice).status_code == 403
    assert client.post(f"/api/secrets/{sid}/rotate", headers=admin).status_code == 200


# ---------------------------------------------------------------------- approval workflow

def test_access_request_workflow(client, admin, make_user):
    alice_id, alice = make_user("alice")
    bob_id, bob = make_user("bob")
    approvers = client.post("/api/groups", headers=admin, json={"name": "Approvers"}).json()
    client.post(f"/api/groups/{approvers['id']}/members", headers=admin, json={"user_id": bob_id})
    folder = _folder(client, admin)
    _grant_folder(client, admin, folder, [
        {"principal_type": "user", "principal_id": alice_id, "level": "view"},
        {"principal_type": "user", "principal_id": bob_id, "level": "view"}])
    sid = _secret(client, admin, folder, settings={"require_approval": True,
                                                   "approver_group_id": approvers["id"]})

    # alice can see the secret exists but cannot open it
    meta = client.get(f"/api/secrets/{sid}", headers=alice).json()
    assert meta["access_granted"] is False
    assert client.post(f"/api/secrets/{sid}/reveal", headers=alice, json={}).status_code == 403

    req = client.post(f"/api/secrets/{sid}/access-requests", headers=alice,
                      json={"reason": "patching", "minutes": 30}).json()
    assert client.post(f"/api/access-requests/{req['id']}/approve", headers=alice,
                       json={}).status_code == 403  # cannot approve own request
    pending = client.get("/api/access-requests", headers=bob).json()
    assert [r["id"] for r in pending] == [req["id"]]
    r = client.post(f"/api/access-requests/{req['id']}/approve", headers=bob, json={"note": "ok"})
    assert r.json()["status"] == "approved"
    assert client.post(f"/api/secrets/{sid}/reveal", headers=alice, json={}).status_code == 200


# ---------------------------------------------------------------------- audit

def test_audit_chain_detects_tampering(client, admin, settings):
    folder = _folder(client, admin)
    _secret(client, admin, folder)
    assert client.get("/api/audit/verify", headers=admin).json()["valid"] is True
    with sqlite3.connect(settings.db_path) as raw:
        raw.execute("UPDATE audit_log SET username = 'mallory' WHERE id = 2")
    result = client.get("/api/audit/verify", headers=admin).json()
    assert result["valid"] is False and result["first_bad_id"] == 2


def test_non_admin_cannot_manage_directory(client, make_user):
    _, alice = make_user("alice")
    assert client.get("/api/users", headers=alice).status_code == 403
    assert client.post("/api/users", headers=alice,
                       json={"username": "x", "password": USER_PASSWORD}).status_code == 403
    assert client.post("/api/folders", headers=alice, json={"name": "root"}).status_code == 403


def test_disabling_user_revokes_sessions(client, admin, make_user):
    alice_id, alice = make_user("alice")
    assert client.get("/api/secrets", headers=alice).status_code == 200
    client.patch(f"/api/users/{alice_id}", headers=admin, json={"enabled": False})
    assert client.get("/api/secrets", headers=alice).status_code == 401
    r = client.post("/api/auth/login", json={"username": "alice", "password": USER_PASSWORD})
    assert r.status_code == 403
