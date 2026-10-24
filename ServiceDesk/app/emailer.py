"""Email notifications.

Emails are first written to the ``email_log`` table (status ``pending``) inside the
same transaction as the ticket change, then delivered by ``deliver_pending`` after
the request finishes. If no SMTP server is configured the message is marked
``logged`` so it can be inspected from the Email Log page while testing.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import database
from .config import settings
from .models import EmailLog, utcnow

logger = logging.getLogger(__name__)


def queue_email(db: Session, to: str, subject: str, body: str, ticket_id: Optional[int] = None) -> EmailLog:
    entry = EmailLog(to_address=to, subject=subject, body=body, ticket_id=ticket_id, status="pending")
    db.add(entry)
    return entry


def _send_smtp(entry: EmailLog) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = entry.to_address
    msg["Subject"] = entry.subject
    msg.set_content(entry.body)

    if settings.smtp_ssl:
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=ssl.create_default_context(), timeout=30)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
    with server:
        if settings.smtp_starttls and not settings.smtp_ssl:
            server.starttls(context=ssl.create_default_context())
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)


def deliver_pending() -> None:
    """Send every pending email. Safe to call repeatedly."""
    db = database.SessionLocal()
    try:
        pending = db.scalars(select(EmailLog).where(EmailLog.status == "pending")).all()
        for entry in pending:
            if not settings.smtp_host:
                entry.status = "logged"
                logger.info("SMTP not configured; email to %s logged: %s", entry.to_address, entry.subject)
            else:
                try:
                    _send_smtp(entry)
                    entry.status = "sent"
                    entry.sent_at = utcnow()
                except Exception as exc:  # keep going; record the failure
                    entry.status = "failed"
                    entry.error = str(exc)
                    logger.exception("Failed to send email to %s", entry.to_address)
            db.commit()
    finally:
        db.close()
