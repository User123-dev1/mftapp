"""Generate the deployment's runtime files from secrets + addressing mode.

    python3 configure.py --secrets secrets.json --host 203.0.113.10          # trial: one IP, a port per app
    python3 configure.py --secrets secrets.json --domain abelzeta.com         # production: a subdomain per app

Writes, next to this script:
  .env                 compose settings (data directory only)
  env/<app>.env        each app's own settings and secrets - an app never sees another's (mode 0600)
  Caddyfile            reverse proxy / HTTPS
  apps.json            the portal's app catalogue
Missing secrets are generated and written back to the --secrets file, so the caller
can store them (e.g. in AWS Secrets Manager). Existing values are never replaced.
"""

from __future__ import annotations  # Amazon Linux 2023 ships Python 3.9

import argparse
import base64
import json
import os
import secrets
import string
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# name, trial port, subdomain, internal upstream, health path, portal card
APPS = [
    {
        "id": "service-desk", "port": 8443, "sub": "servicedesk", "upstream": "servicedesk:8085",
        "health": "/login", "name": "IT Service Desk",
        "description": "Raise IT tickets, service status, change management, CMDB and AbelOrder purchase requests.",
        "category": "IT", "icon": "support_agent", "color": "#16a34a", "owner": "IT Support",
    },
    {
        "id": "gmp-assets", "port": 8444, "sub": "assets", "upstream": "gmp:8000",
        "health": "/api/health", "name": "GMP Asset Manager",
        "description": "GMP equipment register, calibration / PM schedules, work orders and QA approvals.",
        "category": "Quality", "icon": "precision_manufacturing", "color": "#0891b2", "owner": "Engineering & QA",
    },
    {
        "id": "password-vault", "port": 8445, "sub": "vault", "upstream": "vault:8090",
        "health": "/health", "path": "/docs", "name": "Password Vault (admin API)",
        "description": "Enterprise privileged password vault. Administrators only for now: opens the API console.",
        "category": "Security", "icon": "lock", "color": "#7c3aed", "owner": "IT Security",
    },
    {
        "id": "phishguard", "port": 8446, "sub": "phishguard", "upstream": "phishguard:8100",
        "health": "/health", "name": "PhishGuard",
        "description": "Phishing, BEC and malware e-mail protection: test suspicious e-mails, quarantine and reports.",
        "category": "Security", "icon": "phishing", "color": "#dc2626", "owner": "IT Security",
    },
    {
        "id": "password-station", "port": 8447, "sub": "passwords", "upstream": "passwordstation:8200",
        "health": "/health", "name": "Password Station",
        "description": "Change your password, reset a forgotten one or unlock your account - GMP and business domains.",
        "category": "Self-service", "icon": "password", "color": "#006ea1", "owner": "IT Support",
    },
]

REQUIRED_SECRETS = {
    "SD_SECRET_KEY": lambda: secrets.token_urlsafe(48),
    "SD_ADMIN_PASSWORD": lambda: strong_password(),
    "GMP_ADMIN_PASSWORD": lambda: strong_password(),
    "SS_ADMIN_PASSWORD": lambda: strong_password(),
    # AES-256 master key for the vault: base64 of 32 random bytes.
    "SS_MASTER_KEY": lambda: base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
    "PG_ADMIN_PASSWORD": lambda: strong_password(),
    # Signs PhishGuard's report / click-check links; must stay the same across restarts.
    "PG_SECRET_KEY": lambda: secrets.token_urlsafe(48),
    "PS_ADMIN_PASSWORD": lambda: strong_password(),
    # Password Station: signs sessions and one-time codes; AES-256 key for recovery contacts and
    # directory service-account passwords. Never change PS_DATA_KEY once users have enrolled.
    "PS_SECRET_KEY": lambda: secrets.token_urlsafe(48),
    "PS_DATA_KEY": lambda: base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
}


# Optional Password Station settings: add these keys to the Secrets Manager secret and re-run
# install.sh. They're copied into its env file as they are (codes by e-mail / SMS).
PS_OPTIONAL = ["PS_SMTP_HOST", "PS_SMTP_PORT", "PS_SMTP_USER", "PS_SMTP_PASSWORD", "PS_SMTP_FROM",
               "PS_SMTP_STARTTLS", "PS_SMS_PROVIDER", "PS_SMS_SENDER_ID", "PS_SUPPORT_CONTACT"]


def strong_password(length: int = 20) -> str:
    """Random password with upper, lower, digit and symbol (meets every app's policy)."""
    alphabet = string.ascii_letters + string.digits + "-_.!@#%"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw)
                and any(c in "-_.!@#%" for c in pw)):
            return pw


def read_env_files(env_dir: Path) -> dict:
    """Values the apps are running with now (from a previous run's env/*.env)."""
    values = {}
    for f in sorted(env_dir.glob("*.env")) if env_dir.is_dir() else []:
        for line in f.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                values[k.strip()] = v
    return values


def complete_secrets(path: Path, env_dir: Path) -> tuple[dict, list[str], list[str]]:
    """Fill in missing secrets. A secret the apps already run with is reused, never replaced:
    regenerating e.g. the vault master key would make existing data unreadable."""
    data = json.loads(path.read_text()) if path.exists() and path.read_text().strip() else {}
    running = read_env_files(env_dir)
    added, recovered = [], []
    for key, make in REQUIRED_SECRETS.items():
        if data.get(key):
            continue
        if running.get(key):
            data[key] = running[key]
            recovered.append(key)
        else:
            data[key] = make()
            added.append(key)
    return data, added, recovered


# Behind Microsoft Entra Application Proxy the server keeps its internal (host) addresses, while
# users and links use the published external addresses: --url-pattern "https://{sub}-tenant.msappproxy.net".
PS_ADMIN_PORT = 8448      # Password Station admin console, published separately (Entra sign-in required)


def public_urls(host: str | None, domain: str | None, pattern: str | None = None) -> dict:
    if pattern:
        urls = {"portal": pattern.format(sub="portal")}
        urls.update({a["id"]: pattern.format(sub=a["sub"]) for a in APPS})
    elif domain:
        urls = {"portal": f"https://portal.{domain}"}
        urls.update({a["id"]: f"https://{a['sub']}.{domain}" for a in APPS})
    else:
        urls = {"portal": f"https://{host}"}
        urls.update({a["id"]: f"https://{host}:{a['port']}" for a in APPS})
    return urls


def caddyfile(host: str | None, domain: str | None, email: str | None, split_ps_admin: bool = False,
              internal_name: str | None = None) -> str:
    lines = ["# Generated by deploy/configure.py - do not edit by hand.", "{"]
    if domain and email:
        lines.append(f"\temail {email}")
    if not domain:
        # Browsers send no SNI when you browse to an IP address; without this Caddy can't pick a
        # certificate and aborts the handshake (ERR_SSL_PROTOCOL_ERROR).
        lines.append(f"\tdefault_sni {host}")
    lines += ["}", ""]
    sites = [("portal", "portal:8080", 443, "portal")] + [(a["id"], a["upstream"], a["port"], a["sub"]) for a in APPS]
    if split_ps_admin:
        # Password Station: the public port serves everything except /admin, which only the separately
        # published (Entra pre-authenticated) admin port serves.
        sites.append(("password-station-admin", "passwordstation:8200", PS_ADMIN_PORT, "passwords-admin"))
    for _id, upstream, port, sub in sites:
        block = ""
        if split_ps_admin and _id == "password-station":
            block = "\t@admin path /admin /admin/*\n\trespond @admin \"Not found\" 404\n"
        if domain:
            address = f"{sub}.{domain}"          # Let's Encrypt certificate, standard port 443
            tls = ""
        else:
            suffix = "" if port == 443 else f":{port}"
            address = f"https://{host}{suffix}"
            if internal_name:                      # Application Proxy needs a host name, not an IP
                address += f", https://{internal_name}{suffix}"
            tls = "\ttls internal\n"              # self-signed (Caddy's own CA) for an IP address
        lines.append(f"{address} {{\n{tls}{block}\tencode gzip\n\treverse_proxy {upstream}\n"
                     "\theader Strict-Transport-Security \"max-age=31536000\"\n"
                     "\theader X-Content-Type-Options nosniff\n"
                     "\theader Referrer-Policy strict-origin-when-cross-origin\n}\n")
    return "\n".join(lines)


def apps_catalog(urls: dict, organization: str, support_email: str) -> dict:
    cards = []
    for a in APPS:
        cards.append({
            "id": a["id"], "name": a["name"], "description": a["description"],
            "url": urls[a["id"]] + a.get("path", "/"),
            "health_url": f"http://{a['upstream']}{a['health']}",   # checked inside the Docker network
            "category": a["category"], "icon": a["icon"], "color": a["color"], "owner": a["owner"],
        })
    return {"portal": {"title": f"{organization} App Portal", "organization": organization,
                       "support_email": support_email}, "apps": cards}


def env_files(sec: dict, urls: dict, args) -> dict[str, dict]:
    """Per-app settings: each container only receives its own secrets."""
    return {
        "servicedesk": {
            "SD_SECRET_KEY": sec["SD_SECRET_KEY"],
            "SD_ADMIN_EMAIL": args.admin_email,
            "SD_ADMIN_PASSWORD": sec["SD_ADMIN_PASSWORD"],
            "SD_BASE_URL": urls["service-desk"],
            "SD_APP_NAME": "IT Service Desk",
        },
        "gmp": {
            "GMP_ADMIN_PASSWORD": sec["GMP_ADMIN_PASSWORD"],
            "GMP_BASE_URL": urls["gmp-assets"],
            "GMP_SITE_NAME": args.site_name,
        },
        "vault": {
            "SS_ADMIN_PASSWORD": sec["SS_ADMIN_PASSWORD"],
            "SS_MASTER_KEY": sec["SS_MASTER_KEY"],
            "SS_PUBLIC_URL": urls["password-vault"],
        },
        "phishguard": {
            "PG_ADMIN_PASSWORD": sec["PG_ADMIN_PASSWORD"],
            "PG_SECRET_KEY": sec["PG_SECRET_KEY"],
            "PG_BASE_URL": urls["phishguard"],
            "PG_ORG_DOMAINS": args.org_domains,
            # Console-only trial: no Microsoft 365 connection and no AI until keys are added.
            "PG_LLM_ENABLED": "false",
        },
        "passwordstation": {
            "PS_ADMIN_PASSWORD": sec["PS_ADMIN_PASSWORD"],
            "PS_SECRET_KEY": sec["PS_SECRET_KEY"],
            "PS_DATA_KEY": sec["PS_DATA_KEY"],
            "PS_BASE_URL": urls["password-station"],
            "PS_SUPPORT_CONTACT": args.support_email,  # overridden by PS_SUPPORT_CONTACT in the secret
            # Trial: built-in test domains, codes shown in Admin > Outbox. Real GMP / business AD
            # domains are added in the admin console once the VPN to the domain controllers exists.
            "PS_DEMO": "true" if args.ps_demo else "false",
            "PS_DEMO_OUTBOX": "true" if args.ps_demo else "false",
            **{k: sec[k] for k in PS_OPTIONAL if sec.get(k)},
            # SMS: keys of an IAM user allowed only sns:Publish (containers can't use the server's role).
            **({"AWS_ACCESS_KEY_ID": sec["PS_SNS_ACCESS_KEY_ID"], "AWS_SECRET_ACCESS_KEY": sec["PS_SNS_SECRET_ACCESS_KEY"],
                "AWS_REGION": sec.get("PS_SNS_REGION") or "us-east-1"}
               if sec.get("PS_SNS_ACCESS_KEY_ID") and sec.get("PS_SNS_SECRET_ACCESS_KEY") else {}),
        },
    }


def render_env(values: dict) -> str:
    for value in values.values():
        if "\n" in str(value):
            sys.exit("A configuration value contains a newline; refusing to write it")
    return "".join(f"{k}={v}\n" for k, v in values.items())


def write(path: Path, text: str, mode: int = 0o644) -> None:
    path.write_text(text)
    os.chmod(path, mode)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--host", help="IP address or host name for trial mode (one port per app)")
    where.add_argument("--domain", help="e.g. abelzeta.com: portal.<domain>, servicedesk.<domain>, ...")
    ap.add_argument("--internal-name", help="with --url-pattern: the server's internal host name "
                    "(e.g. abz-apps), also served, for Entra Application Proxy internal URLs")
    ap.add_argument("--url-pattern", help="with --host: external addresses, e.g. "
                    "'https://{sub}-abelzetaco.msappproxy.net' (Entra Application Proxy)")
    ap.add_argument("--secrets", required=True, type=Path, help="JSON file of secrets (completed in place)")
    ap.add_argument("--email", help="Contact email for Let's Encrypt (domain mode)")
    ap.add_argument("--admin-email", default="manager@abelzeta.com", help="First Service Desk IT manager login")
    ap.add_argument("--organization", default="AbelZeta")
    ap.add_argument("--support-email", default="servicedesk@abelzeta.com")
    ap.add_argument("--site-name", default="AbelZeta")
    ap.add_argument("--org-domains", default="abelzeta.com", help="PhishGuard: your own e-mail domains")
    ap.add_argument("--ps-demo", default=True, action=argparse.BooleanOptionalAction,
                    help="Password Station: built-in test domains, codes in Admin > Outbox (default on)")
    ap.add_argument("--data-dir", default="/opt/abelzeta/data")
    ap.add_argument("--out", type=Path, default=HERE)
    args = ap.parse_args(argv)

    sec, added, recovered = complete_secrets(args.secrets, args.out / "env")
    write(args.secrets, json.dumps(sec, indent=2) + "\n", 0o600)
    if args.url_pattern and (not args.host or "{sub}" not in args.url_pattern):
        sys.exit("--url-pattern needs --host and must contain {sub}")
    urls = public_urls(args.host, args.domain, args.url_pattern)
    write(args.out / ".env", render_env({"DATA_DIR": args.data_dir}))
    (args.out / "env").mkdir(exist_ok=True)
    for name, values in env_files(sec, urls, args).items():
        write(args.out / "env" / f"{name}.env", render_env(values), 0o600)
    write(args.out / "Caddyfile", caddyfile(args.host, args.domain, args.email, split_ps_admin=bool(args.url_pattern),
                                               internal_name=args.internal_name if args.url_pattern else None))
    write(args.out / "apps.json", json.dumps(apps_catalog(urls, args.organization, args.support_email), indent=2) + "\n")
    if args.url_pattern:
        urls["password-station-admin"] = args.url_pattern.format(sub="passwords-admin")
    print(json.dumps({"generated_secrets": added, "recovered_secrets": recovered, "urls": urls}))


if __name__ == "__main__":
    main()
