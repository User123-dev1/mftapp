"""Read / write the portal's secrets (one JSON document) in Azure Key Vault from the Azure VM.

    python3 keyvault.py get  <vault-name> <out-file>    # writes {} if the secret doesn't exist yet
    python3 keyvault.py put  <vault-name> <in-file>

`get` also merges any separate secrets named after a setting, e.g. PG-M365-CLIENT-SECRET ->
PG_M365_CLIENT_SECRET (prefixes PG, PS, SD, GMP, SS, ANTHROPIC). Those win over portal-secrets, so
a setting can be added or changed in the portal without editing the JSON document.

Signs in with the VM's managed identity (Azure Instance Metadata Service), so no key or password is
stored on the server. Standard library only (runs before anything else is installed).
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SECRET = "portal-secrets"
API = "7.4"
IMDS = "http://169.254.169.254/metadata/identity/oauth2/token"


def token() -> str:
    url = f"{IMDS}?api-version=2018-02-01&resource={urllib.parse.quote('https://vault.azure.net')}"
    req = urllib.request.Request(url, headers={"Metadata": "true"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # IMDS is never proxied
    return json.loads(opener.open(req, timeout=10).read())["access_token"]


def call(method: str, vault: str, body=None):
    url = f"https://{vault}.vault.azure.net/secrets/{SECRET}?api-version={API}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read() or b"{}")


SETTING_SECRET = re.compile(r"^(PG|PS|SD|GMP|SS|ANTHROPIC)(-[A-Za-z0-9]+)+$")


def _get_url(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token()}"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read() or b"{}")


def setting_secrets(vault: str, fetch=_get_url) -> dict:
    """{SETTING_NAME: value} for enabled secrets named like PG-M365-CLIENT-SECRET."""
    out, url = {}, f"https://{vault}.vault.azure.net/secrets?api-version={API}&maxresults=25"
    while url:
        page = fetch(url)
        for item in page.get("value", []):
            name = item["id"].rstrip("/").rsplit("/", 1)[-1]
            if SETTING_SECRET.match(name) and item.get("attributes", {}).get("enabled", True):
                out[name.replace("-", "_").upper()] = fetch(f"{item['id']}?api-version={API}")["value"]
        url = page.get("nextLink")
    return out


def main() -> int:
    if len(sys.argv) != 4 or sys.argv[1] not in ("get", "put"):
        print(__doc__, file=sys.stderr)
        return 2
    action, vault, path = sys.argv[1:]
    # A brand-new VM's access can take a minute or two to apply: retry before giving up.
    for attempt in range(1, 11):
        try:
            if action == "get":
                try:
                    value = call("GET", vault)["value"]
                except urllib.error.HTTPError as e:
                    if e.code != 404:
                        raise
                    value = "{}"
                doc = json.loads(value)                # must be JSON
                doc.update(setting_secrets(vault))
                with open(path, "w") as fh:
                    json.dump(doc, fh)
            else:
                with open(path) as fh:
                    value = fh.read()
                call("PUT", vault, {"value": value, "contentType": "application/json"})
            return 0
        except (urllib.error.URLError, OSError, KeyError) as e:
            detail = e.read().decode(errors="replace")[:300] if isinstance(e, urllib.error.HTTPError) else str(e)
            if attempt == 10:
                print(f"Key Vault {action} failed: {detail}", file=sys.stderr)
                return 1
            print(f"Key Vault not reachable yet ({detail[:120]}) - retrying in 15 s ({attempt}/10)", file=sys.stderr)
            time.sleep(15)
    return 1


if __name__ == "__main__":
    sys.exit(main())
