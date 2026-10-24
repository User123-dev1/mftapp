from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy import select

from app import database, sso
from app.config import settings
from app.models import Role, User

from .fake_idp import FakeIdP

TENANT = "11111111-2222-3333-4444-555555555555"


@pytest.fixture()
def idp():
    server = FakeIdP(client_id="servicedesk-client").start()
    yield server
    server.stop()


@pytest.fixture()
def entra(client, idp, monkeypatch):
    monkeypatch.setattr(settings, "entra_authority", idp.base)
    monkeypatch.setattr(settings, "entra_tenant_id", TENANT)
    monkeypatch.setattr(settings, "entra_client_id", idp.client_id)
    monkeypatch.setattr(settings, "entra_client_secret", "secret")
    sso.reset()
    yield idp
    monkeypatch.undo()
    sso.reset()


@pytest.fixture()
def okta(client, idp, monkeypatch):
    monkeypatch.setattr(settings, "oidc_discovery_url", f"{idp.base}/.well-known/openid-configuration")
    monkeypatch.setattr(settings, "oidc_client_id", idp.client_id)
    monkeypatch.setattr(settings, "oidc_client_secret", "secret")
    monkeypatch.setattr(settings, "oidc_display_name", "Okta")
    sso.reset()
    yield idp
    monkeypatch.undo()
    sso.reset()


def sso_login(browser, key, next_url="/"):
    """Drive the browser through the redirect dance; returns the final response."""
    r = browser.get(f"/auth/{key}/login?next={next_url}", follow_redirects=False)
    assert r.status_code in (302, 303, 307), r.text
    authorize_url = r.headers["location"]
    idp_resp = httpx.get(authorize_url, follow_redirects=False)
    assert idp_resp.status_code == 302, idp_resp.text
    callback = urlsplit(idp_resp.headers["location"])
    assert callback.path == f"/auth/{key}/callback"
    return browser.get(f"{callback.path}?{callback.query}", follow_redirects=False)


def entra_claims(**extra):
    claims = {
        "sub": "pairwise-sub-1",
        "oid": "aaaa-oid-1",
        "tid": TENANT,
        "name": "Erin Entra",
        "preferred_username": "erin@contoso.com",
    }
    claims.update(extra)
    return claims


def user_by_email(email):
    with database.SessionLocal() as db:
        return db.scalar(select(User).where(User.email == email))


def test_login_page_shows_sso_buttons(client, entra):
    page = client().get("/login").text
    assert "Sign in with Microsoft" in page
    assert "/auth/entra/login" in page


def test_entra_sign_in_provisions_requester(client, entra):
    entra.next_claims = entra_claims()
    browser = client()
    r = sso_login(browser, "entra", "/tickets/new")
    assert r.status_code == 303 and r.headers["location"] == "/tickets/new"

    user = user_by_email("erin@contoso.com")
    assert user.full_name == "Erin Entra"
    assert user.role == Role.REQUESTER
    assert user.sso_provider == "entra" and user.sso_subject == "aaaa-oid-1"
    assert not user.has_password
    assert "Erin Entra" in browser.get("/tickets/new").text

    # Second sign-in reuses the same account.
    sso_login(client(), "entra")
    with database.SessionLocal() as db:
        assert db.query(User).filter(User.email == "erin@contoso.com").count() == 1


def test_entra_group_mapping(client, entra, monkeypatch):
    monkeypatch.setattr(settings, "sso_manager_groups", ["grp-it-managers"])
    monkeypatch.setattr(settings, "sso_agent_groups", ["ServiceDesk.Agent"])  # app role value

    entra.next_claims = entra_claims(roles=["ServiceDesk.Agent"])
    browser = client()
    sso_login(browser, "entra")
    assert user_by_email("erin@contoso.com").role == Role.AGENT
    assert browser.get("/dashboard").status_code == 200

    entra.next_claims = entra_claims(groups=["grp-it-managers"])
    sso_login(client(), "entra")
    assert user_by_email("erin@contoso.com").role == Role.MANAGER

    # Group overage: token can't list groups, so the role is left alone.
    entra.next_claims = entra_claims(_claim_names={"groups": "src1"})
    sso_login(client(), "entra")
    assert user_by_email("erin@contoso.com").role == Role.MANAGER

    # Removed from all groups -> back to requester.
    entra.next_claims = entra_claims()
    sso_login(client(), "entra")
    assert user_by_email("erin@contoso.com").role == Role.REQUESTER


def test_manager_precreates_sso_agent(client, manager, entra):
    r = manager.post("/admin/users", {"full_name": "Erin Entra", "email": "erin@contoso.com", "password": "", "role": "agent"})
    assert "They can sign in with SSO" in r.text
    assert not user_by_email("erin@contoso.com").has_password

    entra.next_claims = entra_claims()
    sso_login(client(), "entra")
    user = user_by_email("erin@contoso.com")
    assert user.role == Role.AGENT and user.sso_subject == "aaaa-oid-1"


def test_wrong_tenant_token_rejected(client, entra):
    # A token issued for another tenant fails issuer validation.
    entra.next_claims = entra_claims(iss=entra.issuer("99999999-0000-0000-0000-000000000000"))
    r = sso_login(client(), "entra")
    assert r.headers["location"] == "/login"
    assert user_by_email("erin@contoso.com") is None


def test_multi_tenant_alias_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "entra_tenant_id", "common")
    monkeypatch.setattr(settings, "entra_client_id", "x")
    assert sso.configured_providers() == []


def test_allowed_domains(client, entra, monkeypatch):
    monkeypatch.setattr(settings, "sso_allowed_domains", ["fabrikam.com"])
    entra.next_claims = entra_claims()
    browser = client()
    sso_login(browser, "entra")
    assert user_by_email("erin@contoso.com") is None
    assert "not allowed" in browser.get("/login").text


def test_no_auto_provision(client, entra, monkeypatch):
    monkeypatch.setattr(settings, "sso_auto_provision", False)
    entra.next_claims = entra_claims()
    browser = client()
    sso_login(browser, "entra")
    assert user_by_email("erin@contoso.com") is None
    assert "do not have an account" in browser.get("/login").text


def test_disabled_user_blocked(client, entra):
    entra.next_claims = entra_claims()
    sso_login(client(), "entra")
    with database.SessionLocal() as db:
        db.scalar(select(User).where(User.email == "erin@contoso.com")).is_active = False
        db.commit()
    browser = client()
    sso_login(browser, "entra")
    assert "disabled" in browser.get("/login").text


def test_generic_oidc_requires_verified_email_to_link(client, okta):
    # Existing password account.
    client().post("/register", {"full_name": "Pat", "email": "pat@example.com", "password": "Password1!"})

    okta.next_claims = {"sub": "okta-1", "email": "pat@example.com", "name": "Pat"}
    browser = client()
    sso_login(browser, "oidc")
    assert user_by_email("pat@example.com").sso_subject is None
    assert "already exists" in browser.get("/login").text

    okta.next_claims["email_verified"] = True
    sso_login(client(), "oidc")
    assert user_by_email("pat@example.com").sso_subject == "okta-1"


def test_tampered_state_rejected(client, okta):
    okta.next_claims = {"sub": "okta-2", "email": "sam@example.com", "email_verified": True}
    browser = client()
    browser.get("/auth/oidc/login", follow_redirects=False)
    r = browser.get("/auth/oidc/callback?code=abc&state=forged", follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert user_by_email("sam@example.com") is None


def test_sso_only_mode(client, manager, entra, monkeypatch):
    monkeypatch.setattr(settings, "local_login", False)
    page = client().get("/login").text
    assert 'name="password"' not in page and "Administrator sign-in" in page
    assert client().get("/register", follow_redirects=False).status_code == 303

    # Requesters can't use passwords; the manager still can (break-glass).
    manager.post("/admin/users", {"full_name": "Req", "email": "req@example.com", "password": "Password1!", "role": "requester"})
    r = client().post("/login", {"email": "req@example.com", "password": "Password1!"}, follow=False)
    assert r.status_code == 400
    r = client().post("/login", {"email": settings.admin_email, "password": settings.admin_password}, follow=False)
    assert r.status_code == 303
