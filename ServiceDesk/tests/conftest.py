import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import database  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

CSRF_RE = re.compile(r'<meta name="csrf-token" content="([^"]+)"')


class Browser:
    """A TestClient wrapper that keeps its own cookies and fills in the CSRF token."""

    def __init__(self):
        self.client = TestClient(app)

    def csrf(self) -> str:
        page = self.client.get("/login")
        return CSRF_RE.search(page.text).group(1)

    def get(self, url, **kw):
        return self.client.get(url, **kw)

    def post(self, url, data=None, follow=True):
        data = dict(data or {})
        data["csrf_token"] = self.csrf()
        return self.client.post(url, data=data, follow_redirects=follow)

    def login(self, email, password):
        r = self.post("/login", {"email": email, "password": password}, follow=False)
        assert r.status_code == 303, r.text
        return self


@pytest.fixture()
def client(tmp_path):
    settings.smtp_host = ""  # never send real email from tests
    database.configure(f"sqlite:///{tmp_path / 'test.db'}")
    with TestClient(app):  # runs startup: create tables + initial manager
        yield Browser


@pytest.fixture()
def manager(client):
    return client().login(settings.admin_email, settings.admin_password)
