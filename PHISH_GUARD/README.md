# PhishGuard

AI-assisted phishing, business e-mail compromise (BEC) and malware protection for e-mail.
It works with **Microsoft 365** directly, or with **any mail system** as an SMTP gateway.

What happens to each message:
- Every message is scored 0–100 on who really sent it, how it is written, where its links lead and what its attachments do.
- **High-risk mail is quarantined.**
- **Suspicious mail is delivered with a warning.**
- Users can report anything with one click.
- **Confirmed threats are pulled out of every mailbox in the organisation.**

Python 3.10+, FastAPI, SQLite.

```bash
cd PHISH_GUARD
pip install -r requirements.txt
export PG_ORG_DOMAINS=abelzeta.com            # your own domains
export PG_VIP_NAMES="john mensah,ama owusu"   # executives whose names attackers borrow
export ANTHROPIC_API_KEY=...                  # AI analysis (optional, see below)
python run.py                                 # console on http://127.0.0.1:8100
```

The first start prints a temporary password for the console user `admin`. You can also set `PG_ADMIN_PASSWORD` before the first start.

Tests: `python -m pytest tests`. There are 44 tests. Microsoft 365, Claude and the sandbox are simulated, so no accounts or costs are needed.

## How a message is judged

| Check | What it catches |
|---|---|
| **Sender authentication** | SPF, DKIM and DMARC results from Exchange Online's `Authentication-Results` header, including Microsoft's composite `compauth`. In gateway mode, PhishGuard evaluates SPF and DMARC itself from DNS, and verifies DKIM signatures when `dkimpy` is installed. Mail that claims to come from your own domain but fails authentication scores very high. |
| **Lookalike domains** | `rn`→`m`, `vv`→`w`, `0`→`o`, `1`→`l`, Cyrillic and Greek letters (punycode/IDN), typos (`abelzetta.com`), the right name on a different ending (`paypal.co`), and brands buried in a longer name (`micros0ft-support.net`, `paypal.com.verify-acct.xyz`). These are checked against your domains plus about 40 commonly impersonated brands. |
| **Identity tricks** | Executive names used by outside senders, display names that impersonate brands, an e-mail address hidden inside the display name, and Reply-To addresses that differ from the sender (especially free webmail). |
| **Language heuristics** | Urgency, secrecy, "are you at your desk?", bank-detail changes, wire transfers, gift cards, credential requests, threats, rewards, crypto and callback scams. The combinations behind BEC and invoice fraud score extra. |
| **AI analysis (Claude)** | Reads the message like a security analyst would: the pretext, what is being asked, and whether it fits the sender. This catches polite, typo-free fraud that has no trigger words. It returns a structured verdict, a plain-English explanation for the user and notes for the analyst. |
| **Links** | Lookalike and block-listed domains, links whose visible text shows a different site, raw IPs, `@` tricks, shorteners, abused TLDs and free hosting, credential paths over HTTP, and `javascript:`/`data:` links. |
| **Attachments (static)** | Executables, double extensions, right-to-left-override names, disk images (ISO/IMG/VHD), macros (including legacy Office), remote templates, DDE, Equation Editor exploits, PDF JavaScript/Launch, password-protected archives, archives containing executables, HTML login pages, HTML smuggling and known-bad hashes. |
| **Attachment detonation** | Suspicious files are executed in **CAPE Sandbox** (see below). A conviction blocks the file hash everywhere and pulls the message from every mailbox. |

Scores combine into a verdict:

| Score | Verdict | What happens |
|---|---|---|
| **70 or more** (`PG_QUARANTINE_SCORE`) | malicious | Quarantined |
| **40 or more** (`PG_WARN_SCORE`) | suspicious | Delivered, and the user is warned |
| Below 40 | clean | Delivered normally |

Every verdict lists its reasons with their points, so analysts can see why a decision was made.

## Connecting your mail

### Microsoft 365: API mode (start here)

1. In Microsoft Entra, go to **App registrations → New registration**.
2. Add these Microsoft Graph **application** permissions and grant admin consent:

   | Permission | Used for |
   |---|---|
   | `Mail.ReadWrite` | Reading mail, quarantining and purging |
   | `Mail.Send` | Alerts |
   | `User.Read.All` | Listing mailboxes |

3. Restrict the app to the mailboxes you protect using an Exchange *RBAC for Applications* scope.
4. Set `PG_M365_TENANT_ID`, `PG_M365_CLIENT_ID` and `PG_M365_CLIENT_SECRET`.
5. Optionally set `PG_M365_MAILBOXES` to a comma-separated list (otherwise all enabled users are protected) and `PG_ALERT_FROM` (a mailbox that sends alerts).

What PhishGuard then does:
- **Watches inboxes** for new mail, about every minute.
- **Quarantines** threats into a hidden **PhishGuard Quarantine** folder. Users can't see it, and analysts can release from it.
- **Suspicious mail** gets a red *PhishGuard: Suspicious* category and is flagged. The user receives an alert e-mail with a **Report it to IT Security** link.
- **Existing mail is not scanned on first connection.** Use retro-hunts (below) for that.

### Any mail system: gateway mode

```bash
PG_SMTP_NEXT_HOP=abelzeta-com.mail.protection.outlook.com:25 python run.py gateway
```

How to set it up:
- Point your MX record, or an inbound connector, at the gateway (port `PG_SMTP_LISTEN_PORT`, default 2525). It relays clean mail to `PG_SMTP_NEXT_HOP`.
- For Exchange Online, turn on *Enhanced Filtering for Connectors* so Exchange still sees the real sender IP.
- This works in front of Google Workspace and on-premises servers too.

What gateway mode adds:
- **Threats are held** and never reach the mailbox. Analysts can release them.
- **Every web link is rewritten** to `https://<PhishGuard>/c/<token>` (time-of-click protection, below).
- **Suspicious mail** gets a red banner at the top and `[SUSPICIOUS]` in the subject.
- **The banner has a one-click Report phishing link.** It opens a confirmation page rather than reporting immediately, so link-scanners can't file reports.
- **URLs quoted in banners and alerts are defanged** (`hxxp://evil[.]com`) so they can't be clicked.

> Link rewriting needs gateway mode. Microsoft Graph cannot change the body of a delivered message.
> In API-only mode, links are still analysed at delivery and in retro-hunts, but clicks aren't intercepted.

## Time-of-click protection

When someone clicks a rewritten link, PhishGuard checks it again at that moment:
- **Redirects are followed one hop at a time**, and each hop is checked.
- **The landing page is inspected.** Password forms imitating a brand, login forms that post to another site, obfuscated JavaScript and file downloads all raise the score.
- **The link is checked against the latest threat intel.**

The result:
- **Clean:** the user is redirected straight to the site.
- **Suspicious:** a warning page, with a "continue anyway" link.
- **Dangerous:** blocked, with a "Report this e-mail" button.

**Post-delivery remediation:** if a page turned malicious after the e-mail arrived, the URL is added to the block list and every delivered message containing it is pulled.

The page fetcher refuses anything that resolves to private, loopback, link-local or cloud-metadata addresses (SSRF protection), including after redirects.

## Reporting suspicious mail

All of these land in the console's **Reported** queue and notify `PG_SECURITY_EMAIL`:

- **Outlook "Report Phishing" button** (desktop, web and Mac):
  1. In the Microsoft 365 admin center, go to **Settings → Integrated apps → Upload custom apps → Provide link to manifest file**.
  2. Enter `https://<PhishGuard>/addin/manifest.xml`. HTTPS is required.
  - In API mode PhishGuard checks the message really is in the reporter's mailbox before accepting the report.
- **Forward as attachment** from any mail client to `PG_REPORT_MAILBOX` (API mode).
- **Report phishing** link in warning banners and alert e-mails.

When an analyst confirms a report:
- The sender, the sender's domain (unless it is shared webmail or your own), the links and the attachment hashes are added to the block list.
- **Every copy is pulled from every mailbox.**
- Earlier deliveries that match are hunted down.
- Reporters get a thank-you e-mail.

If the analyst marks it safe instead, the message is released.

## Post-delivery remediation

| Trigger | Action |
|---|---|
| Analyst clicks **Purge from all mailboxes** | Finds the message by Message-ID in every mailbox. Moves it to quarantine (reversible) or deletes it permanently. |
| New block-list indicator (analyst, confirmed report, sandbox, time-of-click) | **Retro-hunt:** scanned messages still in mailboxes that contain the sender, domain, URL or file hash are quarantined. |
| Sandbox convicts an attachment | Hash is block-listed, then retro-hunt |
| Link turns malicious at click time | URL is block-listed, then retro-hunt |

All actions are recorded in the **Activity** log.

## AI analysis with Claude

- **What is sent:** for external mail, the sender details, authentication results, message text, link URLs, attachment *names* and the other detectors' findings go to Anthropic's API.
- **What is not sent:** internal, authenticated mail, mail from allow-listed senders, and mail already certain to be malicious.
- **Model and safeguards:** it uses `claude-opus-5-5` with structured output. The e-mail is passed as untrusted data, and an e-mail that tries to instruct the AI is itself treated as a red flag. If the model declines to assess a message, the server-side refusal fallback retries it automatically.
- **If the AI is unavailable,** scanning continues on the other detectors.
- **Effect on the score:** a confident AI verdict can raise a score, or lower one that rests only on weak signals. It never clears hard evidence such as failed DMARC, lookalike domains or malicious files.
- **Cost:** roughly 2–5 US cents per external message at current Opus prices (input plus the model's reasoning and verdict). Check with your own traffic before rolling it out widely.
- **Settings:** `PG_LLM_ENABLED=false` turns it off. `PG_LLM_MODEL` changes the model. Check that sending mail content to Anthropic fits your data-protection policy (DPA, data residency) first.

## Attachment detonation (CAPE Sandbox)

Static analysis runs on every attachment and covers most commodity malware. To actually run suspicious files and watch what they do, you need a sandbox:
1. Install **[CAPE Sandbox](https://github.com/kevoreilly/CAPEv2)** on a separate, network-isolated host. It runs files in disposable Windows VMs.
2. Set `PG_CAPE_URL` and `PG_CAPE_TOKEN`.

Suspicious attachments are then submitted automatically. A malscore of **6/10 or more** convicts the file.

## Configuration reference

| Variable | Default | Purpose |
|---|---|---|
| `PG_DB_PATH` | `phishguard.db` | Database. Stored message copies go in `message_store/` beside it. |
| `PG_BASE_URL` | `http://127.0.0.1:8100` | Public HTTPS address. Used in rewritten links, report links and the add-in manifest. |
| `PG_SECRET_KEY` | random | Signs report links. **Set it in production**, or links break on restart. |
| `PG_ADMIN_PASSWORD` | generated | First console password |
| `PG_ORG_DOMAINS`, `PG_PROTECTED_DOMAINS`, `PG_VIP_NAMES` | – | Your domains, extra brands to protect, executive names |
| `PG_WARN_SCORE` / `PG_QUARANTINE_SCORE` | 40 / 70 | Verdict thresholds |
| `PG_LLM_ENABLED`, `PG_LLM_MODEL`, `PG_LLM_MAX_CHARS` | true, `claude-opus-5-5`, 60000 | AI analysis |
| `PG_DNS_CHECKS`, `PG_CLICK_FETCH` | true | Live DNS (SPF/DMARC) and live page inspection at click time |
| `PG_M365_TENANT_ID`, `PG_M365_CLIENT_ID`, `PG_M365_CLIENT_SECRET`, `PG_M365_MAILBOXES`, `PG_M365_POLL_SECONDS` | – | Microsoft 365 API mode |
| `PG_REPORT_MAILBOX`, `PG_SECURITY_EMAIL`, `PG_ALERT_FROM` | – | Report-by-forwarding mailbox, SOC address, alert sender |
| `PG_SMTP_LISTEN_HOST`, `PG_SMTP_LISTEN_PORT`, `PG_SMTP_NEXT_HOP` | 0.0.0.0, 2525, – | Gateway mode |
| `PG_CAPE_URL`, `PG_CAPE_TOKEN` | – | Sandbox |

## Console roles

| Role | Can |
|---|---|
| **admin** | Everything, including users |
| **analyst** | Review reports, quarantine, release, purge and manage threat intel |
| **viewer** | Read only |

Passwords must be 12+ characters and are changed at first sign-in. Five wrong attempts lock the account. Sessions expire after 30 idle minutes.

## Status and limitations

- **Tested against simulated services only:**
  - The Microsoft Graph integration follows Microsoft's documented API.
  - The Outlook add-in manifest is written to Microsoft's schema but hasn't been loaded into real Outlook.
  - The CAPE client follows the CAPEv2 REST API.

  Before production, test each one in a pilot: a test tenant with a few mailboxes, and a CAPE instance.
- **Time-of-click page checks** resolve the host, then fetch it. A DNS-rebinding attacker could change the answer in between, so run PhishGuard where it can't reach sensitive internal services anyway.
- **Lookalike detection** uses a built-in list of second-level suffixes rather than the full Public Suffix List.
- **No replacement for Microsoft Defender for Office 365.** If you already have it, PhishGuard adds the AI reading, your own reporting workflow and threat intel on top. Run both at first and compare.
