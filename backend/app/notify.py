"""Flag a chat for the shop team, and email them the moment it happens."""
import logging
import smtplib
import threading
from email.message import EmailMessage

from .config import BUSINESS, env

log = logging.getLogger(__name__)


def flag_for_human(db, conv, reason: str, stop_bot=False):
    newly_flagged = not conv.needs_human
    conv.needs_human, conv.handoff_reason = True, reason
    if stop_bot:
        conv.bot_enabled = False
    db.commit()
    if newly_flagged:  # one email per handoff, not one per message
        email_owner(f"{BUSINESS['agent_name']} needs a human: {conv.phone}",
                    f"The chat with {conv.phone} ({conv.channel}) needs a reply from the team.\n\nReason: {reason}")


def email_owner(subject: str, body: str):
    to, user = env("ALERT_EMAIL"), env("SMTP_USER")
    if not (to and user):
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(body)
    threading.Thread(target=_send, args=(msg, user), daemon=True).start()  # don't make the customer wait on SMTP


def _send(msg: EmailMessage, user: str):
    try:
        with smtplib.SMTP(env("SMTP_HOST", "smtp.gmail.com"), int(env("SMTP_PORT", "587")), timeout=15) as s:
            s.starttls()
            s.login(user, env("SMTP_PASSWORD"))
            s.send_message(msg)
    except Exception:
        log.exception("owner alert email failed")
