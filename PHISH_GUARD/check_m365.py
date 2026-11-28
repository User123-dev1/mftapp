"""Check PhishGuard's Microsoft 365 connection without changing anything. Prints no secrets.

    docker compose exec phishguard python check_m365.py

1. Signs in to Microsoft Graph with PG_M365_* (client credentials).
2. Reads the inbox item count of each protected mailbox (PG_M365_MAILBOXES): must work.
3. Tries the same for PG_SECURITY_EMAIL, which should be OUTSIDE the Exchange scope: must be refused.
Exit code 0 when everything is as expected.
"""
import sys
from urllib.parse import quote

from phishguard.config import Settings
from phishguard.graph import GraphClient, GraphError


def inbox_count(graph: GraphClient, mailbox: str) -> int:
    return graph.request("GET", f"/users/{quote(mailbox)}/mailFolders/inbox",
                         params={"$select": "totalItemCount"}).get("totalItemCount", 0)


def main() -> int:
    s = Settings()
    if not s.m365_configured:
        print("Microsoft 365: not configured (PG_M365_TENANT_ID / CLIENT_ID / CLIENT_SECRET missing)")
        return 0
    graph, ok = GraphClient(s.m365_tenant_id, s.m365_client_id, s.m365_client_secret), True
    try:
        graph._access_token()
        print("Microsoft 365 sign-in: OK")
    except GraphError as e:
        print(f"Microsoft 365 sign-in: FAILED ({e}) - check tenant ID, client ID and that the secret value is current")
        return 1
    if not s.m365_mailboxes:
        print("PG_M365_MAILBOXES is empty: PhishGuard would scan EVERY mailbox it's allowed to - set it for a pilot")
    for mb in s.m365_mailboxes:
        try:
            print(f"Protected mailbox {mb}: readable, {inbox_count(graph, mb)} item(s) in Inbox")
        except GraphError as e:
            ok = False
            hint = (" - Exchange permissions can take up to 2 hours to apply" if e.status == 403 else "")
            print(f"Protected mailbox {mb}: NOT readable (HTTP {e.status}){hint}")
    control = s.security_team_email
    if control and control.lower() not in {m.lower() for m in s.m365_mailboxes}:
        try:
            inbox_count(graph, control)
            ok = False
            print(f"Outside the pilot ({control}): READABLE - the Exchange scope is NOT limiting access!")
        except GraphError as e:
            print(f"Outside the pilot ({control}): refused (HTTP {e.status}) - access is limited as intended")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
