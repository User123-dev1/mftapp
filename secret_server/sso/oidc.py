"""
OpenID Connect relying party (authorization code flow + PKCE).

Works with Microsoft Entra ID (Azure AD), Okta, Ping, Google Workspace,
Keycloak, ADFS 2019+ and other compliant IdPs.
"""

import base64
import hashlib
import hmac
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
import jwt

ALLOWED_ALGORITHMS = {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512",
                      "ES256", "ES384", "ES512"}


class OIDCError(Exception):
    pass


def pkce_pair(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OIDCClient:
    METADATA_TTL = 3600

    _metadata_cache: dict = {}

    def __init__(self, config: dict, client_secret: Optional[str], redirect_uri: str,
                 http: httpx.Client):
        self.config = config
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.http = http

    # ------------------------------------------------------------------ discovery

    def metadata(self) -> dict:
        issuer = self.config["issuer"].rstrip("/")
        url = self.config.get("discovery_url") or f"{issuer}/.well-known/openid-configuration"
        cached = self._metadata_cache.get(url)
        if cached and cached[0] > time.time():
            return cached[1]
        resp = self.http.get(url, timeout=10)
        resp.raise_for_status()
        meta = resp.json()
        if meta.get("issuer", "").rstrip("/") != issuer:
            raise OIDCError("discovery document issuer does not match configured issuer")
        self._metadata_cache[url] = (time.time() + self.METADATA_TTL, meta)
        return meta

    # ------------------------------------------------------------------ flow

    def authorization_url(self, state: str, nonce: str, code_verifier: str) -> str:
        meta = self.metadata()
        params = {
            "response_type": "code",
            "client_id": self.config["client_id"],
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.config.get("scopes") or ["openid", "profile", "email"]),
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce_pair(code_verifier),
            "code_challenge_method": "S256",
        }
        if self.config.get("prompt"):
            params["prompt"] = self.config["prompt"]
        return f"{meta['authorization_endpoint']}?{urlencode(params)}"

    def exchange_code(self, code: str, code_verifier: str) -> dict:
        meta = self.metadata()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": code_verifier,
            "client_id": self.config["client_id"],
        }
        auth = None
        if self.client_secret:
            auth = (self.config["client_id"], self.client_secret)  # client_secret_basic
        resp = self.http.post(meta["token_endpoint"], data=data, auth=auth, timeout=10,
                              headers={"Accept": "application/json"})
        if resp.status_code != 200:
            raise OIDCError(f"token endpoint returned {resp.status_code}")
        tokens = resp.json()
        if "id_token" not in tokens:
            raise OIDCError("token response did not include an id_token")
        return tokens

    def validate_id_token(self, id_token: str, nonce: str) -> dict:
        meta = self.metadata()
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise OIDCError("malformed id_token") from exc
        alg = header.get("alg")
        allowed = set(self.config.get("allowed_algorithms") or ALLOWED_ALGORITHMS) & ALLOWED_ALGORITHMS
        if alg not in allowed:
            raise OIDCError(f"id_token algorithm {alg!r} not allowed")

        resp = self.http.get(meta["jwks_uri"], timeout=10)
        resp.raise_for_status()
        jwks = jwt.PyJWKSet.from_dict(resp.json())
        kid = header.get("kid")
        candidates = [k for k in jwks.keys if kid is None or k.key_id == kid]
        if not candidates:
            raise OIDCError("id_token signing key not found in JWKS")

        try:
            claims = jwt.decode(
                id_token, candidates[0].key, algorithms=[alg],
                audience=self.config["client_id"], issuer=meta["issuer"],
                leeway=int(self.config.get("clock_skew_seconds", 60)),
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise OIDCError(f"id_token validation failed: {exc}") from exc

        aud = claims["aud"]
        if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != self.config["client_id"]:
            raise OIDCError("id_token azp does not match client_id")
        if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise OIDCError("id_token nonce mismatch")
        return claims

    def userinfo(self, access_token: str) -> dict:
        endpoint = self.metadata().get("userinfo_endpoint")
        if not endpoint:
            return {}
        resp = self.http.get(endpoint, headers={"Authorization": f"Bearer {access_token}"},
                             timeout=10)
        if resp.status_code != 200:
            raise OIDCError(f"userinfo endpoint returned {resp.status_code}")
        return resp.json()

    def complete(self, code: str, code_verifier: str, nonce: str) -> dict:
        """Run the callback leg and return the merged identity claims."""
        tokens = self.exchange_code(code, code_verifier)
        claims = self.validate_id_token(tokens["id_token"], nonce)
        groups_claim = self.config.get("groups_claim", "groups")
        if groups_claim not in claims and tokens.get("access_token"):
            info = self.userinfo(tokens["access_token"])
            if info:
                if info.get("sub") != claims["sub"]:
                    raise OIDCError("userinfo subject does not match id_token")
                claims = {**info, **claims}
        return claims
