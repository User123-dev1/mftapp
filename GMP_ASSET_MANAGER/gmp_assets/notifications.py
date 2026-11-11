"""E-mail notifications.

Notifications are written to the ``notifications`` table inside the business
transaction (so nothing is lost if the mail server is down) and sent
afterwards by :func:`send_pending`. Without SMTP settings, messages stay in
the outbox and people see their work in the "My tasks" screen.
"""

import logging
import smtplib
import threading
from email.message import EmailMessage

from . import clock
from .config import Settings
from .db import Database

logger = logging.getLogger("gmp_assets.notifications")
_lock = threading.Lock()


def send_pending(db: Database, settings: Settings) -> int:
    """Send unsent e-mails; returns how many were sent."""
    if not settings.smtp_host:
        return 0
    with _lock:
        with db.read() as conn:
            pending = [dict(r) for r in conn.execute(
                "SELECT * FROM notifications WHERE sent_at IS NULL AND email IS NOT NULL AND email != '' ORDER BY id LIMIT 200")]
        if not pending:
            return 0
        sent = 0
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
                smtp.ehlo()
                if smtp.has_extn("starttls"):
                    smtp.starttls()
                    smtp.ehlo()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                for n in pending:
                    msg = EmailMessage()
                    msg["From"] = settings.smtp_from or settings.smtp_user
                    msg["To"] = n["email"]
                    msg["Subject"] = n["subject"]
                    msg.set_content(n["body"])
                    try:
                        smtp.send_message(msg)
                        result = (clock.now_iso(), None)
                        sent += 1
                    except smtplib.SMTPException as exc:
                        result = (None, str(exc)[:500])
                    with db.transaction() as conn:
                        conn.execute("UPDATE notifications SET sent_at = ?, error = ? WHERE id = ?", (*result, n["id"]))
        except (OSError, smtplib.SMTPException) as exc:
            logger.warning("E-mail delivery failed: %s", exc)
            with db.transaction() as conn:
                conn.executemany("UPDATE notifications SET error = ? WHERE id = ?", [(str(exc)[:500], n["id"]) for n in pending])
        return sent


def send_in_background(db: Database, settings: Settings) -> None:
    if settings.smtp_host:
        threading.Thread(target=send_pending, args=(db, settings), daemon=True).start()
