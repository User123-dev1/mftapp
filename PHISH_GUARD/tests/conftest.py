import pytest
from fastapi.testclient import TestClient

from phishguard import auth
from phishguard.app import create_app
from phishguard.config import Settings
from tests.fakes import FakeGraph

ADMIN_PW = "Initial-Admin-Pass-1"
NEW_PW = "Strong-Analyst-2026"
MAILBOXES = ["ama.owusu@abelzeta.com", "kofi.asante@abelzeta.com", "finance@abelzeta.com"]


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    monkeypatch.setattr(auth, "ITERATIONS", 1000)


def make_settings(tmp_path, **kw):
    base = dict(db_path=str(tmp_path / "pg.db"), admin_password=ADMIN_PW, org_domains=["abelzeta.com"],
                vip_names=["john mensah"], llm_enabled=False, dns_checks=False, click_fetch=False,
                base_url="https://pg.abelzeta.com", secret_key="test-secret",
                security_team_email="soc@abelzeta.com", alert_from="phishguard@abelzeta.com")
    base.update(kw)
    return Settings(**base)


class Console:
    def __init__(self, app):
        self.client = TestClient(app)
        tok = self.client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW}).json()["token"]
        self.h = {"Authorization": f"Bearer {tok}"}
        r = self.client.post("/api/auth/change-password", headers=self.h,
                             json={"old_password": ADMIN_PW, "new_password": NEW_PW})
        assert r.status_code == 200, r.text

    def get(self, url, **kw):
        r = self.client.get(url, headers=self.h, **kw)
        assert r.status_code == 200, r.text
        return r.json()

    def post(self, url, status=200, **kw):
        r = self.client.post(url, headers=self.h, **kw)
        assert r.status_code == status, r.text
        return r.json()


@pytest.fixture
def graph():
    return FakeGraph(MAILBOXES)


@pytest.fixture
def m365(tmp_path, graph):
    """App wired to a fake Microsoft 365 tenant (initial delta baseline already taken)."""
    app = create_app(make_settings(tmp_path), graph=graph, background=False)
    app.state.guard.poll_m365()   # baseline
    return app, graph, Console(app)
