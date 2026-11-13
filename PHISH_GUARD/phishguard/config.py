"""Settings, overridable through environment variables (PG_*)."""

import os
import secrets
from dataclasses import dataclass, field
from typing import List


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_list(name: str) -> List[str]:
    return [v.strip().lower() for v in os.environ.get(name, "").split(",") if v.strip()]


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: _env("PG_DB_PATH", "phishguard.db"))
    base_url: str = field(default_factory=lambda: _env("PG_BASE_URL", "http://127.0.0.1:8100").rstrip("/"))
    # Signs rewritten links and report tokens. Set it in production so links survive restarts.
    secret_key: str = field(default_factory=lambda: _env("PG_SECRET_KEY") or secrets.token_urlsafe(32))
    admin_password: str = field(default_factory=lambda: _env("PG_ADMIN_PASSWORD"))
    session_idle_minutes: int = field(default_factory=lambda: _env_int("PG_SESSION_IDLE_MINUTES", 30))

    # Your own domains (lookalikes of these are high risk) and VIP names (display-name spoofing)
    org_domains: List[str] = field(default_factory=lambda: _env_list("PG_ORG_DOMAINS"))
    protected_domains: List[str] = field(default_factory=lambda: _env_list("PG_PROTECTED_DOMAINS"))
    vip_names: List[str] = field(default_factory=lambda: _env_list("PG_VIP_NAMES"))

    # Verdict thresholds (0-100 risk score)
    quarantine_score: int = field(default_factory=lambda: _env_int("PG_QUARANTINE_SCORE", 70))
    warn_score: int = field(default_factory=lambda: _env_int("PG_WARN_SCORE", 40))

    # AI analysis with Claude. Message content is sent to Anthropic when enabled.
    llm_enabled: bool = field(default_factory=lambda: _env_bool("PG_LLM_ENABLED", True))
    llm_model: str = field(default_factory=lambda: _env("PG_LLM_MODEL", "claude-opus-5-5"))
    llm_max_chars: int = field(default_factory=lambda: _env_int("PG_LLM_MAX_CHARS", 60000))

    # Live checks: DNS (SPF/DMARC) and time-of-click page fetching
    dns_checks: bool = field(default_factory=lambda: _env_bool("PG_DNS_CHECKS", True))
    click_fetch: bool = field(default_factory=lambda: _env_bool("PG_CLICK_FETCH", True))
    click_cache_minutes: int = field(default_factory=lambda: _env_int("PG_CLICK_CACHE_MINUTES", 10))

    # Microsoft 365 (Graph application permissions: Mail.ReadWrite, User.Read.All)
    m365_tenant_id: str = field(default_factory=lambda: _env("PG_M365_TENANT_ID"))
    m365_client_id: str = field(default_factory=lambda: _env("PG_M365_CLIENT_ID"))
    m365_client_secret: str = field(default_factory=lambda: _env("PG_M365_CLIENT_SECRET"))
    m365_mailboxes: List[str] = field(default_factory=lambda: _env_list("PG_M365_MAILBOXES"))  # empty = all users
    m365_poll_seconds: int = field(default_factory=lambda: _env_int("PG_M365_POLL_SECONDS", 60))
    quarantine_folder: str = field(default_factory=lambda: _env("PG_QUARANTINE_FOLDER", "PhishGuard Quarantine"))
    # Mailbox users forward suspicious mail to (any mail client); PhishGuard files it as a report
    report_mailbox: str = field(default_factory=lambda: _env("PG_REPORT_MAILBOX").lower())

    # Attachment detonation via CAPE Sandbox (https://github.com/kevoreilly/CAPEv2)
    cape_url: str = field(default_factory=lambda: _env("PG_CAPE_URL").rstrip("/"))
    cape_token: str = field(default_factory=lambda: _env("PG_CAPE_TOKEN"))

    # SMTP gateway mode
    smtp_listen_host: str = field(default_factory=lambda: _env("PG_SMTP_LISTEN_HOST", "0.0.0.0"))
    smtp_listen_port: int = field(default_factory=lambda: _env_int("PG_SMTP_LISTEN_PORT", 2525))
    smtp_next_hop: str = field(default_factory=lambda: _env("PG_SMTP_NEXT_HOP"))       # host:port to deliver to
    alert_from: str = field(default_factory=lambda: _env("PG_ALERT_FROM", "phishguard@localhost"))
    security_team_email: str = field(default_factory=lambda: _env("PG_SECURITY_EMAIL"))

    @property
    def m365_configured(self) -> bool:
        return bool(self.m365_tenant_id and self.m365_client_id and self.m365_client_secret)

    def all_protected(self) -> List[str]:
        return sorted(set(self.org_domains) | set(self.protected_domains) | set(DEFAULT_BRANDS))


# Frequently impersonated brands; lookalikes of these raise the score even if they are not yours
DEFAULT_BRANDS = (
    "microsoft.com", "office.com", "office365.com", "outlook.com", "live.com", "sharepoint.com", "onedrive.com",
    "google.com", "gmail.com", "apple.com", "icloud.com", "amazon.com", "paypal.com", "docusign.com", "docusign.net",
    "dropbox.com", "adobe.com", "linkedin.com", "facebook.com", "netflix.com", "dhl.com", "fedex.com", "ups.com",
    "wellsfargo.com", "chase.com", "bankofamerica.com", "citi.com", "hsbc.com", "barclays.co.uk", "stripe.com",
    "zoom.us", "salesforce.com", "intuit.com", "quickbooks.com", "coinbase.com", "binance.com", "irs.gov",
)
