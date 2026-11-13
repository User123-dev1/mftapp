"""PhishGuard operations: scan, act, report, review, remediate, hunt."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import os
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .config import Settings
from .db import Database
from .domains import is_internal, registrable_domain
from .emailparse import Link, parse_email
from .engine import FREEMAIL, ScanResult, Scanner
from .graph import GraphClient, GraphError
from .intel import Intel
from .sandbox import CapeSandbox
from .urls import UnsafeDestination, analyze_url, defang, inspect_page, sign

logger = logging.getLogger("phishguard")

OPEN_STATUSES = ("delivered", "warned", "released")   # message is (or may be) in a user's mailbox
CONVICT_MALSCORE = 6.0


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PhishGuard:
    def __init__(self, settings: Settings, db: Database, scanner: Scanner, *, graph: Optional[GraphClient] = None,
                 sandbox: Optional[CapeSandbox] = None, page_get: Optional[Callable] = None,
                 resolver: Optional[Callable] = None, deliver: Optional[Callable[[bytes, List[str]], None]] = None):
        self.s, self.db, self.scanner = settings, db, scanner
        self.graph, self.sandbox = graph, sandbox
        self.page_get, self.resolver = page_get, resolver
        self.deliver = deliver
        self.store_dir = os.path.join(os.path.dirname(os.path.abspath(settings.db_path)), "message_store")
        os.makedirs(self.store_dir, exist_ok=True)

    # ------------------------------------------------------------------ helpers
    def _log(self, conn, actor: str, action: str, target: str = "", detail: str = "") -> None:
        conn.execute("INSERT INTO actions (at, actor, action, target, detail) VALUES (?, ?, ?, ?, ?)",
                     (now_iso(), actor, action, target, detail[:2000]))

    def _store(self, raw: bytes) -> str:
        path = os.path.join(self.store_dir, hashlib.sha256(raw).hexdigest() + ".eml")
        if not os.path.exists(path):
            with open(path, "wb") as fh:
                fh.write(raw)
        return path

    def raw(self, message_id: int) -> bytes:
        row = self.message_row(message_id)
        if not row["raw_path"] or not os.path.exists(row["raw_path"]):
            raise NotFound("No stored copy of this message")
        with open(row["raw_path"], "rb") as fh:
            return fh.read()

    def message_row(self, message_id: int) -> dict:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            raise NotFound(f"Message {message_id} not found")
        return dict(row)

    def intel(self) -> Intel:
        with self.db.read() as conn:
            return Intel(conn)

    def notify(self, to: Iterable[str], subject: str, html_body: str) -> None:
        to = [t for t in to if t]
        if not to:
            return
        try:
            if self.graph and self.s.alert_from and "@" in self.s.alert_from:
                self.graph.send_mail(self.s.alert_from, to, subject, html_body)
            elif self.s.smtp_next_hop:
                msg = EmailMessage()
                msg["From"], msg["To"], msg["Subject"] = self.s.alert_from, ", ".join(to), subject
                msg.set_content("This message requires an HTML-capable mail client.")
                msg.add_alternative(html_body, subtype="html")
                host, _, port = self.s.smtp_next_hop.partition(":")
                with smtplib.SMTP(host, int(port or 25), timeout=20) as smtp:
                    smtp.send_message(msg)
            else:
                logger.info("Notification (no mail transport configured) to %s: %s", to, subject)
        except Exception as exc:  # a failed alert must not break scanning
            logger.warning("Could not send notification '%s': %s", subject, exc)

    # ------------------------------------------------------------------ scanning
    def process(self, raw: bytes, source: str, *, mailbox: Optional[str] = None, graph_id: Optional[str] = None,
                client_ip: Optional[str] = None, mail_from: Optional[str] = None, received_at: Optional[str] = None,
                act: bool = True) -> Tuple[int, ScanResult]:
        result = self.scanner.scan(raw, self.intel(), client_ip=client_ip, mail_from=mail_from)
        if not act:
            status = "reported"
        elif result.verdict == "malicious":
            status = "quarantined"
        elif result.verdict == "suspicious":
            status = "warned"
        else:
            status = "delivered"
        raw_path = self._store(raw) if (result.verdict != "clean" or source in ("report", "gateway")) else None
        e = result.email
        with self.db.tx() as conn:
            cur = conn.execute(
                "INSERT INTO messages (source, mailbox, graph_id, internet_message_id, subject, from_address, "
                "from_display, received_at, scanned_at, score, verdict, category, summary, signals, indicators, llm, "
                "status, raw_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (source, (mailbox or "").lower() or None, graph_id, e.message_id, e.subject[:500], e.from_address,
                 e.from_display[:200], received_at or now_iso(), now_iso(), result.score, result.verdict,
                 result.category, result.summary[:1000], json.dumps([sig.to_dict() for sig in result.signals]),
                 json.dumps(result.indicators), json.dumps(result.llm.to_dict()) if result.llm else None,
                 "delivered" if status == "reported" else status, raw_path))
            message_id = cur.lastrowid
            self._log(conn, "system", "SCANNED", str(message_id),
                      f"{source} {mailbox or ''} score={result.score} verdict={result.verdict} -> {status}")
            for att in result.detonate:
                conn.execute("INSERT INTO sandbox_jobs (message_id, sha256, filename, status, submitted_at) "
                             "VALUES (?, ?, ?, 'pending', ?)", (message_id, att.sha256, att.filename, now_iso()))
        if act and source == "graph" and self.graph and mailbox and graph_id:
            self._graph_apply(message_id, status, mailbox, graph_id, result)
        if result.verdict == "malicious" and self.s.security_team_email:
            self.notify([self.s.security_team_email], f"[PhishGuard] {result.category} quarantined: {e.subject[:80]}",
                        self._alert_html(message_id, result, mailbox, "quarantined"))
        return message_id, result

    def _graph_apply(self, message_id: int, status: str, mailbox: str, graph_id: str, result: ScanResult) -> None:
        try:
            if status == "quarantined":
                folder = self._quarantine_folder(mailbox)
                new_id = self.graph.move(mailbox, graph_id, folder)
                self._set(message_id, status="quarantined", graph_id=new_id)
            elif status == "warned":
                self.graph.tag(mailbox, graph_id, "PhishGuard: Suspicious")
                self.notify([mailbox], f"Caution: suspicious e-mail \"{result.email.subject[:80]}\"",
                            self._user_warning_html(message_id, mailbox, result))
        except GraphError as exc:
            logger.warning("Could not apply %s to %s in %s: %s", status, graph_id, mailbox, exc)
            with self.db.tx() as conn:
                self._log(conn, "system", "ACTION_FAILED", str(message_id), f"{status}: {exc}")

    def _quarantine_folder(self, mailbox: str) -> str:
        with self.db.read() as conn:
            row = conn.execute("SELECT quarantine_folder_id FROM graph_state WHERE mailbox = ?", (mailbox,)).fetchone()
        if row and row["quarantine_folder_id"]:
            return row["quarantine_folder_id"]
        folder = self.graph.quarantine_folder(mailbox, self.s.quarantine_folder)
        with self.db.tx() as conn:
            conn.execute("INSERT INTO graph_state (mailbox, quarantine_folder_id, updated_at) VALUES (?, ?, ?) "
                         "ON CONFLICT(mailbox) DO UPDATE SET quarantine_folder_id = excluded.quarantine_folder_id",
                         (mailbox, folder, now_iso()))
        return folder

    def _set(self, message_id: int, **fields) -> None:
        with self.db.tx() as conn:
            conn.execute(f"UPDATE messages SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                         (*fields.values(), message_id))

    # ------------------------------------------------------------------ analyst actions
    def quarantine(self, message_id: int, actor: str, reason: str = "") -> dict:
        row = self.message_row(message_id)
        if row["status"] in ("quarantined", "purged", "deleted"):
            raise Conflict(f"Message is already {row['status']}")
        if row["graph_id"] and row["mailbox"] and self.graph:
            new_id = self.graph.move(row["mailbox"], row["graph_id"], self._quarantine_folder(row["mailbox"]))
            self._set(message_id, status="quarantined", graph_id=new_id)
        else:
            self._set(message_id, status="quarantined")
        with self.db.tx() as conn:
            self._log(conn, actor, "QUARANTINED", str(message_id), reason)
        return self.message_row(message_id)

    def release(self, message_id: int, actor: str, reason: str = "") -> dict:
        row = self.message_row(message_id)
        if row["status"] != "quarantined":
            raise Conflict("Only quarantined messages can be released")
        if row["graph_id"] and row["mailbox"] and self.graph:
            new_id = self.graph.move(row["mailbox"], row["graph_id"], "inbox")
            self._set(message_id, status="released", graph_id=new_id)
        elif row["source"] == "gateway":
            if not self.deliver:
                raise Conflict("Gateway delivery is not configured (PG_SMTP_NEXT_HOP)")
            self.deliver(self.raw(message_id), [m for m in (row["mailbox"] or "").split(",") if m])
            self._set(message_id, status="released")
        else:
            self._set(message_id, status="released")
        with self.db.tx() as conn:
            self._log(conn, actor, "RELEASED", str(message_id), reason)
        return self.message_row(message_id)

    def purge_everywhere(self, message_id: int, actor: str, mode: str = "quarantine", reason: str = "") -> dict:
        """Pull every copy of this message (same Internet Message-ID) out of every mailbox."""
        if mode not in ("quarantine", "delete"):
            raise ValueError("mode must be quarantine or delete")
        row = self.message_row(message_id)
        imid = row["internet_message_id"]
        touched: List[str] = []
        if self.graph and imid:
            mailboxes = self.s.m365_mailboxes or self.graph.mailboxes()
            for mb in mailboxes:
                try:
                    for m in self.graph.find_by_internet_message_id(mb, imid):
                        if mode == "delete":
                            self.graph.permanent_delete(mb, m["id"])
                        elif m.get("parentFolderId") != self._quarantine_folder(mb):
                            self.graph.move(mb, m["id"], self._quarantine_folder(mb))
                        touched.append(mb)
                except GraphError as exc:
                    logger.warning("Purge in %s failed: %s", mb, exc)
        new_status = "deleted" if mode == "delete" else "purged"
        with self.db.tx() as conn:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM messages WHERE (internet_message_id = ? AND ? != '') OR id = ?", (imid or "", imid or "", message_id))]
            conn.executemany("UPDATE messages SET status = ? WHERE id = ?", [(new_status, i) for i in ids])
            self._log(conn, actor, "PURGED" if mode == "quarantine" else "DELETED_EVERYWHERE", str(message_id),
                      f"{reason} | mailboxes: {', '.join(sorted(set(touched))) or 'none in M365'}")
        return {"message_ids": ids, "mailboxes": sorted(set(touched)), "status": new_status}

    # ------------------------------------------------------------------ threat intel + retro-hunt
    def add_indicator(self, kind: str, value: str, action: str, source: str, actor: str, note: str = "",
                      hunt: bool = True) -> dict:
        value = (value or "").strip().lower()
        if kind not in ("domain", "url", "sender", "hash", "ip") or action not in ("block", "allow") or not value:
            raise ValueError("Invalid indicator")
        if action == "block" and kind == "domain" and (value in FREEMAIL or is_internal(value, self.s.org_domains)
                                                       or value in self.s.all_protected()):
            raise ValueError(f"Refusing to block {value}: it is a shared, internal or protected domain "
                             "(block the specific sender or URL instead)")
        with self.db.tx() as conn:
            conn.execute("INSERT INTO indicators (kind, value, action, source, note, added_by, added_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(kind, value, action) DO UPDATE SET active = 1",
                         (kind, value, action, source, note, actor, now_iso()))
            self._log(conn, actor, f"INDICATOR_{action.upper()}", f"{kind}:{value}", note)
        hits = self.retro_hunt([(kind, value)], actor, f"new {kind} indicator") if hunt and action == "block" else []
        return {"kind": kind, "value": value, "action": action, "remediated": hits}

    def retro_hunt(self, indicators: List[Tuple[str, str]], actor: str, reason: str) -> List[int]:
        """Post-delivery remediation: quarantine delivered mail that matches newly-known indicators."""
        matches: List[int] = []
        with self.db.read() as conn:
            rows = conn.execute(f"SELECT id, indicators FROM messages WHERE status IN "
                                f"({','.join('?' * len(OPEN_STATUSES))})", OPEN_STATUSES).fetchall()
        for row in rows:
            ind = json.loads(row["indicators"])
            for kind, value in indicators:
                values = [v.lower() for v in ind.get(kind, [])]
                if kind == "domain":
                    values += [registrable_domain(v) for v in values]
                if value.lower() in values:
                    matches.append(row["id"])
                    break
        done = []
        for mid in matches:
            try:
                self.quarantine(mid, actor, f"Retro-hunt: {reason}")
                done.append(mid)
            except (Conflict, GraphError) as exc:
                logger.warning("Retro-hunt could not quarantine %s: %s", mid, exc)
        return done

    # ------------------------------------------------------------------ user reports
    def report(self, reporter: str, channel: str, *, message_id: Optional[int] = None, raw: Optional[bytes] = None,
               internet_message_id: Optional[str] = None, comment: str = "") -> dict:
        reporter = (reporter or "").strip().lower()
        if not reporter:
            raise ValueError("Reporter is required")
        if message_id is None and internet_message_id:
            with self.db.read() as conn:
                row = conn.execute("SELECT id FROM messages WHERE internet_message_id = ? AND "
                                   "(mailbox = ? OR mailbox LIKE ?) ORDER BY id DESC LIMIT 1",
                                   (internet_message_id, reporter, f"%{reporter}%")).fetchone()
            if row:
                message_id = row["id"]
            elif self.graph:
                found = self.graph.find_by_internet_message_id(reporter, internet_message_id)
                if not found:
                    raise NotFound("That message is not in your mailbox")
                raw = self.graph.get_mime(reporter, found[0]["id"])
                message_id, _ = self.process(raw, "report", mailbox=reporter, graph_id=found[0]["id"], act=False)
            else:
                raise NotFound("Message not known to PhishGuard")
        elif message_id is None and raw is not None:
            message_id, _ = self.process(raw, "report", mailbox=reporter, act=False)
        if message_id is None:
            raise ValueError("Nothing to report")
        row = self.message_row(message_id)
        with self.db.tx() as conn:
            existing = conn.execute("SELECT id FROM reports WHERE message_id = ? AND reporter = ?",
                                    (message_id, reporter)).fetchone()
            if existing:
                return {"report_id": existing["id"], "duplicate": True, "message_id": message_id}
            cur = conn.execute("INSERT INTO reports (message_id, reporter, channel, comment, reported_at, status) "
                               "VALUES (?, ?, ?, ?, ?, 'new')", (message_id, reporter, channel, comment[:2000], now_iso()))
            self._log(conn, reporter, "REPORTED", str(message_id), f"via {channel}: {comment[:200]}")
            report_id = cur.lastrowid
        if self.s.security_team_email:
            self.notify([self.s.security_team_email], f"[PhishGuard] Phishing reported by {reporter}: {row['subject'][:80]}",
                        f"<p>{html.escape(reporter)} reported a message via {html.escape(channel)}.</p>"
                        f"<p><b>{html.escape(row['subject'] or '')}</b> from {html.escape(row['from_address'] or '')}<br>"
                        f"PhishGuard score {row['score']} ({row['verdict']}).</p>"
                        f"<p><a href=\"{self.s.base_url}/#reports\">Review it in PhishGuard</a></p>")
        return {"report_id": report_id, "duplicate": False, "message_id": message_id}

    def review_report(self, report_id: int, decision: str, actor: str, note: str = "", *,
                      block: bool = True, purge: bool = True) -> dict:
        if decision not in ("confirmed", "false_positive"):
            raise ValueError("decision must be confirmed or false_positive")
        with self.db.read() as conn:
            rep = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        if rep is None:
            raise NotFound("Report not found")
        if rep["status"] != "new":
            raise Conflict(f"Report already reviewed ({rep['status']})")
        row = self.message_row(rep["message_id"])
        added, purged, hunted = [], {}, []
        if decision == "confirmed":
            if block:
                for kind, value in self._indicators_to_block(row):
                    try:
                        self.add_indicator(kind, value, "block", f"report #{report_id}", actor, note, hunt=False)
                        added.append(f"{kind}:{value}")
                    except ValueError:
                        pass
            if purge:
                purged = self.purge_everywhere(row["id"], actor, "quarantine", f"Confirmed phishing (report #{report_id})")
            hunted = self.retro_hunt([tuple(a.split(":", 1)) for a in added], actor, f"report #{report_id}")
            status_note = note or "Confirmed malicious"
        else:
            if row["status"] == "quarantined":
                self.release(row["id"], actor, f"False positive (report #{report_id})")
            status_note = note or "Not malicious"
        with self.db.tx() as conn:
            conn.execute("UPDATE reports SET status = ?, reviewed_by = ?, reviewed_at = ?, review_note = ? "
                         "WHERE message_id = ? AND status = 'new'", (decision, actor, now_iso(), status_note, row["id"]))
            reporters = [r["reporter"] for r in conn.execute("SELECT reporter FROM reports WHERE message_id = ?", (row["id"],))]
            self._log(conn, actor, f"REPORT_{decision.upper()}", str(report_id), note)
        verdict = "was confirmed as malicious and has been removed from all mailboxes" if decision == "confirmed" \
            else "was reviewed and found to be safe"
        self.notify(reporters, "Thank you for reporting a suspicious e-mail",
                    f"<p>The message <b>{html.escape(row['subject'] or '')}</b> you reported {verdict}.</p>"
                    "<p>Thank you for helping keep the organisation safe.</p>")
        return {"decision": decision, "indicators_added": added, "purge": purged, "retro_hunt": hunted}

    def _indicators_to_block(self, row: dict) -> List[Tuple[str, str]]:
        ind = json.loads(row["indicators"])
        signals = json.loads(row["signals"])
        out: List[Tuple[str, str]] = []
        for sender in ind.get("sender", []):
            out.append(("sender", sender))
            domain = sender.rsplit("@", 1)[-1]
            if domain not in FREEMAIL and not is_internal(domain, self.s.org_domains) \
                    and domain not in self.s.all_protected():
                out.append(("domain", registrable_domain(domain)))
        flagged_text = " ".join(sig["text"] for sig in signals if sig["area"] == "link")
        for url in ind.get("url", []):
            out.append(("url", url))
        for domain in ind.get("domain", []):
            if domain in flagged_text and domain not in FREEMAIL and domain not in self.s.all_protected() \
                    and not is_internal(domain, self.s.org_domains):
                out.append(("domain", domain))
        out += [("hash", h) for h in ind.get("hash", [])]
        return list(dict.fromkeys(out))

    # ------------------------------------------------------------------ time-of-click
    def make_link(self, url: str, message_id: Optional[int], recipient: str = "") -> str:
        token = secrets.token_urlsafe(12)
        with self.db.tx() as conn:
            conn.execute("INSERT INTO links (token, message_id, recipient, url, created_at) VALUES (?, ?, ?, ?, ?)",
                         (token, message_id, recipient, url, now_iso()))
        return f"{self.s.base_url}/c/{token}"

    def report_link(self, message_id: int, recipient: str) -> str:
        r = base64.urlsafe_b64encode(recipient.encode()).decode().rstrip("=")
        return f"{self.s.base_url}/r/{message_id}/{r}/{sign(self.s.secret_key, f'{message_id}:{recipient}')}"

    def verify_report_link(self, message_id: int, r: str, sig: str) -> str:
        recipient = base64.urlsafe_b64decode(r + "=" * (-len(r) % 4)).decode()
        if not secrets.compare_digest(sign(self.s.secret_key, f"{message_id}:{recipient}"), sig):
            raise NotFound("Invalid report link")
        return recipient

    def click(self, token: str, client_ip: str = "") -> dict:
        with self.db.read() as conn:
            link = conn.execute("SELECT * FROM links WHERE token = ?", (token,)).fetchone()
            cached = None
            if link:
                cached = conn.execute("SELECT * FROM url_cache WHERE url = ?", (link["url"],)).fetchone()
        if link is None:
            raise NotFound("Unknown link")
        url = link["url"]
        intel = self.intel()
        if cached and datetime.fromisoformat(cached["checked_at"]) > \
                datetime.now(timezone.utc) - timedelta(minutes=self.s.click_cache_minutes):
            result = json.loads(cached["result"])
        else:
            result = self._evaluate_click(url, intel)
            with self.db.tx() as conn:
                conn.execute("INSERT INTO url_cache (url, checked_at, result) VALUES (?, ?, ?) ON CONFLICT(url) "
                             "DO UPDATE SET checked_at = excluded.checked_at, result = excluded.result",
                             (url, now_iso(), json.dumps(result)))
        # threat intel can change at any time - re-check even a cached result
        static_block = intel.blocked("url", url) is not None
        if static_block and result["decision"] != "blocked":
            result = dict(result, decision="blocked", score=100, reasons=["Link is on the block list"] + result["reasons"])
        with self.db.tx() as conn:
            conn.execute("INSERT INTO clicks (token, url, final_url, recipient, clicked_at, verdict, score, reasons, client_ip) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (token, url, result.get("final_url"), link["recipient"],
                                                                now_iso(), result["decision"], result["score"],
                                                                json.dumps(result["reasons"]), client_ip))
        if result["decision"] == "blocked" and result["score"] >= self.s.quarantine_score and not static_block:
            # the page turned malicious after delivery: block it and pull every message that carries it
            try:
                self.add_indicator("url", url, "block", "time-of-click", "system",
                                   "; ".join(result["reasons"][:3]), hunt=True)
            except ValueError:
                pass
        return dict(result, url=url, recipient=link["recipient"], message_id=link["message_id"])

    def _evaluate_click(self, url: str, intel: Intel) -> dict:
        protected = self.s.all_protected()
        static = analyze_url(Link(url), protected, intel.blocked)
        score, reasons, final_url = static.score, list(static.reasons), url
        if self.s.click_fetch and self.page_get:
            try:
                insp = inspect_page(url, protected, intel.blocked, self.page_get, self.resolver)
                score, reasons, final_url = max(score, insp.score), list(dict.fromkeys(reasons + insp.reasons)), insp.final_url
            except UnsafeDestination as exc:
                score, reasons = 100, reasons + [f"Destination is not allowed: {exc}"]
            except Exception as exc:
                reasons.append(f"Page could not be checked live ({type(exc).__name__}); static checks only")
                score = max(score, 20)
        decision = "blocked" if score >= self.s.quarantine_score else "warned" if score >= self.s.warn_score else "allowed"
        return {"decision": decision, "score": min(score, 100), "reasons": reasons, "final_url": final_url}

    # ------------------------------------------------------------------ sandbox
    def sandbox_cycle(self) -> List[str]:
        """Submit pending detonations and collect finished ones. Returns convicted hashes."""
        if not self.sandbox:
            return []
        convicted = []
        with self.db.read() as conn:
            jobs = [dict(r) for r in conn.execute("SELECT * FROM sandbox_jobs WHERE status = 'pending' LIMIT 20")]
        for job in jobs:
            try:
                if not job["task_id"]:
                    data = next((a.data for a in parse_email(self.raw(job["message_id"])).attachments
                                 if a.sha256 == job["sha256"]), None)
                    if data is None:
                        self._job(job["id"], status="failed")
                        continue
                    self._job(job["id"], task_id=self.sandbox.submit(job["filename"], data))
                    continue
                state, malscore, summary = self.sandbox.result(job["task_id"])
                if state == "pending":
                    continue
                self._job(job["id"], status=state, malscore=malscore, report=json.dumps(summary),
                          finished_at=now_iso())
                if state == "done" and malscore is not None and malscore >= CONVICT_MALSCORE:
                    convicted.append(job["sha256"])
                    self.add_indicator("hash", job["sha256"], "block", "sandbox", "system",
                                       f"CAPE malscore {malscore}: {', '.join((summary or {}).get('signatures', [])[:3])}")
            except Exception as exc:
                logger.warning("Sandbox job %s: %s", job["id"], exc)
        return convicted

    def _job(self, job_id: int, **fields) -> None:
        with self.db.tx() as conn:
            conn.execute(f"UPDATE sandbox_jobs SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                         (*fields.values(), job_id))

    # ------------------------------------------------------------------ Microsoft 365 polling
    def poll_m365(self) -> int:
        if not self.graph:
            return 0
        count = 0
        mailboxes = self.s.m365_mailboxes or self.graph.mailboxes()
        for mb in mailboxes:
            with self.db.read() as conn:
                st = conn.execute("SELECT delta_link FROM graph_state WHERE mailbox = ?", (mb,)).fetchone()
            first_run = not (st and st["delta_link"])
            try:
                ids, delta = self.graph.inbox_delta(mb, st["delta_link"] if st else None)
            except GraphError as exc:
                logger.warning("Delta query for %s failed: %s", mb, exc)
                continue
            with self.db.tx() as conn:
                conn.execute("INSERT INTO graph_state (mailbox, delta_link, updated_at) VALUES (?, ?, ?) "
                             "ON CONFLICT(mailbox) DO UPDATE SET delta_link = excluded.delta_link, "
                             "updated_at = excluded.updated_at", (mb, delta, now_iso()))
            if first_run:
                continue  # baseline: existing mail is not re-scanned (use retro-hunts for that)
            for gid in ids:
                with self.db.read() as conn:
                    if conn.execute("SELECT 1 FROM messages WHERE mailbox = ? AND graph_id = ?", (mb, gid)).fetchone():
                        continue
                try:
                    raw = self.graph.get_mime(mb, gid)
                except GraphError as exc:
                    logger.warning("Fetch %s/%s failed: %s", mb, gid, exc)
                    continue
                if self.s.report_mailbox and mb == self.s.report_mailbox:
                    self._handle_forwarded_report(raw)
                else:
                    self.process(raw, "graph", mailbox=mb, graph_id=gid)
                count += 1
        return count

    def _handle_forwarded_report(self, raw: bytes) -> None:
        """Users forward suspicious mail (as attachment) to the report mailbox from any client."""
        outer = parse_email(raw)
        inner = next((a.data for a in outer.attachments if a.content_type == "message/rfc822" or
                      a.filename.lower().endswith(".eml")), None)
        self.report(outer.from_address, "forward", raw=inner or raw, comment=outer.subject)

    # ------------------------------------------------------------------ HTML snippets
    def _alert_html(self, message_id: int, result: ScanResult, mailbox: Optional[str], action: str) -> str:
        reasons = "".join(f"<li>{html.escape(defang(r))}</li>" for r in result.reasons[:6])
        return (f"<p>PhishGuard {action} a message{(' for ' + html.escape(mailbox)) if mailbox else ''}.</p>"
                f"<p><b>{html.escape(result.email.subject)}</b><br>From: {html.escape(result.email.from_address)}<br>"
                f"Score {result.score} - {result.category}</p><ul>{reasons}</ul>"
                f"<p><a href=\"{self.s.base_url}/#message/{message_id}\">Open in PhishGuard</a></p>")

    def _user_warning_html(self, message_id: int, mailbox: str, result: ScanResult) -> str:
        reasons = "".join(f"<li>{html.escape(defang(r))}</li>" for r in result.reasons[:4])
        return (f"<p>The e-mail <b>{html.escape(result.email.subject)}</b> from "
                f"{html.escape(result.email.from_address)} looks suspicious.</p><p>{html.escape(defang(result.summary))}</p>"
                f"<ul>{reasons}</ul><p>Do not click its links, open attachments or reply with payment details.</p>"
                f"<p><a href=\"{self.report_link(message_id, mailbox)}\">Report it to IT Security</a></p>")

    # ------------------------------------------------------------------ dashboard
    def stats(self, days: int = 7) -> dict:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.db.read() as conn:
            q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
            return {
                "scanned": q("SELECT COUNT(*) FROM messages WHERE scanned_at >= ?", since),
                "malicious": q("SELECT COUNT(*) FROM messages WHERE scanned_at >= ? AND verdict = 'malicious'", since),
                "suspicious": q("SELECT COUNT(*) FROM messages WHERE scanned_at >= ? AND verdict = 'suspicious'", since),
                "quarantined_now": q("SELECT COUNT(*) FROM messages WHERE status = 'quarantined'"),
                "open_reports": q("SELECT COUNT(*) FROM reports WHERE status = 'new'"),
                "blocked_clicks": q("SELECT COUNT(*) FROM clicks WHERE clicked_at >= ? AND verdict = 'blocked'", since),
                "indicators": q("SELECT COUNT(*) FROM indicators WHERE active = 1 AND action = 'block'"),
                "by_category": {r[0]: r[1] for r in conn.execute(
                    "SELECT category, COUNT(*) FROM messages WHERE scanned_at >= ? AND verdict != 'clean' "
                    "GROUP BY category", (since,))},
            }
