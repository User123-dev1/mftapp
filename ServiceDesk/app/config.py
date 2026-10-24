"""Application settings, read from environment variables."""

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    app_name: str = field(default_factory=lambda: os.getenv("SD_APP_NAME", "IT Service Desk"))
    base_url: str = field(default_factory=lambda: os.getenv("SD_BASE_URL", "http://localhost:8000"))
    database_url: str = field(default_factory=lambda: os.getenv("SD_DATABASE_URL", "sqlite:///./servicedesk.db"))
    secret_key: str = field(default_factory=lambda: os.getenv("SD_SECRET_KEY", "change-me-in-production"))

    # First IT manager account, created on startup if no manager exists.
    admin_email: str = field(default_factory=lambda: os.getenv("SD_ADMIN_EMAIL", "manager@example.com"))
    admin_password: str = field(default_factory=lambda: os.getenv("SD_ADMIN_PASSWORD", "ChangeMe123!"))
    admin_name: str = field(default_factory=lambda: os.getenv("SD_ADMIN_NAME", "IT Manager"))

    # SMTP. When SMTP_HOST is empty, emails are only recorded in the email log.
    smtp_host: str = field(default_factory=lambda: os.getenv("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.getenv("SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: os.getenv("SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: os.getenv("SMTP_PASSWORD", ""))
    smtp_from: str = field(default_factory=lambda: os.getenv("SMTP_FROM", "servicedesk@example.com"))
    smtp_starttls: bool = field(default_factory=lambda: _bool("SMTP_STARTTLS", True))
    smtp_ssl: bool = field(default_factory=lambda: _bool("SMTP_SSL", False))


settings = Settings()
