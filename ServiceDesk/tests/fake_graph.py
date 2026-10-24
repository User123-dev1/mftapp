"""A minimal Microsoft Graph + Entra token endpoint for testing the device sync."""

import socket
import threading
import time

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse


class FakeGraph:
    PAGE_SIZE = 2

    def __init__(self, tenant: str, client_id: str, client_secret: str):
        self.tenant, self.client_id, self.client_secret = tenant, client_id, client_secret
        self.devices: list[dict] = []
        self.fail_with: int | None = None   # force an HTTP status on /devices
        self.throttle_once = False
        self.requests: list[str] = []
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.app = self._build()

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.post("/{tenant}/oauth2/v2.0/token")
        def token(tenant: str, client_id: str = Form(...), client_secret: str = Form(...),
                  grant_type: str = Form(...), scope: str = Form(...)):
            if (tenant, client_id, client_secret) != (self.tenant, self.client_id, self.client_secret):
                return JSONResponse({"error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret"}, 401)
            assert grant_type == "client_credentials" and scope.endswith("/.default")
            return {"access_token": "graph-token", "token_type": "Bearer", "expires_in": 3600}

        @app.get("/v1.0/devices")
        def devices(request: Request):
            self.requests.append(str(request.url))
            if request.headers.get("authorization") != "Bearer graph-token":
                return JSONResponse({"error": {"code": "InvalidAuthenticationToken", "message": "bad token"}}, 401)
            if self.throttle_once:
                self.throttle_once = False
                return JSONResponse({"error": {"code": "TooManyRequests", "message": "slow down"}}, 429,
                                    headers={"Retry-After": "0"})
            if self.fail_with:
                return JSONResponse({"error": {"code": "Authorization_RequestDenied",
                                               "message": "Insufficient privileges"}}, self.fail_with)
            q = request.query_params
            if "$skiptoken" not in q:
                assert "registeredOwners" in q.get("$expand", "") and "displayName" in q.get("$select", "")
            start = int(q.get("$skiptoken", 0))
            page = self.devices[start:start + self.PAGE_SIZE]
            body = {"value": page}
            if start + self.PAGE_SIZE < len(self.devices):
                body["@odata.nextLink"] = f"{self.base}/v1.0/devices?$skiptoken={start + self.PAGE_SIZE}"
            return body

        return app

    def start(self):
        self._server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning"))
        threading.Thread(target=self._server.run, daemon=True).start()
        for _ in range(100):
            if self._server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("fake Graph did not start")

    def stop(self):
        self._server.should_exit = True


def device(object_id, name, os="Windows", version="10.0.22631", trust="AzureAd", enabled=True,
           owner=None, last_sign_in="2026-10-20T08:00:00Z", **extra):
    owners = []
    if owner:
        owners.append({"@odata.type": "#microsoft.graph.user", "id": f"u-{owner}", "mail": owner,
                       "userPrincipalName": owner, "displayName": owner.split("@")[0].title()})
    return {"id": object_id, "deviceId": f"dev-{object_id}", "displayName": name, "operatingSystem": os,
            "operatingSystemVersion": version, "trustType": trust, "accountEnabled": enabled,
            "approximateLastSignInDateTime": last_sign_in, "registeredOwners": owners, **extra}
