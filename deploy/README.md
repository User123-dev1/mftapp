# AbelZeta app portal on AWS

One address for every internal app. This folder deploys the **portal** and the apps behind it on
**one AWS server** (EC2), with HTTPS in front:

| App | Trial address (server IP) | Later, with the domain |
|-----|---------------------------|------------------------|
| Portal (start here) | `https://<ip>` | `https://portal.abelzeta.com` |
| IT Service Desk (tickets, changes, CMDB, AbelOrder) | `https://<ip>:8443` | `https://servicedesk.abelzeta.com` |
| GMP Asset Manager | `https://<ip>:8444` | `https://assets.abelzeta.com` |
| Password Vault (web interface; API console at `/docs`) | `https://<ip>:8445` | `https://vault.abelzeta.com` |
| PhishGuard (e-mail security console) | `https://<ip>:8446` | `https://phishguard.abelzeta.com` |
| Password Station (change / reset / unlock) | `https://<ip>:8447` | `https://passwords.abelzeta.com` |
| GMP asset naming tool | *to be added* | |

```
 your IP only ──HTTPS──▶ EC2 (Amazon Linux 2023, Elastic IP, encrypted disk)
                          └─ Caddy reverse proxy (HTTPS)
                               ├─ portal        ├─ servicedesk ─┐
                               ├─ gmp ──────────┤  vault ────────┤ SQLite databases on /opt/abelzeta/data
                               └────────────────┘                └─ daily disk snapshots (7 kept)
 Secrets Manager: first admin passwords + the vault master key (never stored in the repo)
```

## 1. Create it (trial, ~10 minutes)

1. Find your public IP (search "what is my ip").
2. AWS console → **CloudFormation** → **Create stack** → **With new resources**.
3. **Upload a template file** → choose `deploy/aws/abelzeta-portal.yaml` → **Next**.
4. Stack name `abelzeta-portal`. **AllowedCidr**: your IP followed by `/32`. Leave the rest (the default
   server, t3.small, works on AWS Free plan accounts). **Next**.
5. **Next** again, tick **I acknowledge that AWS CloudFormation might create IAM resources**, **Submit**.
6. Wait for `CREATE_COMPLETE`, then give the server **about 10 minutes** to build the apps.
7. **Outputs** tab → open **PortalUrl**. Your browser warns that the certificate isn't trusted: the trial
   uses the server's own certificate because there is no domain yet. Click *Advanced → Continue*.
   You'll see this once for each address (the portal and each app's port).

**First sign-ins:** Outputs → **InitialPasswordsSecret** shows a secret's ARN. Open **Secrets Manager**
(the **SecretsManagerConsole** link), open that secret and click **Retrieve secret value**:

| App | User | Password key |
|-----|------|--------------|
| Service Desk | `manager@abelzeta.com` | `SD_ADMIN_PASSWORD` |
| GMP Asset Manager | `admin` | `GMP_ADMIN_PASSWORD` (you must change it at first login) |
| Password Vault | `admin` | `SS_ADMIN_PASSWORD` |
| PhishGuard | `admin` | `PG_ADMIN_PASSWORD` (you must change it at first login) |
| Password Station (admin console: `/admin/login`) | `admin` | `PS_ADMIN_PASSWORD` |

Change each password after signing in. Leave `SS_MASTER_KEY`, `SD_SECRET_KEY`, `PG_SECRET_KEY`,
`PS_SECRET_KEY` and `PS_DATA_KEY` alone (`PS_DATA_KEY` decrypts users' registered recovery details).
**`SS_MASTER_KEY` decrypts the vault: if it's lost, the vault's contents are lost**, including from
backups. That's why the secret is kept even if you delete the stack.

## 2. Day to day

- **Shell on the server:** EC2 → Instances → `abelzeta-portal` → **Connect** → **Session Manager**.
  No SSH keys and no open SSH port are needed.
- **Update to the latest code** (pulls the branch, rebuilds and restarts; data and passwords are kept):
  ```bash
  sudo bash /opt/abelzeta/mftapp/deploy/install.sh
  ```
- **Status and logs:**
  ```bash
  cd /opt/abelzeta/mftapp/deploy
  sudo docker compose ps
  sudo docker compose logs -f servicedesk      # or gmp, vault, portal, proxy
  ```
- **First-install log:** `/var/log/abelzeta-install.log`
- **Your IP changed?** CloudFormation → the stack → **Update** → *Use existing template* → new `AllowedCidr`.
- **Backups:** the disk is snapshotted daily at 03:00 UTC and 7 snapshots are kept
  (EC2 → Snapshots). To restore, create a volume from a snapshot and swap it in, or ask for a
  restore runbook before you need it.

**Approximate cost** (us-east-1, on-demand, outside Free Tier credits): t3.small ≈ $15/month (t3.medium ≈ $30), 30 GB disk ≈ $2.40, public IP
≈ $3.60, snapshots a few dollars, Secrets Manager $0.40. About **$22–25/month** on t3.small. Stopping the
instance stops the server charge but keeps the address and disk.

**Remove everything:** delete the `abelzeta-portal` stack. The secret and any snapshots are kept on
purpose (see above). Delete them by hand when you're sure.

### Passwords from Secrets Manager don't work

Check which secret the server uses and whether its values match the running apps (no passwords are
printed, only short fingerprints):

```bash
sudo python3 /opt/abelzeta/mftapp/deploy/diagnose.py
```

- **NO in either column:** the apps' passwords never reached Secrets Manager (for example, the
  first start couldn't read it yet). Run `sudo bash /opt/abelzeta/mftapp/deploy/install.sh`. It keeps
  the passwords the apps already use, including the vault key, and saves them to the secret shown.
- **Sign-in OK on the server but not in your browser:** you're reading a different secret. After
  re-creating the stack there are several `PortalSecret-...` secrets; use the one in the stack's
  **Outputs → InitialPasswordsSecret**.

### Connecting from a different location (new public IP)

Only the addresses in **AllowedCidr** (plus the optional **AllowedCidr2** / **AllowedCidr3**, e.g.
office, home, spare) can reach the portal. Changing them only edits the firewall rule; the server
and its data are untouched. Either:

- **Console:** CloudFormation → the stack → **Update** → **Use existing template** → **Next** →
  change the address(es) → **Next** → **Next** → tick the IAM box → **Submit**.
- **AWS CloudShell** (the `>_` icon in the console), replacing the address with yours:
  ```bash
  aws cloudformation update-stack --stack-name abelzeta-portal --use-previous-template \
    --capabilities CAPABILITY_IAM --parameters ParameterKey=AllowedCidr,ParameterValue=203.0.113.25/32 \
    ParameterKey=AllowedCidr2,UsePreviousValue=true ParameterKey=AllowedCidr3,UsePreviousValue=true \
    ParameterKey=InstanceType,UsePreviousValue=true ParameterKey=VolumeSize,UsePreviousValue=true \
    ParameterKey=Branch,UsePreviousValue=true ParameterKey=LatestAmiId,UsePreviousValue=true
  ```

Find your current public IP by searching "what is my ip" from the computer you'll use (not from
CloudShell, which has its own address). It takes about a minute to apply.

### Locked out of an app, or the admin password doesn't work

Set your own admin password for every app at once (also unlocks the accounts):

```bash
sudo python3 /opt/abelzeta/mftapp/deploy/reset_passwords.py                       # all five apps
sudo python3 /opt/abelzeta/mftapp/deploy/reset_passwords.py --apps servicedesk gmp   # just some
```

It asks for the password twice without showing it, then saves it in Secrets Manager too, so the
secret always holds the current admin passwords. Passwords set this way (or changed inside an app)
are kept: restarts, reboots and `install.sh` never reset an existing admin.


PhishGuard locks an account after 5 wrong passwords, and only another administrator can unlock
it. To unlock `admin` and set its password back to `PG_ADMIN_PASSWORD` from Secrets Manager:

```bash
cd /opt/abelzeta/mftapp/deploy
sudo docker compose exec phishguard python reset_admin.py
```

Service Desk: see why a sign-in failed, list the accounts, or set the IT manager back to
`SD_ADMIN_PASSWORD`:

```bash
sudo docker compose logs servicedesk | grep "Sign-in failed"
sudo docker compose exec servicedesk python reset_admin.py --list
sudo docker compose exec servicedesk python reset_admin.py
```

Password Station locks `admin` for 15 minutes after 5 wrong passwords. To unlock it at once:
`sudo docker compose exec passwordstation python reset_admin.py`.

When copying a password from Secrets Manager, use the **Plaintext** view and copy only what's
between the quotes, so no stray spaces or quote marks come along.

## 3. Going live on abelzeta.com (when the trial is approved)

1. In DNS, point `portal`, `servicedesk`, `assets`, `vault`, `phishguard` and `passwords`.abelzeta.com (A records) at the
   Elastic IP from the stack outputs.
2. Allow ports **80 and 443** from the internet in the security group. Let's Encrypt must reach port
   80 to issue the certificates.
3. On the server:
   ```bash
   sudo DOMAIN=abelzeta.com LE_EMAIL=it@abelzeta.com bash /opt/abelzeta/mftapp/deploy/install.sh
   ```
   This switches every app to its subdomain with a trusted certificate. Data and passwords are kept.

**Before opening it to the internet,** turn on Microsoft Entra sign-in for each app: Service Desk
(see `ServiceDesk/README.md`, "Single sign-on") and the vault (`secret_server/README.md`). Also
consider keeping the vault reachable only from the office or VPN. The trial's IP restriction is
the only thing protecting it today.

## PhishGuard in this deployment

PhishGuard runs **console-only**: use **Test a message** to scan saved `.eml` files. It is not
connected to any mailboxes, and AI analysis is off. To go further (in a pilot first):
- **Microsoft 365:** add `PG_M365_TENANT_ID`, `PG_M365_CLIENT_ID` and `PG_M365_CLIENT_SECRET` (see
  `PHISH_GUARD/README.md`). This lets it move and delete mail, so get approval from your Microsoft 365
  owner and scope it to the pilot mailboxes.
- **AI analysis:** set `PG_LLM_ENABLED=true` and add `ANTHROPIC_API_KEY` (a paid Anthropic account).

For now, these settings go in `deploy/env/phishguard.env` on the server. They're overwritten when
`install.sh` runs, so ask for them to be added to `configure.py` before relying on them.

## Password Station in this deployment

The trial runs in **demo mode**: two built-in test domains ("GMP domain (demo)" and "Business - Entra
ID / Microsoft 365 (demo)") with test users `jdoe` and `asmith`, password `Welcome-Demo-2026`
(`da-admin` shows that privileged accounts are refused). Codes aren't sent; read them in
**Admin → Outbox**. Try: Set up recovery details → Forgot password → Unlock.

To connect the real GMP and business Active Directory domains (see `PASSWORD_STATION/README.md`):
1. A site-to-site VPN from the VPC to the domain controllers (LDAPS, TCP 636).
2. A service account in each domain with *Reset password* and *Write lockoutTime* on the user OUs only.
3. Admin console → **Add domain** for each, then **Save & test connection**.
4. Set up e-mail (SMTP) and/or SMS (Amazon SNS) for the codes, then turn demo mode off (remembered
   for later runs): `sudo PS_DEMO=false bash /opt/abelzeta/mftapp/deploy/install.sh`

**Codes by e-mail / SMS:** add these keys to the Secrets Manager secret, then re-run `install.sh`:
- E-mail: `PS_SMTP_HOST`, `PS_SMTP_PORT` (587), `PS_SMTP_USER`, `PS_SMTP_PASSWORD`, `PS_SMTP_FROM`
  (for Microsoft 365: `smtp.office365.com` and a licensed or SMTP-AUTH-enabled sender mailbox).
- SMS (Amazon SNS): `PS_SMS_PROVIDER=sns`, `PS_SNS_REGION`, and `PS_SNS_ACCESS_KEY_ID` /
  `PS_SNS_SECRET_ACCESS_KEY` of an IAM user whose only permission is `sns:Publish`. The containers
  deliberately can't use the server's own role, because that role can read every app's secrets.
  Move the SNS account out of the SMS sandbox first, or it can only text verified numbers.

Business AD is synced to Entra ID by Entra Connect, so a password changed or reset here reaches
Microsoft 365 through the normal sync (password hash sync: about 2 minutes).

## Files

| File | Purpose |
|------|---------|
| `aws/abelzeta-portal.yaml` | CloudFormation stack: server, Elastic IP, security group, secret, IAM role, snapshots |
| `install.sh` | Installs / updates everything on the server (re-runnable) |
| `configure.py` | Generates each app's settings, the HTTPS proxy config and the portal's app list |
| `docker-compose.yml` | The containers: proxy, portal, servicedesk, gmp, vault, phishguard, passwordstation |

Each app only receives its own secrets. Generated files (`.env`, `env/`, `Caddyfile`, `apps.json`)
are never committed.

## Adding the GMP asset naming tool (or any app)

1. Put the app in this repository with a `Dockerfile` (copy `GMP_ASSET_MANAGER/Dockerfile`).
2. Add a service for it to `docker-compose.yml` with a data volume.
3. Add an entry to `APPS` in `configure.py` (name, next free port e.g. 8448, subdomain, upstream, health path).
4. Allow the new port in the security group, then run `install.sh`.

If it's hosted somewhere else, only step 3 is needed (the portal just links to it).

## Notes for GMP / GxP use

Hosting GMP Asset Manager on AWS makes AWS part of your computerised-system landscape. Before using
it for GMP records, have QA cover it in your validation: the infrastructure qualification (this
template is your documented, repeatable build), backup and restore tests, access control, and
supplier assessment of AWS under your Annex 11 / Part 11 procedures.
