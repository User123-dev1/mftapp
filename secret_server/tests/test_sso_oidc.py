import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from secret_server.sso.oidc import pkce_pair

ISSUER = "https://idp.example.com"
CLIENT_ID = "secret-server"


class MockIdP:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.codes = {}
        self.overrides = {}

    def jwks(self):
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update(kid="key-1", use="sig", alg="RS256")
        return {"keys": [jwk]}

    def issue_code(self, nonce, challenge, claims):
        code = f"code-{len(self.codes)}"
        self.codes[code] = (nonce, challenge, claims)
        return code

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
            })
        if path == "/jwks":
            return httpx.Response(200, json=self.jwks())
        if path == "/token":
            form = parse_qs(request.content.decode())
            nonce, challenge, claims = self.codes.pop(form["code"][0], (None, None, None))
            if nonce is None or pkce_pair(form["code_verifier"][0]) != challenge:
                return httpx.Response(400, json={"error": "invalid_grant"})
            now = int(time.time())
            payload = {"iss": ISSUER, "aud": CLIENT_ID, "iat": now, "exp": now + 300,
                       "nonce": nonce, **claims, **self.overrides}
            token = jwt.encode(payload, self.key, algorithm="RS256", headers={"kid": "key-1"})
            return httpx.Response(200, json={"id_token": token, "access_token": "at",
                                             "token_type": "Bearer"})
        return httpx.Response(404)


@pytest.fixture
def mock_idp(idp, client, admin):
    server = MockIdP()
    idp.handler = server
    r = client.post("/api/sso/providers", headers=admin, json={
        "name": "okta", "type": "oidc",
        "config": {"issuer": ISSUER, "client_id": CLIENT_ID, "client_secret": "s3cr3t",
                   "group_mappings": {"vault-dba": "DBA Team"},
                   "admin_groups": ["vault-admins"],
                   "allowed_email_domains": ["example.com"]},
    })
    assert r.status_code == 201, r.text
    assert "client_secret" not in r.json()["config"] and r.json()["has_client_secret"]
    return server


def _start(client):
    r = client.get("/sso/okta/login", follow_redirects=False)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == ["https://vault.example.com/sso/okta/callback"]
    return q["state"][0], q["nonce"][0], q["code_challenge"][0]


def _sso_login(client, mock_idp, **claims):
    state, nonce, challenge = _start(client)
    base = {"sub": "00u1", "preferred_username": "carol", "email": "carol@example.com",
            "name": "Carol", "groups": []}
    code = mock_idp.issue_code(nonce, challenge, {**base, **claims})
    return client.get("/sso/okta/callback", params={"code": code, "state": state},
                      follow_redirects=False)


def test_oidc_login_provisions_user_and_maps_groups(client, admin, mock_idp):
    r = _sso_login(client, mock_idp, groups=["vault-dba"])
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["username"] == "carol" and me["auth_source"] == "okta" and me["role"] == "user"

    dba = next(g for g in client.get("/api/groups", headers=admin).json() if g["name"] == "DBA Team")
    assert dba["member_ids"] == [me["id"]]

    # Next login: removed from IdP group -> removed locally; added to admin group -> admin
    r = _sso_login(client, mock_idp, groups=["vault-admins"])
    me = client.get("/api/auth/me",
                    headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()
    assert me["role"] == "administrator"
    dba = next(g for g in client.get("/api/groups", headers=admin).json() if g["name"] == "DBA Team")
    assert dba["member_ids"] == []


def test_sso_user_cannot_use_local_password(client, mock_idp):
    _sso_login(client, mock_idp)
    r = client.post("/api/auth/login", json={"username": "carol", "password": "anything"})
    assert r.status_code == 403


def test_state_is_single_use(client, mock_idp):
    state, nonce, challenge = _start(client)
    code = mock_idp.issue_code(nonce, challenge, {"sub": "1", "email": "a@example.com"})
    assert client.get("/sso/okta/callback", params={"code": code, "state": state}).status_code == 200
    code = mock_idp.issue_code(nonce, challenge, {"sub": "1", "email": "a@example.com"})
    assert client.get("/sso/okta/callback", params={"code": code, "state": state}).status_code == 400


@pytest.mark.parametrize("override", [
    {"aud": "someone-else"},
    {"iss": "https://evil.example.com"},
    {"exp": int(time.time()) - 3600},
    {"nonce": "wrong-nonce"},
])
def test_invalid_id_tokens_rejected(client, admin, mock_idp, override):
    mock_idp.overrides = override
    assert _sso_login(client, mock_idp).status_code == 401
    failures = client.get("/api/audit", headers=admin, params={"action": "SSO_LOGIN_FAILED"}).json()
    assert len(failures) == 1


def test_email_domain_allow_list(client, mock_idp):
    assert _sso_login(client, mock_idp, email="carol@evil.com").status_code == 401


def test_jit_cannot_take_over_local_account(client, mock_idp, make_user):
    make_user("carol")
    assert _sso_login(client, mock_idp).status_code == 401


def test_jit_disabled_requires_preprovisioned_user(client, admin, mock_idp):
    r = client.post("/api/sso/providers", headers=admin, json={
        "name": "entra", "type": "oidc", "jit_provisioning": False,
        "config": {"issuer": ISSUER, "client_id": CLIENT_ID}})
    assert r.status_code == 201

    def login_via_entra():
        r = client.get("/sso/entra/login", follow_redirects=False)
        q = parse_qs(urlparse(r.headers["location"]).query)
        code = mock_idp.issue_code(q["nonce"][0], q["code_challenge"][0],
                                   {"sub": "x9", "preferred_username": "dave"})
        return client.get("/sso/entra/callback", params={"code": code, "state": q["state"][0]})

    assert login_via_entra().status_code == 401
    client.post("/api/users", headers=admin, json={"username": "dave", "auth_source": "entra"})
    assert login_via_entra().status_code == 200


def test_redirect_mode_uses_one_time_code(client, settings, mock_idp):
    settings.sso_success_redirect = "https://vault.example.com/app"
    r = _sso_login(client, mock_idp)
    assert r.status_code == 303
    location = r.headers["location"]
    assert "access_token" not in location
    code = parse_qs(urlparse(location).query)["code"][0]
    first = client.post("/api/auth/sso/exchange", json={"code": code})
    assert first.status_code == 200 and "access_token" in first.json()
    assert client.post("/api/auth/sso/exchange", json={"code": code}).status_code == 400


def test_public_provider_list_hides_config(client, mock_idp):
    providers = client.get("/api/auth/providers").json()
    assert providers == [{"name": "okta", "type": "oidc",
                          "login_url": "https://vault.example.com/sso/okta/login"}]
