"""Application settings, read from environment variables."""

import os
from dataclasses import dataclass, field


def _list(name: str) -> list[str]:
    return [v.strip() for v in os.getenv(name, "").split(",") if v.strip()]


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    app_name: str = field(default_factory=lambda: os.getenv("SD_APP_NAME", "IT Service Desk"))
    base_url: str = field(
        default_factory=lambda: os.getenv("SD_BASE_URL", f"http://localhost:{os.getenv('SD_PORT', '8085')}")
    )
    database_url: str = field(default_factory=lambda: os.getenv("SD_DATABASE_URL", "sqlite:///./servicedesk.db"))
    # Where uploaded files (supplier quotes) and generated PDFs are stored.
    upload_dir: str = field(default_factory=lambda: os.getenv("SD_UPLOAD_DIR", "./uploads"))
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

    # Password login and self-registration. When False, only SSO is offered,
    # except a break-glass password login for IT managers at /login?local=1.
    local_login: bool = field(default_factory=lambda: _bool("SD_LOCAL_LOGIN", True))

    # --- Single sign-on (OpenID Connect) ---
    # Microsoft Entra ID (Azure AD). Use your tenant ID (GUID) or primary domain.
    entra_tenant_id: str = field(default_factory=lambda: os.getenv("ENTRA_TENANT_ID", ""))
    entra_client_id: str = field(default_factory=lambda: os.getenv("ENTRA_CLIENT_ID", ""))
    entra_client_secret: str = field(default_factory=lambda: os.getenv("ENTRA_CLIENT_SECRET", ""))
    entra_display_name: str = field(default_factory=lambda: os.getenv("ENTRA_DISPLAY_NAME", "Microsoft"))
    # Change for sovereign clouds, e.g. https://login.microsoftonline.us (US Government).
    entra_authority: str = field(
        default_factory=lambda: os.getenv("ENTRA_AUTHORITY", "https://login.microsoftonline.com")
    )

    # Any other OpenID Connect provider (Okta, Google Workspace, Ping, Keycloak, ADFS...).
    oidc_discovery_url: str = field(default_factory=lambda: os.getenv("OIDC_DISCOVERY_URL", ""))
    oidc_client_id: str = field(default_factory=lambda: os.getenv("OIDC_CLIENT_ID", ""))
    oidc_client_secret: str = field(default_factory=lambda: os.getenv("OIDC_CLIENT_SECRET", ""))
    oidc_display_name: str = field(default_factory=lambda: os.getenv("OIDC_DISPLAY_NAME", "Single Sign-On"))
    oidc_scopes: str = field(default_factory=lambda: os.getenv("OIDC_SCOPES", "openid email profile"))

    # Create a requester account the first time someone signs in with SSO.
    sso_auto_provision: bool = field(default_factory=lambda: _bool("SSO_AUTO_PROVISION", True))
    # Only allow SSO sign-in for these email domains (empty = any).
    sso_allowed_domains: list[str] = field(default_factory=lambda: [d.lower() for d in _list("SSO_ALLOWED_DOMAINS")])
    # Group object IDs or app role values from the token's "groups"/"roles" claims.
    # When either list is set, the user's role is synced from the IdP at every sign-in.
    sso_manager_groups: list[str] = field(default_factory=lambda: _list("SSO_MANAGER_GROUPS"))
    sso_agent_groups: list[str] = field(default_factory=lambda: _list("SSO_AGENT_GROUPS"))


    # --- Entra ID device sync (Microsoft Graph, app-only) ---
    # Uses the ENTRA_* app registration above. Needs application permissions
    # Device.Read.All and User.Read.All with admin consent.
    entra_sync_enabled: bool = field(default_factory=lambda: _bool("ENTRA_SYNC_ENABLED", False))
    entra_sync_hour: int = field(default_factory=lambda: int(os.getenv("ENTRA_SYNC_HOUR", "2")))  # UTC
    # Include personal "registered" (BYOD) devices, not just Entra-joined / hybrid-joined ones.
    entra_sync_include_registered: bool = field(
        default_factory=lambda: _bool("ENTRA_SYNC_INCLUDE_REGISTERED", False)
    )
    # Mark previously synced devices as Retired when they disappear from Entra.
    entra_sync_retire_missing: bool = field(default_factory=lambda: _bool("ENTRA_SYNC_RETIRE_MISSING", False))
    # Entra can't tell laptops from desktops; new Windows/macOS/Linux devices get this type.
    entra_sync_default_type: str = field(default_factory=lambda: os.getenv("ENTRA_SYNC_DEFAULT_TYPE", "laptop"))
    # e.g. https://graph.microsoft.us for US Government.
    graph_base_url: str = field(default_factory=lambda: os.getenv("GRAPH_BASE_URL", "https://graph.microsoft.com"))


settings = Settings()
