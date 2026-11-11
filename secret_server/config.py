"""
Runtime configuration for Secret Server, read from environment variables.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # SQLite database file holding encrypted vault data
    db_path: str = "secret_server.db"
    # JSON key file: {"current": "k1", "keys": {"k1": "<base64 32 bytes>"}}
    master_key_file: Optional[str] = "secret_server.key"
    # Alternatively a single base64 master key (takes precedence over the file)
    master_key: Optional[str] = None
    # Externally reachable base URL, used to build SSO redirect / ACS URLs
    public_url: str = "http://localhost:8443"
    token_ttl_minutes: int = 30
    mfa_token_ttl_seconds: int = 300
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    # If set, SSO logins redirect here with a one-time ?code= to exchange for a token
    sso_success_redirect: Optional[str] = None
    # Disable local (username/password) logins for everyone except administrators
    sso_enforced: bool = False
    cors_origins: list = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=os.getenv("SS_DB_PATH", cls.db_path),
            master_key_file=os.getenv("SS_MASTER_KEY_FILE", cls.master_key_file),
            master_key=os.getenv("SS_MASTER_KEY"),
            public_url=os.getenv("SS_PUBLIC_URL", cls.public_url).rstrip("/"),
            token_ttl_minutes=int(os.getenv("SS_TOKEN_TTL_MINUTES", cls.token_ttl_minutes)),
            max_failed_logins=int(os.getenv("SS_MAX_FAILED_LOGINS", cls.max_failed_logins)),
            lockout_minutes=int(os.getenv("SS_LOCKOUT_MINUTES", cls.lockout_minutes)),
            sso_success_redirect=os.getenv("SS_SSO_SUCCESS_REDIRECT") or None,
            sso_enforced=_env_bool("SS_SSO_ENFORCED", False),
            cors_origins=[o for o in os.getenv("SS_CORS_ORIGINS", "").split(",") if o],
        )
