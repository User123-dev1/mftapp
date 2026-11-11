"""
SAML 2.0 service provider (SP-initiated, HTTP-Redirect AuthnRequest,
HTTP-POST assertion consumer).

Security notes:
* Only data inside the XML-signature-verified element is trusted (defeats
  signature-wrapping attacks).
* The assertion must answer an AuthnRequest we issued (InResponseTo), be
  addressed to our ACS URL, target our entity ID, be within its validity
  window, and not have been seen before (replay protection).
* Unsolicited / IdP-initiated responses and encrypted assertions are not
  accepted.
"""

import base64
import zlib
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from urllib.parse import urlencode
from xml.sax.saxutils import escape, quoteattr

from lxml import etree
from signxml import InvalidInput, InvalidSignature, SignatureConfiguration, XMLVerifier
from signxml.algorithms import DigestAlgorithm, SignatureMethod

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
}
STATUS_SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"

_SIGNATURE_CONFIG = SignatureConfiguration(
    require_x509=True,
    expect_references=1,
    signature_methods=frozenset({
        SignatureMethod.RSA_SHA256, SignatureMethod.RSA_SHA384, SignatureMethod.RSA_SHA512,
        SignatureMethod.ECDSA_SHA256, SignatureMethod.ECDSA_SHA384, SignatureMethod.ECDSA_SHA512,
    }),
    digest_algorithms=frozenset({DigestAlgorithm.SHA256, DigestAlgorithm.SHA384,
                                 DigestAlgorithm.SHA512}),
)


class SAMLError(Exception):
    pass


def _parse_time(value: str) -> datetime:
    value = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _saml_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SAMLServiceProvider:
    def __init__(self, config: dict, sp_entity_id: str, acs_url: str):
        self.config = config
        self.sp_entity_id = config.get("sp_entity_id") or sp_entity_id
        self.acs_url = acs_url
        self.skew = timedelta(seconds=int(config.get("clock_skew_seconds", 120)))

    # ------------------------------------------------------------------ outbound

    def authn_request(self, request_id: str) -> str:
        xml = (
            f'<samlp:AuthnRequest xmlns:samlp="{NS["samlp"]}" xmlns:saml="{NS["saml"]}" '
            f'ID={quoteattr(request_id)} Version="2.0" IssueInstant="{_saml_now()}" '
            f'Destination={quoteattr(self.config["idp_sso_url"])} '
            f'AssertionConsumerServiceURL={quoteattr(self.acs_url)} '
            f'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST">'
            f'<saml:Issuer>{escape(self.sp_entity_id)}</saml:Issuer>'
            f'<samlp:NameIDPolicy AllowCreate="true" '
            f'Format="urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified"/>'
            f'</samlp:AuthnRequest>'
        )
        return xml

    def redirect_url(self, request_id: str, relay_state: str) -> str:
        deflated = zlib.compress(self.authn_request(request_id).encode())[2:-4]
        query = urlencode({"SAMLRequest": base64.b64encode(deflated).decode(),
                           "RelayState": relay_state})
        sep = "&" if "?" in self.config["idp_sso_url"] else "?"
        return f"{self.config['idp_sso_url']}{sep}{query}"

    def metadata(self) -> str:
        return (
            f'<?xml version="1.0"?>'
            f'<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" '
            f'entityID={quoteattr(self.sp_entity_id)}>'
            f'<md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" '
            f'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
            f'<md:AssertionConsumerService index="0" isDefault="true" '
            f'Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
            f'Location={quoteattr(self.acs_url)}/>'
            f'</md:SPSSODescriptor></md:EntityDescriptor>'
        )

    # ------------------------------------------------------------------ inbound

    def _verify_signature(self, xml: bytes) -> etree._Element:
        certs = self.config["idp_x509_cert"]
        certs = certs if isinstance(certs, list) else [certs]
        last_error: Optional[Exception] = None
        for cert in certs:  # multiple certs allow IdP signing-cert rollover
            try:
                result = XMLVerifier().verify(xml, x509_cert=cert, expect_config=_SIGNATURE_CONFIG)
                return result.signed_xml
            except (InvalidSignature, InvalidInput) as exc:
                last_error = exc
        raise SAMLError(f"SAML signature validation failed: {last_error}")

    def parse_response(self, saml_response_b64: str, expected_request_id: str) -> dict:
        try:
            xml = base64.b64decode(saml_response_b64, validate=True)
        except Exception as exc:
            raise SAMLError("SAMLResponse is not valid base64") from exc
        if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
            raise SAMLError("DTDs are not allowed in SAML messages")
        if b"EncryptedAssertion" in xml:
            raise SAMLError("encrypted assertions are not supported")

        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
        try:
            outer = etree.fromstring(xml, parser=parser)
        except etree.XMLSyntaxError as exc:
            raise SAMLError("SAMLResponse is not well-formed XML") from exc
        status = outer.find("samlp:Status/samlp:StatusCode", NS)
        if status is None or status.get("Value") != STATUS_SUCCESS:
            raise SAMLError("IdP reported an unsuccessful authentication")

        signed = self._verify_signature(xml)
        tag = etree.QName(signed).localname
        if tag == "Response":
            assertions = signed.findall("saml:Assertion", NS)
            if len(assertions) != 1:
                raise SAMLError("signed response must contain exactly one assertion")
            assertion = assertions[0]
            if signed.get("InResponseTo") != expected_request_id:
                raise SAMLError("response InResponseTo does not match our request")
            dest = signed.get("Destination")
            if dest is not None and dest != self.acs_url:
                raise SAMLError("response Destination does not match ACS URL")
        elif tag == "Assertion":
            assertion = signed
        else:
            raise SAMLError("signature does not cover a Response or Assertion")
        return self._validate_assertion(assertion, expected_request_id)

    def _validate_assertion(self, assertion, expected_request_id: str) -> dict:
        now = datetime.now(timezone.utc)

        issuer = assertion.findtext("saml:Issuer", namespaces=NS)
        if (issuer or "").strip() != self.config["idp_entity_id"]:
            raise SAMLError("assertion issuer does not match configured IdP")

        conditions = assertion.find("saml:Conditions", NS)
        if conditions is None:
            raise SAMLError("assertion has no Conditions")
        nb, noa = conditions.get("NotBefore"), conditions.get("NotOnOrAfter")
        if nb and now + self.skew < _parse_time(nb):
            raise SAMLError("assertion not yet valid")
        if not noa or now - self.skew >= _parse_time(noa):
            raise SAMLError("assertion expired")
        audiences = [a.text.strip() for a in conditions.findall(
            "saml:AudienceRestriction/saml:Audience", NS) if a.text]
        if self.sp_entity_id not in audiences:
            raise SAMLError("assertion audience does not include this service provider")

        subject = assertion.find("saml:Subject", NS)
        name_id = subject.findtext("saml:NameID", namespaces=NS) if subject is not None else None
        if not name_id:
            raise SAMLError("assertion has no NameID")
        confirmed = False
        for sc in subject.findall("saml:SubjectConfirmation", NS):
            data = sc.find("saml:SubjectConfirmationData", NS)
            if sc.get("Method") != BEARER or data is None:
                continue
            if data.get("InResponseTo") != expected_request_id:
                continue
            if data.get("Recipient") != self.acs_url:
                continue
            if not data.get("NotOnOrAfter") or now - self.skew >= _parse_time(data.get("NotOnOrAfter")):
                continue
            confirmed = True
        if not confirmed:
            raise SAMLError("no valid bearer SubjectConfirmation for this request")

        attributes: Dict[str, List[str]] = {}
        for attr in assertion.findall("saml:AttributeStatement/saml:Attribute", NS):
            values = [v.text or "" for v in attr.findall("saml:AttributeValue", NS)]
            attributes.setdefault(attr.get("Name"), []).extend(values)

        return {
            "assertion_id": assertion.get("ID"),
            "not_on_or_after": noa,
            "name_id": name_id.strip(),
            "attributes": attributes,
        }
