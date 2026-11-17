import base64
import os
import re
import secrets
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ADMIN_PASSWORD = "Admin-Pass-2026x"
DEMO_PASSWORD = "Welcome-Demo-2026"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    env = dict(PS_DB_PATH=str(tmp_path / "ps.db"), PS_SECRET_KEY="k" * 40,
               PS_DATA_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
               PS_ADMIN_PASSWORD=ADMIN_PASSWORD, PS_DEMO="true", PS_DEMO_OUTBOX="true",
               PS_BASE_URL="https://passwords.example.test")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from pstation.config import Settings
    return Settings.from_env() if hasattr(Settings, "from_env") else Settings()


@pytest.fixture
def app(settings):
    from pstation.app import create_app
    return create_app(settings)


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def csrf(client, path="/change"):
    html = client.get(path).text
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def outbox(app):
    station = app.state.station
    return [dict(r) for r in station.db.read().execute("SELECT * FROM outbox ORDER BY rowid").fetchall()]


def last_code(app):
    rows = outbox(app)
    assert rows, "nothing was sent"
    return re.search(r"\b(\d{6})\b", rows[-1]["body"]).group(1)
