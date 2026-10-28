import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from app.config import get_settings

logger = logging.getLogger(__name__)


def _build_message(to: str, subject: str, text: str, html: str | None) -> EmailMessage:
    settings = get_settings()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.smtp_from_name, settings.smtp_from_email))
    msg["To"] = to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def _send_blocking(msg: EmailMessage) -> None:
    settings = get_settings()
    if settings.smtp_use_ssl:
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout,
            context=ssl.create_default_context(),
        )
    else:
        server = smtplib.SMTP(
            settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout
        )
    with server:
        server.ehlo()
        if settings.smtp_use_tls and not settings.smtp_use_ssl:
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(msg)


async def send_email(to: str, subject: str, text: str, html: str | None = None) -> bool:
    """Send an email. Never raises; returns True only when SMTP accepted the message."""
    settings = get_settings()
    try:
        if not settings.smtp_enabled:
            if settings.app_env.strip().lower() == "development":
                logger.info(
                    "SMTP disabled; email not sent to=%s subject=%r\n%s", to, subject, text
                )
            else:
                logger.info("SMTP disabled; email not sent to=%s subject=%r", to, subject)
            return False
        msg = _build_message(to, subject, text, html)
        await asyncio.to_thread(_send_blocking, msg)
        logger.info("Email sent to=%s subject=%r", to, subject)
        return True
    except Exception:
        logger.exception("Failed to send email to=%s subject=%r", to, subject)
        return False
