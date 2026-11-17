"""Settings from environment variables (PS_*)."""

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    app_name: str = field(default_factory=lambda: os.getenv("PS_APP_NAME", "Password Station"))
    base_url: str = field(default_factory=lambda: os.getenv("PS_BASE_URL", "http://localhost:8200").rstrip("/"))
    db_path: str = field(default_factory=lambda: os.getenv("PS_DB_PATH", "password_station.db"))
    # Signs sessions and hashes one-time codes. Must stay the same across restarts.
    secret_key: str = field(default_factory=lambda: os.getenv("PS_SECRET_KEY", ""))
    # base64 32-byte AES key encrypting recovery contacts and directory bind passwords.
    data_key: str = field(default_factory=lambda: os.getenv("PS_DATA_KEY", ""))
    admin_password: str = field(default_factory=lambda: os.getenv("PS_ADMIN_PASSWORD", ""))
    support_contact: str = field(default_factory=lambda: os.getenv("PS_SUPPORT_CONTACT", "the IT Service Desk"))

    # One-time codes
    code_ttl_minutes: int = field(default_factory=lambda: int(os.getenv("PS_CODE_TTL_MINUTES", "10")))
    code_max_attempts: int = field(default_factory=lambda: int(os.getenv("PS_CODE_MAX_ATTEMPTS", "5")))
    sends_per_account_hour: int = field(default_factory=lambda: int(os.getenv("PS_SENDS_PER_ACCOUNT_HOUR", "5")))
    requests_per_ip_hour: int = field(default_factory=lambda: int(os.getenv("PS_REQUESTS_PER_IP_HOUR", "30")))

    # E-mail (SMTP) for codes and notices
    smtp_host: str = field(default_factory=lambda: os.getenv("PS_SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.getenv("PS_SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: os.getenv("PS_SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: os.getenv("PS_SMTP_PASSWORD", ""))
    smtp_from: str = field(default_factory=lambda: os.getenv("PS_SMTP_FROM", "password-station@example.com"))
    smtp_starttls: bool = field(default_factory=lambda: _bool("PS_SMTP_STARTTLS", True))
    # SMS: "sns" (Amazon SNS, uses the server's AWS role) or "" (disabled)
    sms_provider: str = field(default_factory=lambda: os.getenv("PS_SMS_PROVIDER", ""))
    sms_sender_id: str = field(default_factory=lambda: os.getenv("PS_SMS_SENDER_ID", ""))
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "")))

    # Trial only: built-in demo domains, and messages shown in the admin "Outbox" instead of sent.
    demo: bool = field(default_factory=lambda: _bool("PS_DEMO", False))
    demo_outbox: bool = field(default_factory=lambda: _bool("PS_DEMO_OUTBOX", False))
