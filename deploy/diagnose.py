"""Check that each app's admin password matches AWS Secrets Manager - prints no passwords.

    sudo python3 /opt/abelzeta/mftapp/deploy/diagnose.py

For every secret: is it in Secrets Manager, and is it the value the running apps use?
Then signs in to each app from the server itself with the value the app was set up with.
"""

import hashlib
import http.cookiejar
import json
import re
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
            out[k] = v
    return out


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


def opener():
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX),
                                       urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                       urllib.request.ProxyHandler({}))


def json_login(port, username, password):
    req = urllib.request.Request(f"https://127.0.0.1:{port}/api/auth/login",
                                 json.dumps({"username": username, "password": password}).encode(),
                                 {"Content-Type": "application/json"})
    try:
        body = json.loads(opener().open(req, timeout=10).read())
        return "OK" if any(k in body for k in ("token", "access_token")) else f"unexpected reply {sorted(body)}"
    except urllib.error.HTTPError as e:
        return f"FAILED ({e.code}: {e.read()[:120].decode(errors='replace')})"
    except Exception as e:  # noqa: BLE001
        return f"FAILED ({e})"


def servicedesk_login(email, password):
    op = opener()
    try:
        page = op.open("https://127.0.0.1:8443/login", timeout=10).read().decode()
        token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
        data = urllib.parse.urlencode({"email": email, "password": password, "csrf_token": token, "next": "/"})
        r = op.open("https://127.0.0.1:8443/login", data.encode(), timeout=10)
        return "OK" if "/login" not in r.geturl() else "FAILED (back at the login page)"
    except urllib.error.HTTPError as e:
        return f"FAILED ({e.code})"
    except Exception as e:  # noqa: BLE001
        return f"FAILED ({e})"


def main():
    c, env = conf(), env_values()
    secret_id = c.get("SECRET_ID", "")
    print(f"Secret this server uses: {secret_id or '(none)'}")
    sm, err = secrets_manager(secret_id)
    if err:
        print(f"  Secrets Manager: {err}")
    local = BASE / "secrets.json"
    if local.exists():
        print(f"  NOTE: {local} exists - passwords kept only on this server (not yet in Secrets Manager)")
    print("\nSecret                In Secrets Manager   Matches running apps   (fingerprints, not passwords)")
    for key in ["SD_ADMIN_PASSWORD", "GMP_ADMIN_PASSWORD", "SS_ADMIN_PASSWORD", "PG_ADMIN_PASSWORD",
                "SS_MASTER_KEY", "SD_SECRET_KEY", "PG_SECRET_KEY"]:
        aws_v, run_v = (sm or {}).get(key, ""), env.get(key, "")
        print(f"{key:21} {'yes' if aws_v else 'NO':20} {'yes' if aws_v and aws_v == run_v else 'NO':22} "
              f"aws {fp(aws_v)}  running {fp(run_v)}")

    print("\nSign-in test from this server, with the passwords the apps were set up with:")
    print(f"  Service Desk  {env.get('SD_ADMIN_EMAIL', '?'):24} {servicedesk_login(env.get('SD_ADMIN_EMAIL', ''), env.get('SD_ADMIN_PASSWORD', ''))}")
    print(f"  GMP Assets    {'admin':24} {json_login(8444, 'admin', env.get('GMP_ADMIN_PASSWORD', ''))}")
    print(f"  Vault         {'admin':24} {json_login(8445, 'admin', env.get('SS_ADMIN_PASSWORD', ''))}")
    print(f"  PhishGuard    {'admin':24} {json_login(8446, 'admin', env.get('PG_ADMIN_PASSWORD', ''))}")
    print("\nIf a sign-in FAILS here, that app's admin password was changed or set differently on first start;"
          "\nif it is OK here but not in your browser, the value you're using differs from Secrets Manager.")


if __name__ == "__main__":
    main()
