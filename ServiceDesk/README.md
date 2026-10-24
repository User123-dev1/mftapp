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
  models.py      User, Ticket, Comment, Activity, EmailLog
  emailer.py     email queue + SMTP delivery
  security.py    password hashing, sessions, CSRF, role checks
  config.py      environment settings
  templates/     Jinja2 HTML pages
  static/        CSS
tests/           end-to-end workflow tests
```
