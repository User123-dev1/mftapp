# AbelZeta app portal on AWS

One address for every internal app. This folder deploys the **portal** and the apps behind it on
**one AWS server** (EC2), with HTTPS in front:

| App | Trial address (server IP) | Later, with the domain |
|-----|---------------------------|------------------------|
| Portal (start here) | `https://<ip>` | `https://portal.abelzeta.com` |
| IT Service Desk (tickets, changes, CMDB, AbelOrder) | `https://<ip>:8443` | `https://servicedesk.abelzeta.com` |
| GMP Asset Manager | `https://<ip>:8444` | `https://assets.abelzeta.com` |
| Password Vault (admin API) | `https://<ip>:8445/docs` | `https://vault.abelzeta.com/docs` |
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
4. Stack name `abelzeta-portal`. **AllowedCidr**: your IP followed by `/32`. Leave the rest. **Next**.
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

Change each password after signing in. Leave `SS_MASTER_KEY` and `SD_SECRET_KEY` alone.
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

**Approximate cost** (us-east-1, on-demand): t3.medium ≈ $30/month, 30 GB disk ≈ $2.40, public IP
≈ $3.60, snapshots a few dollars, Secrets Manager $0.40. About **$37–40/month**. Stopping the
instance stops the server charge but keeps the address and disk.

**Remove everything:** delete the `abelzeta-portal` stack. The secret and any snapshots are kept on
purpose (see above). Delete them by hand when you're sure.

## 3. Going live on abelzeta.com (when the trial is approved)

1. In DNS, point `portal`, `servicedesk`, `assets` and `vault`.abelzeta.com (A records) at the
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

## Files

| File | Purpose |
|------|---------|
| `aws/abelzeta-portal.yaml` | CloudFormation stack: server, Elastic IP, security group, secret, IAM role, snapshots |
| `install.sh` | Installs / updates everything on the server (re-runnable) |
| `configure.py` | Generates each app's settings, the HTTPS proxy config and the portal's app list |
| `docker-compose.yml` | The containers: proxy, portal, servicedesk, gmp, vault |

Each app only receives its own secrets. Generated files (`.env`, `env/`, `Caddyfile`, `apps.json`)
are never committed.

## Adding the GMP asset naming tool (or any app)

1. Put the app in this repository with a `Dockerfile` (copy `GMP_ASSET_MANAGER/Dockerfile`).
2. Add a service for it to `docker-compose.yml` with a data volume.
3. Add an entry to `APPS` in `configure.py` (name, port 8446, subdomain, upstream, health path).
4. Allow the new port in the security group, then run `install.sh`.

If it's hosted somewhere else, only step 3 is needed (the portal just links to it).

## Notes for GMP / GxP use

Hosting GMP Asset Manager on AWS makes AWS part of your computerised-system landscape. Before using
it for GMP records, have QA cover it in your validation: the infrastructure qualification (this
template is your documented, repeatable build), backup and restore tests, access control, and
supplier assessment of AWS under your Annex 11 / Part 11 procedures.
