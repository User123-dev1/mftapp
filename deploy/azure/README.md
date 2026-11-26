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
   `AZURE_LOCATION`. Optional: `AZURE_APPPROXY_TENANT` (default `abelzetaco`), `RDP_ALLOWED_CIDR`.

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
3. Publish each app: **Enterprise applications → New application → Add an on-premises application**:

| Name | Internal URL | External URL prefix | Pre-authentication |
|------|--------------|---------------------|--------------------|
| Portal | `https://10.40.1.4/` | `portal` | Microsoft Entra ID |
| Service Desk | `https://10.40.1.4:8443/` | `servicedesk` | Microsoft Entra ID |
| GMP Asset Manager | `https://10.40.1.4:8444/` | `assets` | Microsoft Entra ID |
| Password Vault | `https://10.40.1.4:8445/` | `vault` | Microsoft Entra ID |
| PhishGuard | `https://10.40.1.4:8446/` | `phishguard` | Microsoft Entra ID |
| Password Station | `https://10.40.1.4:8447/` | `passwords` | **Passthrough** (users who forgot their password can't sign in) |
| Password Station admin | `https://10.40.1.4:8448/` | `passwords-admin` | Microsoft Entra ID |

   The public Password Station address refuses `/admin`; its admin console is only on the separately
   published, pre-authenticated address. Assign users or a group to each app (Users and groups).
4. Users need a Microsoft 365 Business Premium licence (Entra ID P1) assigned.

## Removing it

Stop backups first (Recovery Services vault `rsv-abz` → Backup items → Stop backup, delete data), then
delete `rg-abelzeta-portal`. Key Vault stays recoverable for 30 days (soft delete).
