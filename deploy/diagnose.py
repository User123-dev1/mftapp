"""Check that each app's admin password matches AWS Secrets Manager - prints no passwords.

    sudo python3 /opt/abelzeta/mftapp/deploy/diagnose.py

For every secret: is it in Secrets Manager, and is it the value the running apps use?
Then signs in to each app from the server itself with the value the app was set up with.
"""

import hashlib
import http.client
import http.cookiejar
import json
import re
import socket
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path("/opt/abelzeta")


def env_values() -> dict:
    values = {}
    for f in sorted((HERE / "env").glob("*.env")):
        for line in f.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                values[k] = v
    return values


def conf() -> dict:
    out = {}
    p = BASE / "install.conf"
    for line in p.read_text().splitlines() if p.exists() else []:
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v.strip("'")
    return out


def key_vault(vault: str):
    tmp = BASE / "diagnose-kv.json"
    r = subprocess.run(["python3", str(HERE / "azure" / "keyvault.py"), "get", vault, str(tmp)],
                       capture_output=True, text=True)
    try:
        if r.returncode:
            return None, (r.stderr.strip().splitlines() or ["Key Vault read failed"])[-1]
        return json.loads(tmp.read_text() or "{}"), None
    finally:
        tmp.unlink(missing_ok=True)


def secrets_manager(secret_id: str):
    if not secret_id:
        return None, "no SECRET_ID in install.conf"
    region = secret_id.split(":")[3] if secret_id.startswith("arn:") else None
    cmd = ["aws", "secretsmanager", "get-secret-value", "--secret-id", secret_id, "--query", "SecretString",
           "--output", "text"] + (["--region", region] if region else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        return None, r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "aws cli failed"
    try:
        return json.loads(r.stdout or "{}"), None
    except ValueError:
        return None, "secret is not JSON"


def fp(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:8] if value else "-"


CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


class _LocalHTTPS(http.client.HTTPSConnection):
    """Connect to this server (127.0.0.1) while using the public name for TLS and the Host header,
    exactly like a browser - the proxy only serves its configured addresses."""

    def connect(self):
        sock = socket.create_connection(("127.0.0.1", self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _LocalHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_LocalHTTPS, req, context=CTX)


def opener():
    return urllib.request.build_opener(_LocalHandler(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                       urllib.request.ProxyHandler({}))


def app_url(c: dict, sub: str, port: int) -> str:
    if c.get("DOMAIN"):
        return f"https://{sub}.{c['DOMAIN']}"
    return f"https://{c.get('PUBLIC_HOST', '127.0.0.1')}:{port}"


def json_login(base, username, password):
    req = urllib.request.Request(f"{base}/api/auth/login",
                                 json.dumps({"username": username, "password": password}).encode(),
                                 {"Content-Type": "application/json"})
    try:
        raw = opener().open(req, timeout=10).read()
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            return f"PASSWORD REFUSED ({e.code}: {e.read()[:120].decode(errors='replace')})"
        return f"APP ERROR ({e.code})"
    except Exception as e:  # noqa: BLE001
        return f"NO RESPONSE ({e})"
    try:
        body = json.loads(raw)
    except ValueError:
        return "NO RESPONSE (empty or non-JSON page - check the proxy / address)"
    return "OK" if any(k in body for k in ("token", "access_token")) else f"unexpected reply {sorted(body)}"


def station_login(base, username, password):
    op = opener()
    try:
        page = op.open(f"{base}/admin/login", timeout=10).read().decode()
        m = re.search(r'name="csrf_token" value="([^"]+)"', page)
        if not m:
            return "NO RESPONSE (login page did not load - check the proxy / address)"
        data = urllib.parse.urlencode({"username": username, "password": password, "csrf_token": m.group(1)})
        r = op.open(f"{base}/admin/login", data.encode(), timeout=10)
        return "OK" if r.geturl().rstrip("/").endswith("/admin") else "PASSWORD REFUSED"
    except urllib.error.HTTPError as e:
        return "PASSWORD REFUSED" if e.code == 400 else f"APP ERROR ({e.code})"
    except Exception as e:  # noqa: BLE001
        return f"NO RESPONSE ({e})"


def servicedesk_login(base, email, password):
    op = opener()
    try:
        page = op.open(f"{base}/login", timeout=10).read().decode()
        m = re.search(r'name="csrf-token" content="([^"]+)"', page)
        if not m:
            return "NO RESPONSE (login page did not load - check the proxy / address)"
        data = urllib.parse.urlencode({"email": email, "password": password, "csrf_token": m.group(1), "next": "/"})
        r = op.open(f"{base}/login", data.encode(), timeout=10)
        return "OK" if "/login" not in r.geturl() else "PASSWORD REFUSED"
    except urllib.error.HTTPError as e:
        return "PASSWORD REFUSED" if e.code == 400 else f"APP ERROR ({e.code})"
    except Exception as e:  # noqa: BLE001
        return f"NO RESPONSE ({e})"


def main():
    c, env = conf(), env_values()
    secret_id = c.get("SECRET_ID", "")
    if c.get("KEY_VAULT"):
        print(f"Key Vault this server uses: {c['KEY_VAULT']} (secret 'portal-secrets')")
        sm, err = key_vault(c["KEY_VAULT"])
    else:
        print(f"Secret this server uses: {secret_id or '(none)'}")
        sm, err = secrets_manager(secret_id)
    if err:
        print(f"  Secrets Manager: {err}")
    local = BASE / "secrets.json"
    if local.exists():
        print(f"  NOTE: {local} exists - passwords kept only on this server (not yet in Secrets Manager)")
    print("\nSecret                In secret store      Matches running apps   (fingerprints, not passwords)")
    for key in ["SD_ADMIN_PASSWORD", "GMP_ADMIN_PASSWORD", "SS_ADMIN_PASSWORD", "PG_ADMIN_PASSWORD",
                "PS_ADMIN_PASSWORD", "SS_MASTER_KEY", "SD_SECRET_KEY", "PG_SECRET_KEY", "PS_SECRET_KEY",
                "PS_DATA_KEY"]:
        aws_v, run_v = (sm or {}).get(key, ""), env.get(key, "")
        print(f"{key:21} {'yes' if aws_v else 'NO':20} {'yes' if aws_v and aws_v == run_v else 'NO':22} "
              f"aws {fp(aws_v)}  running {fp(run_v)}")

    print("\nSign-in test from this server, with the passwords the apps were set up with:")
    sd = env.get("SD_ADMIN_EMAIL", "")
    print(f"  Service Desk  {sd:24} {servicedesk_login(app_url(c, 'servicedesk', 8443), sd, env.get('SD_ADMIN_PASSWORD', ''))}")
    print(f"  GMP Assets    {'admin':24} {json_login(app_url(c, 'assets', 8444), 'admin', env.get('GMP_ADMIN_PASSWORD', ''))}")
    print(f"  Vault         {'admin':24} {json_login(app_url(c, 'vault', 8445), 'admin', env.get('SS_ADMIN_PASSWORD', ''))}")
    print(f"  PhishGuard    {'admin':24} {json_login(app_url(c, 'phishguard', 8446), 'admin', env.get('PG_ADMIN_PASSWORD', ''))}")
    print(f"  Pwd Station   {'admin':24} {station_login(app_url(c, 'passwords', 8448 if c.get('URL_PATTERN') else 8447), 'admin', env.get('PS_ADMIN_PASSWORD', ''))}")
    print("\nOK here but not in your browser: the value you're entering differs from the secret above."
          "\nPASSWORD REFUSED here: that admin password was changed in the app (or PhishGuard / Password Station is locked:"
          "\n  run reset_admin.py). A refused password counts toward the app's lockout, so don't repeat this"
          "\n  more than a couple of times in a row.")


if __name__ == "__main__":
    main()
