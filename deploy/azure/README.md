# AbelZeta app portal on Azure

Same apps and installer as the AWS deployment, reached from anywhere through **Microsoft Entra
Application Proxy** (sign-in with Entra + MFA before any app is reached). The servers accept no
connections from the internet.

```
 user (anywhere) ──HTTPS──▶ Entra Application Proxy (sign-in, MFA, Conditional Access)
                                   ▲  outbound only
 Azure: rg-abelzeta-portal         │
   vm-abz-connector (Windows) ─────┘  Entra private network connector, 10.40.1.5
        │ private network
   vm-abz-apps (Ubuntu, Docker)       portal + apps, 10.40.1.4   ── Key Vault (secrets), daily backup
```

## One-time setup (done)

1. `setup-github-identity.sh` in Azure Cloud Shell (Owner on the subscription): resource groups and the
   GitHub deployment identity (Contributor on `rg-abelzeta-portal` only, no stored secret).
2. GitHub repository variables: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
   `AZURE_LOCATION`. Optional: `AZURE_APPPROXY_TENANT` (default `abelzetaco`), `RDP_ALLOWED_CIDR`,
   `KEY_VAULT_READERS` (comma-separated Entra object IDs of people who may read the passwords in Key
   Vault; a full deployment resets the Key Vault access list to the VM, the deployer and these people).

## Deploy / update

Push a commit to the branch with **`[deploy azure]`** in its message. The *Azure deploy* workflow
(`.github/workflows/azure-deploy.yml`) then deploys `main.bicep` and runs `deploy/install.sh` on
`vm-abz-apps` through Run Command, printing `diagnose.py` at the end. Other pushes change nothing in Azure.

Secrets live in Key Vault `kv-abz-…` (secret `portal-secrets`, same keys as the AWS secret). The
connector VM's local admin (`abzadmin`) password is the Key Vault secret `connector-vm-admin-password`.

On the apps VM (Azure portal → `vm-abz-apps` → **Run command** → RunShellScript), the same commands as
on AWS work, e.g. `python3 /opt/abelzeta/mftapp/deploy/diagnose.py` or `reset_passwords.py`.

## Connector and publishing (once)

1. Sign in to `vm-abz-connector`: **Connect → Bastion** (Developer tier, free, if offered in the
   region), or set `RDP_ALLOWED_CIDR` to your IP/32, redeploy, and use remote desktop to its public IP.
2. On it, as a Global / Application Administrator: Entra admin center → **Global Secure Access →
   Connect → Connectors** (or *Enterprise apps → Application proxy*) → **Download connector service**,
   install, sign in. Then remove `RDP_ALLOWED_CIDR` and redeploy.
3. Publish each app: **Enterprise applications → New application → Add an on-premises application**
   (internal URLs must be host names: the server answers to `abz-apps`; on the **Advanced** tab untick
   **Validate Backend SSL certificate**, since the server uses its own internal certificate):

| Name | Internal URL | External URL prefix | Pre-authentication |
|------|--------------|---------------------|--------------------|
| Portal | `https://abz-apps/` | `portal` | Microsoft Entra ID |
| Service Desk | `https://abz-apps:8443/` | `servicedesk` | Microsoft Entra ID |
| GMP Asset Manager | `https://abz-apps:8444/` | `assets` | Microsoft Entra ID |
| Password Vault | `https://abz-apps:8445/` | `vault` | Microsoft Entra ID |
| PhishGuard | `https://abz-apps:8446/` | `phishguard` | Microsoft Entra ID |
| Password Station | `https://abz-apps:8447/` | `passwords` | **Passthrough** (users who forgot their password can't sign in) |
| Password Station admin | `https://abz-apps:8448/` | `passwordsadmin` (no hyphens: msappproxy.net prefixes allow only letters and digits) | Microsoft Entra ID |

   The public Password Station address refuses `/admin`; its admin console is only on the separately
   published, pre-authenticated address. Assign users or a group to each app (Users and groups).
4. Users need a Microsoft 365 Business Premium licence (Entra ID P1) assigned.

## Adding settings (e.g. PhishGuard's Microsoft 365 connection)

Add each setting as its **own Key Vault secret**, named after the setting with dashes instead of
underscores, then run an apps-only deployment (commit message with `[deploy azure] [apps only]`):

| Key Vault secret | Setting |
|---|---|
| `PG-M365-TENANT-ID`, `PG-M365-CLIENT-ID`, `PG-M365-CLIENT-SECRET` | PhishGuard Microsoft 365 (Graph) connection |
| `PG-M365-MAILBOXES` | Mailboxes to protect, comma-separated (pilot) |
| `PG-REPORT-MAILBOX`, `PG-SECURITY-EMAIL`, `PG-ALERT-FROM` | Reported-mail mailbox, analyst alert address, alert sender |
| `PS-SMTP-HOST`, … | Password Station e-mail / SMS (see deploy/README.md) |

They override the same keys in `portal-secrets` (and are copied into it at install). Adding a secret
needs **Set** in your Key Vault access policy.

## Removing it

Stop backups first (Recovery Services vault `rsv-abz` → Backup items → Stop backup, delete data), then
delete `rg-abelzeta-portal`. Key Vault stays recoverable for 30 days (soft delete).
