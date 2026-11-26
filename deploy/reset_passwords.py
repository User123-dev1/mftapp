"""Set the admin password of every app (or some) to one you choose, from the server.

    sudo python3 /opt/abelzeta/mftapp/deploy/reset_passwords.py
    sudo python3 /opt/abelzeta/mftapp/deploy/reset_passwords.py --apps servicedesk gmp

Asks for the password twice (not shown, not saved in the shell history). Each account is also
unlocked, re-enabled and signed out. Then the same password is stored in AWS Secrets Manager and in
this server's settings, so Secrets Manager stays the record of the current admin passwords and
diagnose.py keeps matching. The apps only use those settings to create the very first admin, so
installer runs and restarts never change a password you set here.
"""

import argparse
import getpass
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from diagnose import conf  # noqa: E402

# service, account shown to the user, secret key, command inside the container (reads stdin)
APPS = {
    "servicedesk": ("Service Desk", "SD_ADMIN_EMAIL", "SD_ADMIN_PASSWORD",
                    ["python", "reset_admin.py", "--password-stdin"]),
    "gmp": ("GMP Asset Manager", "admin", "GMP_ADMIN_PASSWORD",
            ["python", "reset_admin.py", "--password-stdin"]),
    "vault": ("Password Vault", "admin", "SS_ADMIN_PASSWORD",
              ["python", "-m", "secret_server", "reset-admin", "--password-stdin"]),
    "phishguard": ("PhishGuard", "admin", "PG_ADMIN_PASSWORD",
                   ["python", "reset_admin.py", "--password-stdin", "--no-force-change"]),
    "passwordstation": ("Password Station", "admin", "PS_ADMIN_PASSWORD",
                        ["python", "reset_admin.py", "--password-stdin"]),
}


def policy_problem(pw: str) -> str:
    """The strictest rules of all five apps, so one password works everywhere."""
    if len(pw) < 12:
        return "use at least 12 characters"
    if not (re.search(r"[A-Z]", pw) and re.search(r"[a-z]", pw) and re.search(r"\d", pw)
            and re.search(r"[^A-Za-z0-9]", pw)):
        return "use upper-case, lower-case, a digit and a symbol"
    if "admin" in pw.lower() or "manager" in pw.lower():
        return "don't include 'admin' or 'manager'"
    if pw != pw.strip():
        return "no spaces at the start or end"
    return ""


def update_env_files(updates: dict) -> None:
    for f in (HERE / "env").glob("*.env"):
        lines = f.read_text().splitlines()
        new = [f"{k}={updates[k]}" if (k := line.split("=", 1)[0]) in updates and "=" in line else line
               for line in lines]
        if new != lines:
            f.write_text("\n".join(new) + "\n")


def update_key_vault(vault: str, updates: dict) -> str:
    kv = ["python3", str(HERE / "azure" / "keyvault.py")]
    fd, path = tempfile.mkstemp(prefix="secret-", suffix=".json")
    os.close(fd)
    try:
        r = subprocess.run(kv + ["get", vault, path], capture_output=True, text=True)
        if r.returncode:
            return "could not read it: " + (r.stderr.strip().splitlines() or ["error"])[-1]
        data = json.loads(open(path).read() or "{}")
        data.update(updates)
        with open(path, "w") as fh:
            json.dump(data, fh)
        r = subprocess.run(kv + ["put", vault, path], capture_output=True, text=True)
        return "updated" if r.returncode == 0 else "could not save it: " + r.stderr.strip()[-200:]
    finally:
        os.unlink(path)


def update_secrets_manager(secret_id: str, updates: dict) -> str:
    region = secret_id.split(":")[3] if secret_id.startswith("arn:") else None
    reg = ["--region", region] if region else []
    r = subprocess.run(["aws", "secretsmanager", "get-secret-value", "--secret-id", secret_id, "--query",
                        "SecretString", "--output", "text"] + reg, capture_output=True, text=True)
    if r.returncode:
        return "could not read it: " + (r.stderr.strip().splitlines() or ["aws cli failed"])[-1]
    data = json.loads(r.stdout or "{}")
    data.update(updates)
    fd, path = tempfile.mkstemp(prefix="secret-", suffix=".json")   # mode 0600; not on the command line
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        r = subprocess.run(["aws", "secretsmanager", "put-secret-value", "--secret-id", secret_id,
                            "--secret-string", f"file://{path}"] + reg, capture_output=True, text=True)
    finally:
        os.unlink(path)
    return "updated" if r.returncode == 0 else "could not save it: " + r.stderr.strip()[-200:]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apps", nargs="+", choices=list(APPS), default=list(APPS))
    args = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("Run with sudo.")

    print("New admin password for: " + ", ".join(APPS[a][0] for a in args.apps))
    print("Rules: 12+ characters with upper-case, lower-case, a digit and a symbol.")
    while True:
        pw = getpass.getpass("New password: ")
        problem = policy_problem(pw)
        if problem:
            print(f"  Not allowed: {problem}.")
            continue
        if getpass.getpass("Again: ") != pw:
            print("  The two entries differ - try again.")
            continue
        break

    env = {}
    for f in (HERE / "env").glob("*.env"):
        for line in f.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                env[k] = v

    done = {}
    for app in args.apps:
        name, user, key, cmd = APPS[app]
        user = env.get(user, user) if user.isupper() else user
        r = subprocess.run(["docker", "compose", "exec", "-T", app] + cmd, cwd=HERE, input=pw + "\n",
                           capture_output=True, text=True)
        if r.returncode == 0:
            print(f"  OK      {name:18} sign in as {user}")
            done[key] = pw
        else:
            msg = (r.stderr.strip() or r.stdout.strip()).splitlines()
            print(f"  FAILED  {name:18} {msg[-1] if msg else 'error'}")

    if done:
        update_env_files(done)
        c = conf()
        secret_id = c.get("SECRET_ID", "")
        if c.get("KEY_VAULT"):
            print(f"Key Vault ({c['KEY_VAULT']}): {update_key_vault(c['KEY_VAULT'], done)}")
        elif secret_id:
            print(f"Secrets Manager ({secret_id.rsplit(':', 1)[-1]}): {update_secrets_manager(secret_id, done)}")
    print("\nSign in with the new password (type it - don't let the browser fill in an old one).")
    return 0 if len(done) == len(args.apps) else 1


if __name__ == "__main__":
    sys.exit(main())
