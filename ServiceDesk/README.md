# IT Service Desk

A lightweight ServiceNow-style ticketing app (Python / FastAPI / SQLite).

## Workflow

```
Requester submits ticket ──► status NEW ──► email to every IT Manager (+ receipt to requester)
                                   │
IT Manager assigns to an agent ────┘──► status ASSIGNED ──► email to agent and requester
                                   │
Agent works it: In Progress / On Hold, comments, internal work notes
                                   │
Agent resolves (resolution notes required) ──► status RESOLVED ──► email to requester
                                   │
Requester can reopen (back to agent) — or staff close it
```

**Request types:** General Inquiry, Incident, Software, Hardware, Firewall Change, Network/VPN,
Account/Access, Email, Other. Some types ask for extra fields (e.g. firewall source/destination/ports,
account system/access level). Incidents are numbered `INC…`, everything else `REQ…`.

**Roles**

| Role | Can do |
|------|--------|
| Requester | Register, submit tickets, see/comment on their own tickets, reopen when resolved, cancel |
| Service Desk Agent | See all tickets, work + resolve tickets assigned to them, internal work notes |
| IT Manager | Everything above, assign/reassign tickets, work any ticket, manage users, view email log |

## Run it

```bash
cd ServiceDesk
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py                                          # http://localhost:8000
```

On first start an IT Manager account is created: **manager@example.com / ChangeMe123!**
(override with `SD_ADMIN_EMAIL` / `SD_ADMIN_PASSWORD`). Sign in, change the password under your
name (top right), then go to **Users** to add service desk agents. Requesters register themselves
at `/register`.

### Quick test of the full flow
1. Sign in as the manager → **Users** → add an agent.
2. In a private/incognito window, register as a requester and submit a ticket.
3. Manager → **Dashboard** → assign the ticket to the agent.
4. Sign in as the agent → **My Work** → open the ticket → set status *Resolved* with notes.
5. Manager → **Email Log** shows every notification (manager alert, assignment, resolution…).

## CMDB (configuration items)

The CMDB lists the devices, applications and services people can need help with. Requesters
choose the affected item when they submit a ticket. The form offers:
- **My devices**: items assigned to them
- **Applications & services**: shared items anyone can choose
- **Shared printers**
- **"Not listed / I don't know"**, so a missing item never blocks a ticket

The list has a type-to-filter box. Requesters can't choose someone else's device or a retired item.

| Who | What they get |
|-----|---------------|
| Requester | *My devices* on the home page with a **Get help** button that pre-fills the form |
| Agent / Manager | **CMDB** menu: search and filter, add or edit items, export CSV, and a panel on each ticket with model, serial, owner, location, warranty (expired is shown in red) and the item's ticket history |
| IT Manager | **Import CSV**, plus **Groups** for support groups (Network, Desktop Support, Business Apps…) |

**Assignment suggestion:** each item can belong to a support group. When a ticket names an item, the
manager sees the group's least-busy member as the suggested assignee. It is preselected in the
assign dropdown and starred on the dashboard. Assignment still happens manually.

### Importing your inventory

**CMDB → Import CSV**. Download the template from that page. Columns:

```
name,type,status,asset_tag,serial_number,manufacturer,model,version,ip_address,location,vendor,
warranty_expires,assigned_to_email,support_group,notes
```

- Only `name` and `type` are required. Types: `laptop, desktop, mobile, monitor, peripheral, printer,
  server, network, application, service, other`.
- A row updates an existing item when its `asset_tag` matches, otherwise its `serial_number`.
  Applications and services with neither match on name. Re-importing is safe, and blank cells never
  erase data. **Export CSV → edit in Excel → import** works for bulk changes.
- `assigned_to_email` puts the device under that person's *My devices*. With SSO configured, unknown
  emails get an account that links when the person first signs in; otherwise they are reported.
- `support_group` is matched by name and created if missing. Add members under **Groups**.
- Bad rows are skipped and listed; the rest of the file still imports.

**Where to get the data without Intune:** an export from your current asset spreadsheet,
procurement or vendor portals (Dell/HP/Lenovo give serial and warranty lists), network scanners such
as Lansweeper, or a Microsoft Graph export of Entra devices (`Get-MgDevice` gives names, OS and
registered owners).

## Single sign-on (SSO)

Sign-in through **Microsoft Entra ID** (Azure AD) plus any other **OpenID Connect** provider:
Okta, Google Workspace, Ping, Keycloak, ADFS and so on. Both can be on at the same time, and each
gets a "Sign in with …" button on the login page.

- **Just-in-time accounts:** the first SSO sign-in creates a Requester account (turn this off with
  `SSO_AUTO_PROVISION=false`).
- **Role mapping:** Entra app roles or security groups can map to IT Manager or Service Desk Agent.
  The role is synced at every sign-in.
- **Pre-created staff:** in **Users**, a manager can add someone by email with a blank password.
  When that person signs in with Entra, the account links to them and keeps the role that was set.
- **Existing password accounts** link automatically on first SSO sign-in. Entra is always trusted
  for this; other providers only when the token says `email_verified: true`.
- **SSO-only mode:** `SD_LOCAL_LOGIN=false` hides password login and self-registration. IT
  managers keep a break-glass password login at `/login?local=1`.

### Microsoft Entra ID setup

1. **Entra admin center → Identity → Applications → App registrations → New registration**
   - Name: `IT Service Desk`
   - Supported account types: **Accounts in this organizational directory only (Single tenant)**
   - Redirect URI: platform **Web**, `https://<your-host>/auth/entra/callback`
     (`http://localhost:8000/auth/entra/callback` works for local testing)
2. From **Overview**, copy the *Application (client) ID* and the *Directory (tenant) ID*.
3. **Certificates & secrets → New client secret**, then copy the secret **Value**.
4. *(Recommended)* **Token configuration → Add optional claim → ID → `email`**. Without it, the
   user's UPN (`preferred_username`) is used as their email.
5. *(Optional, roles)* **App roles → Create app role** twice, for example:
   - value `ServiceDesk.Manager`
   - value `ServiceDesk.Agent`

   Allowed member types: Users/Groups. Then go to **Enterprise applications → IT Service Desk →
   Users and groups** and assign people or groups to the roles. Anyone not assigned a role becomes
   a Requester.
6. *(Optional)* **Enterprise applications → IT Service Desk → Properties → Assignment required =
   Yes** to allow only assigned users and groups to sign in.

```bash
export SD_BASE_URL=https://servicedesk.contoso.com
export ENTRA_TENANT_ID=<directory-tenant-id>
export ENTRA_CLIENT_ID=<application-client-id>
export ENTRA_CLIENT_SECRET=<secret-value>
# optional role mapping (app role values, or security-group object IDs)
export SSO_MANAGER_GROUPS=ServiceDesk.Manager
export SSO_AGENT_GROUPS=ServiceDesk.Agent
```

App roles are preferred over groups. If you use groups (**Token configuration → Add groups
claim**), put the group *object IDs* in the variables above. A user in more than 200 groups gets
no group list in the token (Entra "group overage"); their role is then left unchanged and a warning
is logged.

Only single-tenant setups are accepted. `common`, `organizations` and `consumers` are refused so
that accounts from other organizations can't sign in.

### Okta / Google / other OpenID Connect providers

Create a "Web" OIDC app at the provider with redirect URI `https://<your-host>/auth/oidc/callback`, then:

```bash
export OIDC_DISCOVERY_URL=https://yourco.okta.com/.well-known/openid-configuration
#   Google:   https://accounts.google.com/.well-known/openid-configuration
#   Keycloak: https://kc.example.com/realms/<realm>/.well-known/openid-configuration
#   ADFS:     https://adfs.example.com/adfs/.well-known/openid-configuration
export OIDC_CLIENT_ID=...
export OIDC_CLIENT_SECRET=...
export OIDC_DISPLAY_NAME=Okta          # button label
export OIDC_SCOPES="openid email profile groups"   # add "groups" if your IdP needs it for role mapping
```

To limit sign-in to your organization's domains with a provider like Google, also set
`SSO_ALLOWED_DOMAINS`.

### SSO settings

| Variable | Default | Purpose |
|----------|---------|---------|
| `ENTRA_TENANT_ID` / `ENTRA_CLIENT_ID` / `ENTRA_CLIENT_SECRET` | | Enables "Sign in with Microsoft" |
| `ENTRA_DISPLAY_NAME` | `Microsoft` | Button label |
| `ENTRA_AUTHORITY` | `https://login.microsoftonline.com` | e.g. `https://login.microsoftonline.us` for US Government |
| `OIDC_DISCOVERY_URL` / `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | | Enables a generic OIDC provider |
| `OIDC_DISPLAY_NAME` / `OIDC_SCOPES` | `Single Sign-On` / `openid email profile` | |
| `SSO_AUTO_PROVISION` | `true` | Create accounts on first sign-in |
| `SSO_ALLOWED_DOMAINS` | *(any)* | Comma-separated email domains allowed to sign in |
| `SSO_MANAGER_GROUPS` / `SSO_AGENT_GROUPS` | *(off)* | Comma-separated app role values or group IDs. When set, the role is synced from the IdP at every sign-in (anyone in neither list becomes a Requester) |
| `SD_LOCAL_LOGIN` | `true` | `false` = SSO only (plus manager break-glass at `/login?local=1`) |

**Important:** when role mapping is on, a manager who signs in with SSO is demoted unless they are
in the manager role or group. Set up the Entra roles *before* turning mapping on. The break-glass
password login always stays available for the initial manager account.

The SSO tests (`tests/test_sso.py`) run against a local fake OpenID provider that signs real RS256
tokens. The full redirect, code exchange and token validation path is exercised, including
wrong-tenant tokens and forged `state`.

## Configuration (environment variables)

| Variable | Default | Purpose |
|----------|---------|---------|
| `SD_BASE_URL` | `http://localhost:8000` | Used for links in emails |
| `SD_SECRET_KEY` | `change-me-in-production` | Signs session cookies — **set this** |
| `SD_DATABASE_URL` | `sqlite:///./servicedesk.db` | Any SQLAlchemy URL |
| `SD_APP_NAME` | `IT Service Desk` | Shown in header and emails |
| `SD_ADMIN_EMAIL` / `SD_ADMIN_PASSWORD` / `SD_ADMIN_NAME` | see above | First IT Manager |
| `SMTP_HOST` | *(empty)* | SMTP server. Empty = emails are only recorded in the Email Log |
| `SMTP_PORT` | `587` | |
| `SMTP_USER` / `SMTP_PASSWORD` | | SMTP login (optional) |
| `SMTP_FROM` | `servicedesk@example.com` | From address |
| `SMTP_STARTTLS` | `true` | Use STARTTLS on port 587 |
| `SMTP_SSL` | `false` | Use implicit TLS (port 465) |

Example for Office 365:
```bash
export SMTP_HOST=smtp.office365.com SMTP_PORT=587 SMTP_USER=servicedesk@yourco.com \
       SMTP_PASSWORD='...' SMTP_FROM=servicedesk@yourco.com
```

Emails are sent in the background after each action; failures are recorded as `failed` with the
error in the Email Log, and never block the ticket update.

## Tests

```bash
pip install -r requirements.txt
python -m pytest -q
```

## Project layout

```
app/
  main.py        routes / pages
  services.py    ticket workflow rules and notification content
  models.py      User, Ticket, Comment, Activity, EmailLog, ConfigItem, SupportGroup
  emailer.py     email queue + SMTP delivery
  security.py    password hashing, sessions, CSRF, role checks
  cmdb.py        CMDB rules: item choices, assignee suggestion, CSV import/export
  cmdb_routes.py CMDB and support-group pages
  web.py         shared page rendering helpers
  sso.py         OpenID Connect sign-in (Entra ID, Okta, Google...) and claim-to-user mapping
  config.py      environment settings
  templates/     Jinja2 HTML pages
  static/        CSS
tests/           end-to-end workflow tests
```
