import base64
import zlib
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import XMLSigner

IDP_ENTITY = "https://idp.example.com/saml"
SP_ENTITY = "https://vault.example.com/sso/adfs/metadata"
ACS = "https://vault.example.com/sso/adfs/acs"


def _keypair(cn="idp"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
            .sign(key, hashes.SHA256()))
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return key_pem, cert.public_bytes(serialization.Encoding.PEM)


IDP_KEY, IDP_CERT = _keypair()


def _ts(delta=timedelta()):
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_response(request_id, name_id="erin@example.com", groups=(), audience=SP_ENTITY,
                   recipient=ACS, assertion_id="_a1", key=IDP_KEY, cert=IDP_CERT,
                   not_on_or_after=timedelta(minutes=5)):
    attrs = "".join(f"<saml:AttributeValue>{g}</saml:AttributeValue>" for g in groups)
    assertion = f"""<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
        ID="{assertion_id}" Version="2.0" IssueInstant="{_ts()}">
      <saml:Issuer>{IDP_ENTITY}</saml:Issuer>
      <saml:Subject>
        <saml:NameID>{name_id}</saml:NameID>
        <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
          <saml:SubjectConfirmationData InResponseTo="{request_id}" Recipient="{recipient}"
              NotOnOrAfter="{_ts(not_on_or_after)}"/>
        </saml:SubjectConfirmation>
      </saml:Subject>
      <saml:Conditions NotBefore="{_ts(-timedelta(minutes=1))}" NotOnOrAfter="{_ts(not_on_or_after)}">
        <saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction>
      </saml:Conditions>
      <saml:AttributeStatement>
        <saml:Attribute Name="email"><saml:AttributeValue>{name_id}</saml:AttributeValue></saml:Attribute>
        <saml:Attribute Name="groups">{attrs}</saml:Attribute>
      </saml:AttributeStatement>
    </saml:Assertion>"""
    signed = XMLSigner(signature_algorithm="rsa-sha256", digest_algorithm="sha256",
                       c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#").sign(
        etree.fromstring(assertion), key=key, cert=cert)
    response = etree.fromstring(f"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
        xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_r1" Version="2.0"
        IssueInstant="{_ts()}" Destination="{ACS}" InResponseTo="{request_id}">
      <saml:Issuer>{IDP_ENTITY}</saml:Issuer>
      <samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>
    </samlp:Response>""")
    response.append(signed)
    return response


def encode(response) -> str:
    return base64.b64encode(etree.tostring(response)).decode()


@pytest.fixture
def saml(client, admin):
    r = client.post("/api/sso/providers", headers=admin, json={
        "name": "adfs", "type": "saml",
        "config": {"idp_entity_id": IDP_ENTITY, "idp_sso_url": "https://idp.example.com/sso",
                   "idp_x509_cert": IDP_CERT.decode(), "username_claim": "email",
                   "group_mappings": {"Vault-Ops": "Operations"},
                   "admin_groups": ["Vault-Admins"]},
    })
    assert r.status_code == 201, r.text
    assert r.json()["acs_url"] == ACS


def start(client):
    r = client.get("/sso/adfs/login", follow_redirects=False)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    xml = zlib.decompress(base64.b64decode(q["SAMLRequest"][0]), -15)
    request = etree.fromstring(xml)
    assert request.get("AssertionConsumerServiceURL") == ACS
    return request.get("ID"), q["RelayState"][0]


def post(client, response, relay_state):
    return client.post("/sso/adfs/acs", data={"SAMLResponse": encode(response),
                                              "RelayState": relay_state})


def test_saml_login_provisions_user(client, admin, saml):
    request_id, relay = start(client)
    r = post(client, build_response(request_id, groups=["Vault-Ops", "Vault-Admins"]), relay)
    assert r.status_code == 200, r.text
    me = client.get("/api/auth/me",
                    headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()
    assert me["username"] == "erin@example.com"
    assert me["role"] == "administrator"
    ops = next(g for g in client.get("/api/groups", headers=admin).json() if g["name"] == "Operations")
    assert ops["member_ids"] == [me["id"]]


def test_sp_metadata(client, saml):
    r = client.get("/sso/adfs/metadata")
    assert r.status_code == 200 and ACS in r.text and SP_ENTITY in r.text


def test_tampered_assertion_rejected(client, saml):
    request_id, relay = start(client)
    response = build_response(request_id, name_id="erin@example.com")
    ns = {"saml": "urn:oasis:names:tc:SAML:2.0:assertion"}
    response.find(".//saml:NameID", ns).text = "admin"
    assert post(client, response, relay).status_code == 401


def test_signature_wrapping_attack_rejected(client, saml):
    """Attacker injects an unsigned assertion ahead of a legitimately signed one."""
    request_id, relay = start(client)
    response = build_response(request_id, name_id="erin@example.com")
    evil = build_response(request_id, name_id="admin", assertion_id="_evil")
    ns = {"saml": "urn:oasis:names:tc:SAML:2.0:assertion",
          "ds": "http://www.w3.org/2000/09/xmldsig#"}
    evil_assertion = evil.find("saml:Assertion", ns)
    evil_assertion.remove(evil_assertion.find("ds:Signature", ns))
    response.insert(2, evil_assertion)
    r = post(client, response, relay)
    assert r.status_code == 200, r.text
    me = client.get("/api/auth/me",
                    headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()
    assert me["username"] == "erin@example.com"  # only signed data is trusted


def test_untrusted_signing_key_rejected(client, saml):
    request_id, relay = start(client)
    key, cert = _keypair("attacker")
    assert post(client, build_response(request_id, key=key, cert=cert), relay).status_code == 401


@pytest.mark.parametrize("kwargs", [
    {"audience": "https://other-sp.example.com"},
    {"recipient": "https://evil.example.com/acs"},
    {"not_on_or_after": -timedelta(minutes=10)},
])
def test_invalid_conditions_rejected(client, saml, kwargs):
    request_id, relay = start(client)
    assert post(client, build_response(request_id, **kwargs), relay).status_code == 401


def test_unsolicited_and_replayed_responses_rejected(client, saml):
    request_id, relay = start(client)
    # Response answering a different request
    assert post(client, build_response("_not-our-request"), relay).status_code == 401
    # RelayState was consumed by the failed attempt; a fresh login succeeds once
    request_id, relay = start(client)
    response = build_response(request_id, assertion_id="_replay")
    assert post(client, response, relay).status_code == 200
    # Replaying the same assertion with a fresh RelayState is rejected
    _, relay2 = start(client)
    assert post(client, response, relay2).status_code == 401
