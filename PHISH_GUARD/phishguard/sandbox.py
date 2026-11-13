"""Attachment detonation in CAPE Sandbox (CAPEv2 REST API).

CAPE runs the file inside an isolated, instrumented virtual machine and reports
what it did (processes, network, dropped files, signatures) with a 0-10
"malscore". PhishGuard submits suspicious attachments, polls for the result and
remediates if the sandbox convicts a file that was already delivered.

Install CAPE on a separate, network-isolated host: https://github.com/kevoreilly/CAPEv2
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

logger = logging.getLogger("phishguard.sandbox")


class CapeSandbox:
    def __init__(self, base_url: str, token: str = "", http=None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._http = http

    @property
    def http(self):
        if self._http is None:
            import httpx
            headers = {"Authorization": f"Token {self.token}"} if self.token else {}
            self._http = httpx.Client(timeout=30, headers=headers)
        return self._http

    def submit(self, filename: str, data: bytes) -> Optional[str]:
        resp = self.http.post(f"{self.base_url}/apiv2/tasks/create/file/",
                              files={"file": (filename, data)}, data={"timeout": "180"})
        resp.raise_for_status()
        body = resp.json()
        ids = (body.get("data") or {}).get("task_ids") or []
        if body.get("error") or not ids:
            raise RuntimeError(f"CAPE rejected the submission: {body.get('error_value') or body}")
        return str(ids[0])

    def result(self, task_id: str) -> Tuple[str, Optional[float], Optional[dict]]:
        """('pending'|'done'|'failed', malscore, summary)."""
        status = self.http.get(f"{self.base_url}/apiv2/tasks/status/{task_id}/").json()
        state = str(status.get("data", "")).lower()
        if state in ("failed_analysis", "failed_processing", "failed_reporting"):
            return "failed", None, None
        if state != "reported":
            return "pending", None, None
        report = self.http.get(f"{self.base_url}/apiv2/tasks/get/report/{task_id}/").json()
        malscore = report.get("malscore")
        if malscore is None:
            malscore = (report.get("info") or {}).get("score")
        signatures = [s.get("description") or s.get("name") for s in report.get("signatures", [])][:15]
        family = (report.get("detections") or report.get("malfamily") or "")
        return "done", float(malscore or 0), {"signatures": signatures, "family": family}
