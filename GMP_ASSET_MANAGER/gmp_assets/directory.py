"""Look up people in the company directory to create users and approvers.

Two sources:

* Microsoft 365 / Entra ID through Microsoft Graph (client-credentials flow).
  Needs an app registration with the *User.Read.All* application permission
  and admin consent; configure GMP_M365_TENANT_ID / _CLIENT_ID / _CLIENT_SECRET.
* A CSV file exported from the Microsoft 365 admin centre
  (Users > Active users > Export users) or any CSV with name and e-mail columns.
"""

import csv
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List

from .config import Settings

GRAPH = "https://graph.microsoft.com/v1.0"
_token_cache: Dict[str, tuple] = {}


class DirectoryError(Exception):
    pass


def is_configured(settings: Settings) -> bool:
    return bool(settings.m365_tenant_id and settings.m365_client_id and settings.m365_client_secret)


def _token(settings: Settings) -> str:
    cached = _token_cache.get(settings.m365_client_id)
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials", "client_id": settings.m365_client_id,
        "client_secret": settings.m365_client_secret, "scope": "https://graph.microsoft.com/.default",
    }).encode()
    url = f"https://login.microsoftonline.com/{urllib.parse.quote(settings.m365_tenant_id)}/oauth2/v2.0/token"
    payload = _http_json(urllib.request.Request(url, data=data, method="POST"))
    _token_cache[settings.m365_client_id] = (payload["access_token"], time.time() + int(payload.get("expires_in", 3600)))
    return payload["access_token"]


def _http_json(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise DirectoryError(f"Microsoft 365 returned {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise DirectoryError(f"Cannot reach Microsoft 365: {exc}") from exc


def _person(display_name: str, email: str, upn: str = "", department: str = "", job_title: str = "") -> dict:
    email = (email or upn or "").strip()
    local = email.split("@")[0] if "@" in email else ""
    username = re.sub(r"[^A-Za-z0-9._-]", "", local)[:40]
    return {"full_name": (display_name or "").strip(), "email": email, "username": username,
            "department": (department or "").strip(), "job_title": (job_title or "").strip()}


def search_m365(settings: Settings, query: str) -> List[dict]:
    if not is_configured(settings):
        raise DirectoryError("Microsoft 365 lookup is not configured")
    q = (query or "").strip().replace("'", "''")
    if len(q) < 2:
        raise DirectoryError("Type at least 2 characters")
    flt = f"startswith(displayName,'{q}') or startswith(mail,'{q}') or startswith(userPrincipalName,'{q}') or startswith(surname,'{q}')"
    url = f"{GRAPH}/users?" + urllib.parse.urlencode({
        "$filter": flt, "$select": "displayName,mail,userPrincipalName,department,jobTitle,accountEnabled", "$top": "25"})
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_token(settings)}"})
    users = _http_json(req).get("value", [])
    return [_person(u.get("displayName"), u.get("mail"), u.get("userPrincipalName"), u.get("department"), u.get("jobTitle"))
            for u in users if u.get("accountEnabled", True)]


_NAME_COLS = ("display name", "displayname", "name", "full name")
_EMAIL_COLS = ("email", "e-mail", "mail", "email address", "primary email", "user principal name", "userprincipalname", "upn")
_DEPT_COLS = ("department",)
_TITLE_COLS = ("title", "job title", "jobtitle")


def parse_csv(text: str) -> List[dict]:
    """Parse a Microsoft 365 admin-centre user export (or similar CSV) into people."""
    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise DirectoryError("The CSV file is empty")
    cols = {name.strip().lower(): name for name in reader.fieldnames}

    def pick(options):
        return next((cols[o] for o in options if o in cols), None)

    name_col, email_col = pick(_NAME_COLS), pick(_EMAIL_COLS)
    if not name_col or not email_col:
        raise DirectoryError("CSV needs a name column (e.g. 'Display name') and an e-mail column (e.g. 'User principal name')")
    dept_col, title_col = pick(_DEPT_COLS), pick(_TITLE_COLS)
    people = []
    for row in reader:
        person = _person(row.get(name_col), row.get(email_col), "", row.get(dept_col) if dept_col else "",
                         row.get(title_col) if title_col else "")
        if person["full_name"] and "@" in person["email"]:
            people.append(person)
    return people
