"""Microsoft 365 / Exchange Online through Microsoft Graph.

App registration (Entra ID), application permissions with admin consent:
  Mail.ReadWrite  - read new mail, move to quarantine, purge
  Mail.Send       - alert users and the security team (from PG_ALERT_FROM)
  User.Read.All   - enumerate mailboxes (unless PG_M365_MAILBOXES lists them)
Restrict the app to the right mailboxes with an Exchange RBAC for Applications
scope or an application access policy.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.parse import quote

logger = logging.getLogger("phishguard.graph")
GRAPH = "https://graph.microsoft.com/v1.0"


class GraphError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Graph {status}: {message}")
        self.status = status


class GraphClient:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str, http=None):
        self.tenant_id, self.client_id, self.client_secret = tenant_id, client_id, client_secret
        self._http = http
        self._token: Optional[Tuple[str, float]] = None

    @property
    def http(self):
        if self._http is None:
            import httpx
            self._http = httpx.Client(timeout=30)
        return self._http

    def _access_token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        resp = self.http.post(
            f"https://login.microsoftonline.com/{quote(self.tenant_id)}/oauth2/v2.0/token",
            data={"grant_type": "client_credentials", "client_id": self.client_id,
                  "client_secret": self.client_secret, "scope": "https://graph.microsoft.com/.default"})
        if resp.status_code != 200:
            raise GraphError(resp.status_code, resp.text[:300])
        body = resp.json()
        self._token = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
        return self._token[0]

    def request(self, method: str, path_or_url: str, *, json=None, params=None, raw: bool = False,
                headers: Optional[Dict[str, str]] = None):
        url = path_or_url if path_or_url.startswith("https://") else GRAPH + path_or_url
        for attempt in range(4):
            resp = self.http.request(method, url, json=json, params=params,
                                     headers={"Authorization": f"Bearer {self._access_token()}", **(headers or {})})
            if resp.status_code in (429, 503, 504) and attempt < 3:
                time.sleep(min(int(resp.headers.get("Retry-After", "2") or 2), 30))
                continue
            break
        if resp.status_code >= 400:
            raise GraphError(resp.status_code, resp.text[:300])
        if raw:
            return resp.content
        return resp.json() if resp.content else {}

    def _paged(self, path: str, params=None) -> Iterator[dict]:
        page = self.request("GET", path, params=params)
        while True:
            yield from page.get("value", [])
            nxt = page.get("@odata.nextLink")
            if not nxt:
                return
            page = self.request("GET", nxt)

    # -- mailboxes ------------------------------------------------------------
    def mailboxes(self) -> List[str]:
        users = self._paged("/users", {"$select": "mail,userPrincipalName,accountEnabled",
                                       "$filter": "accountEnabled eq true", "$top": "200"})
        return sorted({(u.get("mail") or "").lower() for u in users if u.get("mail")})

    def inbox_delta(self, mailbox: str, delta_link: Optional[str]) -> Tuple[List[str], str]:
        """New/changed message ids in the inbox since the last call, and the next delta link."""
        url = delta_link or (f"/users/{quote(mailbox)}/mailFolders/inbox/messages/delta"
                             "?$select=id,internetMessageId,receivedDateTime")
        ids: List[str] = []
        page = self.request("GET", url, headers={"Prefer": "odata.maxpagesize=50"})
        while True:
            ids += [m["id"] for m in page.get("value", []) if "@removed" not in m]
            if page.get("@odata.nextLink"):
                page = self.request("GET", page["@odata.nextLink"], headers={"Prefer": "odata.maxpagesize=50"})
                continue
            return ids, page.get("@odata.deltaLink", delta_link or "")

    def get_message(self, mailbox: str, message_id: str) -> dict:
        return self.request("GET", f"/users/{quote(mailbox)}/messages/{quote(message_id)}",
                            params={"$select": "id,internetMessageId,receivedDateTime,parentFolderId,subject"})

    def get_mime(self, mailbox: str, message_id: str) -> bytes:
        return self.request("GET", f"/users/{quote(mailbox)}/messages/{quote(message_id)}/$value", raw=True)

    # -- remediation ------------------------------------------------------------
    def quarantine_folder(self, mailbox: str, name: str) -> str:
        """Hidden mail folder used as the quarantine (not shown in Outlook)."""
        found = self.request("GET", f"/users/{quote(mailbox)}/mailFolders",
                             params={"$filter": f"displayName eq '{name.replace(chr(39), chr(39) * 2)}'",
                                     "includeHiddenFolders": "true"})
        if found.get("value"):
            return found["value"][0]["id"]
        created = self.request("POST", f"/users/{quote(mailbox)}/mailFolders",
                               json={"displayName": name, "isHidden": True})
        return created["id"]

    def move(self, mailbox: str, message_id: str, destination: str) -> str:
        """Move to a folder id or well-known name ('inbox'). Returns the message's new id."""
        moved = self.request("POST", f"/users/{quote(mailbox)}/messages/{quote(message_id)}/move",
                             json={"destinationId": destination})
        return moved.get("id", message_id)

    def permanent_delete(self, mailbox: str, message_id: str) -> None:
        self.request("POST", f"/users/{quote(mailbox)}/messages/{quote(message_id)}/permanentDelete")

    def tag(self, mailbox: str, message_id: str, category: str) -> None:
        self.request("PATCH", f"/users/{quote(mailbox)}/messages/{quote(message_id)}",
                     json={"categories": [category], "flag": {"flagStatus": "flagged"}})

    def find_by_internet_message_id(self, mailbox: str, internet_message_id: str) -> List[dict]:
        imid = internet_message_id.replace("'", "''")
        return list(self._paged(f"/users/{quote(mailbox)}/messages",
                                {"$filter": f"internetMessageId eq '{imid}'", "$select": "id,parentFolderId,subject"}))

    def send_mail(self, from_mailbox: str, to: List[str], subject: str, html_body: str) -> None:
        self.request("POST", f"/users/{quote(from_mailbox)}/sendMail", json={
            "message": {"subject": subject, "body": {"contentType": "HTML", "content": html_body},
                        "toRecipients": [{"emailAddress": {"address": a}} for a in to]},
            "saveToSentItems": False})
