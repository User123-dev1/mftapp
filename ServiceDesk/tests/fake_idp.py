"""A minimal OpenID Connect provider for tests: auto-approves every sign-in.

Set ``FakeIdP.next_claims`` before starting a login to choose who signs in.
It serves both a generic discovery document and Entra-style tenant paths.
"""

import secrets
import socket
import threading
import time

import uvicorn
from authlib.jose import JsonWebKey, jwt
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse


class FakeIdP:
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.key = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "test-key"})
        self.next_claims: dict = {}
        self.codes: dict[str, dict] = {}
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.app = self._build()
        self._server = None

    def issuer(self, tenant: str = "") -> str:
        return f"{self.base}/{tenant}/v2.0" if tenant else self.base

    def _build(self) -> FastAPI:
        app = FastAPI()

        def discovery(issuer: str):
            return {
                "issuer": issuer,
                "authorization_endpoint": f"{self.base}/authorize?iss={issuer}",
                "token_endpoint": f"{self.base}/token",
                "jwks_uri": f"{self.base}/jwks",
                "response_types_supported": ["code"],
                "id_token_signing_alg_values_supported": ["RS256"],
            }

        @app.get("/.well-known/openid-configuration")
        def generic():
            return discovery(self.issuer())

        @app.get("/{tenant}/v2.0/.well-known/openid-configuration")
        def entra(tenant: str):
            return discovery(self.issuer(tenant))

        @app.get("/authorize")
        def authorize(request: Request):
            q = request.query_params
            if q.get("client_id") != self.client_id:
                return JSONResponse({"error": "unauthorized_client"}, status_code=400)
            code = secrets.token_urlsafe(16)
            self.codes[code] = {"nonce": q.get("nonce"), "iss": q["iss"], "redirect_uri": q["redirect_uri"]}
            return RedirectResponse(f"{q['redirect_uri']}?code={code}&state={q['state']}", status_code=302)

        @app.post("/token")
        def token(code: str = Form(...), redirect_uri: str = Form(...)):
            grant = self.codes.pop(code, None)
            if grant is None or grant["redirect_uri"] != redirect_uri:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            now = int(time.time())
            claims = {"iss": grant["iss"], "aud": self.client_id, "iat": now, "exp": now + 300, "nonce": grant["nonce"]}
            claims.update(self.next_claims)
            id_token = jwt.encode({"alg": "RS256", "kid": "test-key"}, claims, self.key).decode()
            return {"access_token": "at", "token_type": "Bearer", "expires_in": 300, "id_token": id_token}

        @app.get("/jwks")
        def jwks():
            return {"keys": [self.key.as_dict(is_private=False)]}

        return app

    def start(self):
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        threading.Thread(target=self._server.run, daemon=True).start()
        for _ in range(100):
            if self._server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("fake IdP did not start")

    def stop(self):
        if self._server:
            self._server.should_exit = True
