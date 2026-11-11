# Secret Server

An enterprise privileged-password vault modeled on Delinea Secret Server, with
single sign-on through **OpenID Connect** (Entra ID / Azure AD, Okta, Ping,
Keycloak, Google, ADFS 2019+) and **SAML 2.0** (ADFS, Okta, Ping, Shibboleth).

Built with FastAPI and SQLite, the same stack as the MFT application in `MFT_PRO/`.

## Features

| Area | What you get |
|---|---|
| **Vault** | Folders, secret templates (AD, Windows, Unix/SSH, Web, Database and your own), field-level encryption |
| **Encryption** | Envelope encryption: master key → per-secret AES-256-GCM data key → each field. Ciphertexts are bound to their secret/field, and the master key can be rotated online |
| **Permissions** | View / Edit / Owner ACLs for users and groups, inherited down the folder tree, with per-secret overrides. Admins are implicit Owners |
| **Check-out** | Exclusive, time-limited check-out, with optional password rotation on check-in |
| **Approval** | Access requests reviewed by an approver group, with a time-boxed grant; nobody can approve their own request |
| **Require comment** | A reason is required before a secret can be opened, and it is recorded in the audit log |
| **Rotation** | Password policies per template, manual and on-check-in rotation, pluggable *password changers* (built-in: vault-only, Active Directory over LDAPS), expiry tracking, field history |
| **Authentication** | Local accounts (scrypt hashes, lockout), TOTP MFA, revocable short-lived session tokens |
| **SSO** | OIDC auth-code + PKCE, SAML 2.0 SP-initiated login, JIT provisioning, IdP-group → local group sync, IdP-group → admin role, e-mail domain allow-list, optional SSO-only enforcement |
| **Audit** | Every login, view, edit, check-out, rotation and permission change goes into an HMAC hash-chained log that shows any tampering |

## Quick start

```bash
pip install -r secret_server/requirements.txt
python -m secret_server init --admin admin          # creates secret_server.key (0600) + first admin
python -m secret_server serve --host 0.0.0.0 --port 8443 \
    --ssl-certfile cert.pem --ssl-keyfile key.pem
# Interactive API docs: https://localhost:8443/docs
```

Environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `SS_DB_PATH` | `secret_server.db` | Vault database |
| `SS_MASTER_KEY_FILE` | `secret_server.key` | Master key ring (JSON) |
| `SS_MASTER_KEY` | – | A single base64 master key; use this to inject the key from a KMS or HSM-backed secret store |
| `SS_PUBLIC_URL` | `http://localhost:8443` | External URL, used to build SSO redirect and ACS URLs |
| `SS_TOKEN_TTL_MINUTES` | `30` | Session lifetime |
| `SS_SSO_SUCCESS_REDIRECT` | – | Front-end URL that receives a one-time `?code=` after SSO (exchanged at `POST /api/auth/sso/exchange`) |
| `SS_SSO_ENFORCED` | `false` | Block local password logins for everyone except administrators (break-glass) |
| `SS_MAX_FAILED_LOGINS` / `SS_LOCKOUT_MINUTES` | `5` / `15` | Account lockout |

Scheduled jobs (cron / Task Scheduler):

```bash
python -m secret_server maintenance        # release expired check-outs (rotating where set), list expired passwords
python -m secret_server rotate-master-key  # new master key + re-wrap all data keys
```

## Configuring SSO

### OIDC (example: Microsoft Entra ID)

1. In Entra, register an app with the redirect URI `https://<vault>/sso/entra/callback`,
   create a client secret, and add a `groups` claim to the ID token.
2. Register the provider:

```http
POST /api/sso/providers
{
  "name": "entra",
  "type": "oidc",
  "config": {
    "issuer": "https://login.microsoftonline.com/<tenant-id>/v2.0",
    "client_id": "<application-id>",
    "client_secret": "<secret>",
    "groups_claim": "groups",
    "group_mappings": {"<entra-group-object-id>": "Server Admins"},
    "admin_groups": ["<vault-admins-group-object-id>"],
    "allowed_email_domains": ["corp.example.com"]
  },
  "jit_provisioning": true
}
```

3. Users sign in at `https://<vault>/sso/entra/login`.

### SAML 2.0 (example: ADFS)

1. Register the provider. The response includes `acs_url` and `metadata_url`:

```http
POST /api/sso/providers
{
  "name": "adfs",
  "type": "saml",
  "config": {
    "idp_entity_id": "http://adfs.corp.example.com/adfs/services/trust",
    "idp_sso_url": "https://adfs.corp.example.com/adfs/ls/",
    "idp_x509_cert": "-----BEGIN CERTIFICATE-----\n...",
    "username_claim": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn",
    "email_claim": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "groups_claim": "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups",
    "group_mappings": {"CORP\\Vault-DBA": "DBA Team"},
    "admin_groups": ["CORP\\Vault-Admins"]
  }
}
```

2. In ADFS, add a relying party trust from `https://<vault>/sso/adfs/metadata`.
3. Users sign in at `https://<vault>/sso/adfs/login`.

`idp_x509_cert` also accepts a list of certificates, so you can roll over the IdP signing certificate without downtime.

### Provisioning rules

- An SSO identity is keyed by `(provider, subject)`. Role and mapped group membership
  are recomputed at **every** login, so removing a user from an IdP group takes effect
  the next time they sign in.
- JIT provisioning never links an SSO identity to an existing account from another
  source. With `jit_provisioning: false`, an admin must first create the user with
  `auth_source: "<provider>"`.
- SSO users have no local password and cannot use `/api/auth/login`.

## API overview

| Endpoint | Purpose |
|---|---|
| `POST /api/auth/login`, `/api/auth/mfa/verify` | Local login (+ TOTP) |
| `POST /api/auth/mfa/enroll`, `/confirm` | Enroll in TOTP |
| `GET /api/auth/providers` | Enabled SSO providers (for login buttons) |
| `GET /sso/{p}/login`, `/callback`, `POST /acs`, `GET /metadata` | SSO browser endpoints |
| `/api/users`, `/api/groups`, `/api/sso/providers` | Administration |
| `/api/folders`, `/api/folders/{id}/permissions` | Folder tree and ACLs |
| `/api/templates`, `GET /api/password/generate` | Templates and the password generator |
| `GET/POST /api/secrets`, `GET/PATCH/DELETE /api/secrets/{id}` | Secret metadata and editing |
| `POST /api/secrets/{id}/reveal` | Decrypt field values (audited; enforces comment, check-out and approval) |
| `POST /api/secrets/{id}/check-out`, `/check-in`, `/rotate` | Check-out and rotation |
| `GET /api/secrets/{id}/history`, `/audit`, `/permissions` | Owner views |
| `POST /api/secrets/{id}/access-requests`, `/api/access-requests/{id}/approve` | Approval workflow |
| `GET /api/audit`, `/api/audit/verify`, `POST /api/maintenance/run` | Audit and maintenance |

## Production notes

- **Protect the master key.** Keep it outside the database host's backups: use
  `SS_MASTER_KEY` from a KMS or HSM-backed secret store, or a key file on an
  encrypted volume. Back it up offline; without it the vault is unrecoverable.
- Always serve over TLS (directly, or behind a reverse proxy with `--behind-proxy`).
- SQLite serializes writes, which suits a single node. For HA, move to PostgreSQL;
  the data layer is plain SQL in `db.py` / `vault.py`.
- Not included: encrypted SAML assertions, SCIM provisioning, session recording,
  and a web UI. The REST API is UI-agnostic.

## Tests

```bash
cd secret_server && python -m pytest tests -q
```

The suite covers the crypto, permissions, check-out/rotation, approvals, audit-tamper
detection, and both SSO protocols against a mock OIDC IdP and real signed SAML
assertions. The SAML tests include forged, wrapped, replayed, mis-addressed and expired assertions.
