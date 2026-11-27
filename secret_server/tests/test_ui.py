"""The web interface: served by the vault, locked down with a CSP, built only on the API."""
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "ui"


def test_index_served_with_csp(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert '<script src="/ui/app.js" defer>' in r.text
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
    assert r.headers["x-frame-options"] == "DENY" and r.headers["cache-control"] == "no-store"


def test_assets_served(client):
    for name, kind in [("app.js", "javascript"), ("style.css", "css"), ("vault.svg", "svg")]:
        r = client.get(f"/ui/{name}")
        assert r.status_code == 200 and kind in r.headers["content-type"], name
        assert "content-security-policy" in r.headers


def test_api_console_and_api_unchanged(client, admin):
    r = client.get("/docs")
    assert r.status_code == 200 and "content-security-policy" not in r.headers   # Swagger needs its CDN
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/auth/me", headers=admin).json()["username"] == "admin"
    assert client.get("/api/folders").status_code == 401


def test_no_raw_html_insertion():
    """Server data must only ever reach the page as text."""
    js = (UI / "app.js").read_text()
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert bad not in js, bad
    html = (UI / "index.html").read_text()
    assert "<script>" not in html and " style=" not in html and "http" not in html.replace("https://", "")


def test_ui_flow_through_api(client, admin, make_user):
    """The calls the interface makes for a typical session, in order."""
    uid, user = make_user("alice")
    folder = client.post("/api/folders", headers=admin, json={"name": "Servers"}).json()
    r = client.put(f"/api/folders/{folder['id']}/permissions", headers=admin,
                   json={"inherit_permissions": False,
                         "entries": [{"principal_type": "user", "principal_id": uid, "level": "edit"}]})
    assert r.status_code == 200, r.text
    tpl = client.get("/api/templates", headers=user).json()
    login_tpl = next(t for t in tpl if t["name"] == "Database Account")
    required = {f["slug"]: "x-" + f["slug"] for f in login_tpl["fields"]
                if f.get("required") and f.get("type") != "password"}
    r = client.post("/api/secrets", headers=user, json={
        "name": "SQL01 sa", "folder_id": folder["id"], "template_id": login_tpl["id"],
        "fields": required, "generate_password": True,
        "settings": {"require_checkout": True, "checkout_minutes": 30, "require_comment": False}})
    assert r.status_code == 201, r.text
    s = r.json()
    d = client.get(f"/api/secrets/{s['id']}", headers=user).json()
    assert d["my_permission"] == "edit" and d["settings"]["require_checkout"]
    assert client.post(f"/api/secrets/{s['id']}/reveal", headers=user, json={}).status_code == 409   # not checked out
    assert client.post(f"/api/secrets/{s['id']}/check-out", headers=user, json={"comment": None}).status_code == 200
    values = client.post(f"/api/secrets/{s['id']}/reveal", headers=user, json={"comment": "maintenance"}).json()
    assert any(len(v) >= 12 for v in values.values())
    assert client.post(f"/api/secrets/{s['id']}/check-in", headers=user).json()["checked_in"]
    assert [x["name"] for x in client.get("/api/secrets?q=SQL", headers=user).json()] == ["SQL01 sa"]
    audit = client.get(f"/api/secrets/{s['id']}/audit", headers=admin).json()
    assert {"SECRET_CHECKOUT", "SECRET_VIEW", "SECRET_CHECKIN"} <= {a["action"] for a in audit}


def test_requests_keep_same_origin_cookies():
    """Behind Entra Application Proxy every request must carry the proxy's own sign-in cookie."""
    js = (UI / "app.js").read_text()
    assert "credentials: 'omit'" not in js and "credentials: 'same-origin'" in js
