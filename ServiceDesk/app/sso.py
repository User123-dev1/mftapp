"""Single sign-on via OpenID Connect.

Supports Microsoft Entra ID out of the box plus one generic OIDC provider
(Okta, Google Workspace, Ping, Keycloak, ADFS, ...). Providers are configured
with environment variables - see README.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from authlib.integrations.starlette_client import OAuth
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Role, User
from .security import NO_PASSWORD

logger = logging.getLogger(__name__)

# Entra tenant aliases that accept accounts from any tenant - not allowed, because
# then anyone with a Microsoft account could sign in.
_MULTI_TENANT = {"common", "organizations", "consumers"}


class SSOError(Exception):
    """Sign-in was rejected; the message is safe to show to the user."""


@dataclass(frozen=True)
class Provider:
    key: str
    display_name: str
    metadata_url: str
    client_id: str
    client_secret: str
    scope: str
    # Whether the IdP's email claim can be trusted to link to an existing account
    # without an email_verified claim (true for a single Entra tenant, whose admins
    # control the addresses).
    trust_email: bool


def configured_providers() -> list[Provider]:
    providers = []
    if settings.entra_tenant_id and settings.entra_client_id:
        tenant = settings.entra_tenant_id.strip()
        if tenant.lower() in _MULTI_TENANT:
            logger.error("ENTRA_TENANT_ID must be your tenant ID or domain, not %r; Entra SSO disabled.", tenant)
        else:
            providers.append(
                Provider(
                    key="entra",
                    display_name=settings.entra_display_name,
                    metadata_url=f"{settings.entra_authority.rstrip('/')}/{tenant}/v2.0/.well-known/openid-configuration",
                    client_id=settings.entra_client_id,
                    client_secret=settings.entra_client_secret,
                    scope="openid email profile",
                    trust_email=True,
                )
            )
    if settings.oidc_discovery_url and settings.oidc_client_id:
        providers.append(
            Provider(
                key="oidc",
                display_name=settings.oidc_display_name,
                metadata_url=settings.oidc_discovery_url,
                client_id=settings.oidc_client_id,
                client_secret=settings.oidc_client_secret,
                scope=settings.oidc_scopes,
                trust_email=False,
            )
        )
    return providers


_oauth: Optional[OAuth] = None
_providers: dict[str, Provider] = {}


def reset() -> None:
    """Rebuild the provider registry from settings (called at startup and by tests)."""
    global _oauth, _providers
    _oauth = OAuth()
    _providers = {}
    for p in configured_providers():
        _oauth.register(
            name=p.key,
            client_id=p.client_id,
            client_secret=p.client_secret,
            server_metadata_url=p.metadata_url,
            client_kwargs={"scope": p.scope},
        )
        _providers[p.key] = p


def providers() -> list[Provider]:
    if _oauth is None:
        reset()
    return list(_providers.values())


def get(key: str):
    """Return (provider, oauth client) or (None, None) if not configured."""
    if _oauth is None:
        reset()
    provider = _providers.get(key)
    return (provider, _oauth.create_client(key)) if provider else (None, None)


# --- Mapping identity-provider claims to a local user -------------------------

def _subject(provider: Provider, claims: dict) -> str:
    # For Entra, "oid" is the user's immutable object ID across all apps in the tenant.
    if provider.key == "entra" and claims.get("oid"):
        return str(claims["oid"])
    return str(claims["sub"])


def _email(provider: Provider, claims: dict) -> str:
    email = claims.get("email") or (claims.get("preferred_username") if provider.key == "entra" else None) or ""
    return email.strip().lower()


def _full_name(claims: dict, email: str) -> str:
    name = claims.get("name") or " ".join(p for p in (claims.get("given_name"), claims.get("family_name")) if p)
    return (name or email.split("@")[0]).strip()


def _mapped_role(claims: dict) -> Optional[Role]:
    """Role from the IdP's groups/app roles, or None if role sync is off or not possible."""
    if not (settings.sso_manager_groups or settings.sso_agent_groups):
        return None
    if "groups" in (claims.get("_claim_names") or {}):
        # Entra "group overage": the user is in too many groups for the token to list them.
        logger.warning("Groups claim overage for %s; role left unchanged. Use app roles instead.", claims.get("sub"))
        return None
    memberships = {str(g) for g in (claims.get("groups") or [])} | {str(r) for r in (claims.get("roles") or [])}
    if memberships & set(settings.sso_manager_groups):
        return Role.MANAGER
    if memberships & set(settings.sso_agent_groups):
        return Role.AGENT
    return Role.REQUESTER


def resolve_user(db: Session, provider: Provider, claims: dict) -> User:
    """Find, link or create the local user for a verified ID token. Caller commits."""
    subject = _subject(provider, claims)
    email = _email(provider, claims)
    if not email or "@" not in email:
        raise SSOError("Your identity provider did not send an email address.")
    if settings.sso_allowed_domains and email.rsplit("@", 1)[1] not in settings.sso_allowed_domains:
        raise SSOError("Your organization is not allowed to sign in to this service desk.")

    user = db.scalar(select(User).where(User.sso_provider == provider.key, User.sso_subject == subject))

    if user is None:
        existing = db.scalar(select(User).where(User.email == email))
        if existing is not None:
            if existing.sso_subject and existing.sso_provider == provider.key:
                # Same email, different identity at the same IdP (e.g. account recreated).
                raise SSOError("This email is linked to a different sign-in identity. Contact IT.")
            if not (provider.trust_email or claims.get("email_verified") is True):
                raise SSOError("An account with this email already exists. Sign in with your password.")
            existing.sso_provider, existing.sso_subject = provider.key, subject
            user = existing
            logger.info("Linked existing account %s to %s SSO", email, provider.key)
        elif settings.sso_auto_provision:
            user = User(
                email=email,
                full_name=_full_name(claims, email),
                password_hash=NO_PASSWORD,
                role=Role.REQUESTER,
                is_active=True,
                sso_provider=provider.key,
                sso_subject=subject,
            )
            db.add(user)
            logger.info("Provisioned %s from %s SSO", email, provider.key)
        else:
            raise SSOError("You do not have an account on this service desk. Contact IT.")

    if not user.is_active:
        raise SSOError("Your account is disabled. Contact IT.")

    role = _mapped_role(claims)
    if role is not None and role != user.role:
        logger.info("Role for %s synced from IdP: %s -> %s", user.email, user.role.value, role.value)
        user.role = role
    return user
