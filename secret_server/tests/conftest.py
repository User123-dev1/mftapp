import os
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from secret_server.api import create_app  # noqa: E402
from secret_server.audit import AuditLog  # noqa: E402
from secret_server.config import Settings  # noqa: E402
from secret_server.crypto import KeyRing, generate_key  # noqa: E402
from secret_server.db import Database  # noqa: E402
from secret_server.directory import Directory  # noqa: E402
from secret_server.sso.oidc import OIDCClient  # noqa: E402

ADMIN_PASSWORD = "Adm1n-Passw0rd!"
USER_PASSWORD = "Us3r-Passw0rd!x"


class IdPRouter:
    """Pluggable handler for the mock HTTP transport used by SSO tests."""

    def __init__(self):
        self.handler = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.handler is None:
            return httpx.Response(503)
        return self.handler(request)


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=str(tmp_path / "vault.db"), master_key_file=None,
                    master_key=generate_key(), public_url="https://vault.example.com",
                    max_failed_logins=3)


@pytest.fixture
def idp():
    OIDCClient._metadata_cache.clear()
    return IdPRouter()


@pytest.fixture
def app(settings, idp):
    application = create_app(settings, http_factory=lambda: httpx.Client(
        transport=httpx.MockTransport(idp)))
    keyring = KeyRing.from_settings(settings)
    with Database(settings.db_path).transaction() as conn:
        Directory(AuditLog(keyring)).create_user(conn, None, "admin", ADMIN_PASSWORD,
                                                 role="administrator")
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def admin(client):
    return login(client, "admin", ADMIN_PASSWORD)


@pytest.fixture
def make_user(client, admin):
    def _make(username, role="user"):
        r = client.post("/api/users", headers=admin,
                        json={"username": username, "password": USER_PASSWORD, "role": role})
        assert r.status_code == 201, r.text
        return r.json()["id"], login(client, username, USER_PASSWORD)
    return _make
