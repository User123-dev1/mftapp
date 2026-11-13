"""SMTP gateway mode: scan in transit, rewrite links, add warning banners.

Place it in front of your mail server (any provider):
  internet -> PhishGuard gateway (this) -> Exchange Online / Google Workspace / on-prem
For Exchange Online: point MX (or an inbound connector) at the gateway, and
deliver onward to your tenant's MX endpoint (PG_SMTP_NEXT_HOP). Enable
"Enhanced Filtering for Connectors" so Exchange still sees the original sender IP.
"""

from __future__ import annotations

import html
import logging
import smtplib
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from typing import Callable, List

from .urls import defang, rewrite_html, rewrite_text

logger = logging.getLogger("phishguard.gateway")


def smtp_deliver(next_hop: str) -> Callable[[bytes, List[str]], None]:
    host, _, port = next_hop.partition(":")

    def deliver(raw: bytes, recipients: List[str], mail_from: str = "") -> None:
        with smtplib.SMTP(host, int(port or 25), timeout=60) as smtp:
            smtp.ehlo()
            if smtp.has_extn("starttls"):
                smtp.starttls()
                smtp.ehlo()
            smtp.sendmail(mail_from or "", recipients, raw)
    return deliver


def banner_html(summary: str, reasons: List[str], report_url: str) -> str:
    items = "".join(f"<li>{html.escape(defang(r)[:200])}</li>" for r in reasons[:3])
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;'
        'margin:0 0 12px 0"><tr><td style="background:#fdecea;border-left:6px solid #b42318;padding:10px 14px;'
        'font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#7a1610">'
        '<b>Caution - PhishGuard flagged this e-mail as suspicious.</b> '
        f'{html.escape(defang(summary)[:300])}<ul style="margin:6px 0 6px 18px;padding:0">{items}</ul>'
        "Do not click links, open attachments or send payments or passwords unless you have verified the sender "
        "by phone. "
        f'<a href="{html.escape(report_url)}" style="color:#7a1610;font-weight:bold">Report phishing</a>'
        "</td></tr></table>")


def banner_text(summary: str, report_url: str) -> str:
    return ("CAUTION - PhishGuard flagged this e-mail as suspicious. " + defang(summary)[:300] +
            f"\nDo not click links or open attachments unless you have verified the sender. "
            f"Report phishing: {report_url}\n" + "-" * 60 + "\n\n")


def transform(raw: bytes, make_link: Callable[[str], str], *, warn: bool, summary: str, reasons: List[str],
              report_url: str, verdict_header: str) -> bytes:
    """Rewrite every http(s) link to the time-of-click checker and, if warned, add banners."""
    msg: EmailMessage = BytesParser(policy=policy.default).parsebytes(raw)
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/html", "text/plain"):
            continue
        try:
            body = part.get_content()
        except Exception:
            continue
        if ctype == "text/html":
            body = rewrite_html(body, make_link)
            if warn:
                lowered = body.lower()
                pos = lowered.find("<body")
                insert_at = lowered.find(">", pos) + 1 if pos != -1 else 0
                body = body[:insert_at] + banner_html(summary, reasons, report_url) + body[insert_at:]
            part.set_content(body, subtype="html", charset="utf-8", cte="quoted-printable")
        else:
            body = rewrite_text(body, make_link)
            if warn:
                body = banner_text(summary, report_url) + body
            part.set_content(body, charset="utf-8", cte="quoted-printable")
    del msg["X-PhishGuard-Verdict"]
    msg["X-PhishGuard-Verdict"] = verdict_header
    if warn and not str(msg.get("Subject", "")).startswith("[SUSPICIOUS]"):
        subject = str(msg.get("Subject", ""))
        del msg["Subject"]
        msg["Subject"] = f"[SUSPICIOUS] {subject}"
    return msg.as_bytes(policy=policy.SMTP)


class GatewayHandler:
    """aiosmtpd handler. Accepts mail, scans it, then quarantines or relays."""

    def __init__(self, guard, deliver: Callable):
        self.guard = guard
        self.deliver = deliver

    def process(self, raw: bytes, mail_from: str, recipients: List[str], peer_ip: str) -> str:
        guard = self.guard
        message_id, result = guard.process(raw, "gateway", mailbox=",".join(r.lower() for r in recipients),
                                           client_ip=peer_ip, mail_from=mail_from)
        if result.verdict == "malicious":
            logger.info("Quarantined message %s from %s (score %s)", message_id, mail_from, result.score)
            return "250 OK: queued as PG%d" % message_id    # accepted, held in quarantine
        for rcpt in recipients:
            out = transform(raw, lambda url, r=rcpt: guard.make_link(url, message_id, r),
                            warn=result.verdict == "suspicious", summary=result.summary, reasons=result.reasons,
                            report_url=guard.report_link(message_id, rcpt),
                            verdict_header=f"{result.verdict}; score={result.score}; category={result.category}")
            try:
                self.deliver(out, [rcpt], mail_from)
            except (OSError, smtplib.SMTPException) as exc:
                logger.warning("Relay to next hop failed: %s", exc)
                return "451 4.4.1 Temporary delivery failure, try again later"
        return "250 OK: delivered PG%d" % message_id

    async def handle_DATA(self, server, session, envelope):
        import asyncio
        raw = envelope.original_content or envelope.content
        peer = session.peer[0] if session.peer else ""
        return await asyncio.to_thread(self.process, raw, envelope.mail_from, list(envelope.rcpt_tos), peer)


def run_gateway(guard, settings) -> None:  # pragma: no cover - needs a socket
    from aiosmtpd.controller import Controller
    if not settings.smtp_next_hop:
        raise SystemExit("PG_SMTP_NEXT_HOP is required in gateway mode (host:port of your mail server)")
    deliver = smtp_deliver(settings.smtp_next_hop)
    guard.deliver = lambda raw, rcpts: deliver(raw, rcpts)
    controller = Controller(GatewayHandler(guard, deliver), hostname=settings.smtp_listen_host,
                            port=settings.smtp_listen_port, data_size_limit=50 * 1024 * 1024)
    controller.start()
    logger.info("SMTP gateway listening on %s:%s, relaying to %s", settings.smtp_listen_host,
                settings.smtp_listen_port, settings.smtp_next_hop)
