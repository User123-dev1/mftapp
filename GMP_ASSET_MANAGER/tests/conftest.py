from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from gmp_assets import clock
from gmp_assets.api import create_app
from gmp_assets.config import Settings

ADMIN_TEMP = "Initial-Admin-1"
PASSWORDS = {"admin": "Strong-Root-2026", "qa1": "Quality-Pw-2026", "tech1": "Calib-Pw-20261",
             "tech2": "Maint-Pw-20262", "op1": "Floor-Pw-20263"}


class FakeClock:
    def __init__(self):
        self.current = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)

    def now(self):
        return self.current

    def advance(self, **kw):
        self.current += timedelta(**kw)


@pytest.fixture
def fake_clock(monkeypatch):
    fc = FakeClock()
    monkeypatch.setattr(clock, "now", fc.now)
    return fc


@pytest.fixture
def app(tmp_path, monkeypatch, fake_clock):
    monkeypatch.setenv("GMP_ADMIN_PASSWORD", ADMIN_TEMP)
    settings = Settings(db_path=str(tmp_path / "gmp.db"), pbkdf2_iterations=1000)
    return create_app(settings)


class Api:
    def __init__(self, client: TestClient):
        self.client = client
        self.tokens = {}

    def login(self, username, password=None):
        r = self.client.post("/api/auth/login", json={"username": username, "password": password or PASSWORDS[username]})
        assert r.status_code == 200, r.text
        self.tokens[username] = r.json()["token"]
        return r.json()

    def call(self, user, method, url, **kw):
        headers = {"Authorization": f"Bearer {self.tokens[user]}"} if user else {}
        return self.client.request(method, url, headers=headers, **kw)

    def ok(self, user, method, url, status=200, **kw):
        r = self.call(user, method, url, **kw)
        assert r.status_code == status, f"{method} {url}: {r.status_code} {r.text}"
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text

    def sig(self, user):
        return {"password": PASSWORDS[user]}


@pytest.fixture
def api(app):
    client = TestClient(app)
    a = Api(client)
    first = a.login("admin", ADMIN_TEMP)
    assert first["must_change_password"]
    a.ok("admin", "post", "/api/auth/change-password", json={"old_password": ADMIN_TEMP, "new_password": PASSWORDS["admin"]})
    for username, role in (("qa1", "qa"), ("tech1", "technician"), ("tech2", "technician"), ("op1", "operator")):
        a.ok("admin", "post", "/api/users", status=201, json={
            "username": username, "full_name": f"{username.upper()} Person", "role": role, "temporary_password": "Temp-Pass-123"})
        a.login(username, "Temp-Pass-123")
        a.ok(username, "post", "/api/auth/change-password",
             json={"old_password": "Temp-Pass-123", "new_password": PASSWORDS[username]})
    return a
