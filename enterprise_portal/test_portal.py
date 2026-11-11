import json

import httpx
import pytest
from fastapi.testclient import TestClient

import portal_server


@pytest.fixture
def catalog_file(tmp_path, monkeypatch):
    path = tmp_path / "apps.json"
    path.write_text(json.dumps({
        "portal": {"title": "Test Portal"},
        "apps": [
            {"id": "up-app", "name": "Up", "url": "http://up.test/", "health_url": "http://up.test/health"},
            {"id": "login-app", "name": "Login", "url": "http://login.test/"},
            {"id": "broken-app", "name": "Broken", "url": "http://broken.test/"},
            {"id": "offline-app", "name": "Offline", "url": "http://offline.test/"},
        ],
    }))
    monkeypatch.setattr(portal_server, "APPS_FILE", path)
    portal_server._status_cache.clear()
    return path


@pytest.fixture
def client(catalog_file, monkeypatch):
    def handler(request: httpx.Request):
        host = request.url.host
        if host == "up.test":
            return httpx.Response(200)
        if host == "login.test":
            return httpx.Response(302, headers={"Location": "/login"})
        if host == "broken.test":
            return httpx.Response(503)
        raise httpx.ConnectError("unreachable", request=request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(portal_server.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    return TestClient(portal_server.app)


def test_index_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Enterprise App Portal" in response.text


def test_list_apps_hides_health_url(client):
    data = client.get("/api/apps").json()
    assert data["portal"]["title"] == "Test Portal"
    assert [a["id"] for a in data["apps"]] == ["up-app", "login-app", "broken-app", "offline-app"]
    assert all(a["health_url"] is None for a in data["apps"])
    assert data["apps"][0]["category"] == "General"


def test_status(client):
    statuses = {s["id"]: s for s in client.get("/api/status").json()}
    assert statuses["up-app"]["status"] == "up"
    assert statuses["login-app"]["status"] == "up"
    assert statuses["login-app"]["http_status"] == 302
    assert statuses["broken-app"]["status"] == "down"
    assert statuses["offline-app"]["status"] == "down"
    assert statuses["offline-app"]["http_status"] is None


def test_status_is_cached(client, monkeypatch):
    client.get("/api/status")
    calls = []
    monkeypatch.setattr(portal_server, "check_app",
                        lambda c, a: calls.append(a.id))
    client.get("/api/status")
    assert calls == []


def test_duplicate_ids_rejected(client, catalog_file):
    catalog_file.write_text(json.dumps({"apps": [
        {"id": "a", "name": "A", "url": "http://a/"},
        {"id": "a", "name": "A2", "url": "http://a2/"},
    ]}))
    response = client.get("/api/apps")
    assert response.status_code == 500
    assert "Duplicate" in response.json()["detail"]
