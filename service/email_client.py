"""email_client.py – dual-backend transactional email (SMTP / AWS SES).

Backend is selected at startup via ``settings.EMAIL_BACKEND``:
  * ``"smtp"``  – sends through a local or remote Postfix/relay using smtplib
                  (no extra dependencies; works with unauthenticated localhost:25
                  or authenticated STARTTLS/SMTPS submission ports).
  * ``"ses"``   – sends through AWS Simple Email Service using boto3 (legacy,
                  kept for easy rollback).

Public API (unchanged from the SES-only version):
  await send_magic_link(email, token) -> bool
  await send_subscription_welcome(email, token) -> bool
"""
from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import settings
from .logger import get_logger

logger = get_logger(__name__)


# ── SMTP backend ───────────────────────────────────────────────────────────────

def _smtp_send(to: str, subject: str, body_text: str, body_html: str) -> bool:
    """Blocking SMTP send; runs in a thread-pool via asyncio.to_thread."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.EMAIL_FROM
    msg["To"] = to
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        if settings.SMTP_USE_SSL:
            context = ssl.create_default_context()
            cls = smtplib.SMTP_SSL
            kwargs = {"host": settings.SMTP_HOST, "port": settings.SMTP_PORT,
                      "context": context, "timeout": settings.SMTP_TIMEOUT}
        else:
            cls = smtplib.SMTP
            kwargs = {"host": settings.SMTP_HOST, "port": settings.SMTP_PORT,
                      "timeout": settings.SMTP_TIMEOUT}

        with cls(**kwargs) as smtp:
            if settings.SMTP_USE_STARTTLS and not settings.SMTP_USE_SSL:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if settings.SMTP_USERNAME:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.sendmail(settings.EMAIL_FROM, [to], msg.as_bytes())

        logger.info("[smtp] sent %r to %s", subject, to)
        return True

    except (smtplib.SMTPException, OSError) as exc:
        logger.error("[smtp] failed to send to %s: %s", to, exc)
        return False


# ── SES backend ────────────────────────────────────────────────────────────────

_ses = None


def _get_ses():
    global _ses
    if _ses is None:
        import boto3  # lazy import – not needed when backend == "smtp"
        _ses = boto3.client(
            "ses",
            region_name=settings.AWS_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )
    return _ses


def _ses_send(to: str, subject: str, body_text: str, body_html: str) -> bool:
    from botocore.exceptions import BotoCoreError, ClientError
    try:
        _get_ses().send_email(
            Source=settings.AWS_SES_FROM_EMAIL,
            Destination={"ToAddresses": [to]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": body_text, "Charset": "UTF-8"},
                    "Html": {"Data": body_html, "Charset": "UTF-8"},
                },
            },
        )
        logger.info("[ses] sent %r to %s", subject, to)
        return True
    except (BotoCoreError, ClientError) as exc:
        logger.error("[ses] failed to send to %s: %s", to, exc)
        return False


# ── Backend dispatcher ─────────────────────────────────────────────────────────

def _send(to: str, subject: str, body_text: str, body_html: str) -> bool:
    if settings.EMAIL_BACKEND == "ses":
        return _ses_send(to, subject, body_text, body_html)
    return _smtp_send(to, subject, body_text, body_html)


# ── Public helpers ─────────────────────────────────────────────────────────────

async def send_magic_link(email: str, token: str) -> bool:
    url = f"{settings.APP_BASE_URL}/auth/verify/{token}"
    logger.debug("Sending magic link to %s: %s", email, url)
    subject = "Your AdAgent sign-in link"
    body_text = (
        f"Click the link below to sign in to AdAgent (valid for "
        f"{settings.MAGIC_LINK_TTL_MINUTES} minutes):\n\n{url}\n\n"
        "If you did not request this, ignore this email."
    )
    body_html = f"""
<html><body style="font-family:sans-serif;max-width:520px;margin:40px auto;color:#1e293b">
  <h2 style="color:#1e3a5f">Sign in to AdAgent</h2>
  <p>Click the button below to sign in. This link expires in
     {settings.MAGIC_LINK_TTL_MINUTES} minutes.</p>
  <p style="margin:28px 0">
    <a href="{url}"
       style="background:#1e3a5f;color:#fff;padding:12px 24px;border-radius:6px;
              text-decoration:none;font-weight:700">
      Sign in to AdAgent
    </a>
  </p>
  <p style="color:#94a3b8;font-size:13px">
    Or copy this link: <a href="{url}">{url}</a>
  </p>
  <p style="color:#94a3b8;font-size:13px">
    If you did not request this, you can safely ignore this email.
  </p>
</body></html>"""

    return await asyncio.to_thread(_send, email, subject, body_text, body_html)


async def send_report_ready(email: str, report_id: str, status: str) -> bool:
    """
    Notify a signed-in user that their report finished (or failed).
    Opt-in only — sent when Report.notify_email is set.
    """
    url = f"{settings.APP_BASE_URL}/report/{report_id}"

    if status == "failed":
        subject = "Your AdAgent report failed"
        heading = "Your report couldn't be completed"
        intro   = "Unfortunately something went wrong while analyzing your ads. You can open the report page for details, or start a new search."
        btn_bg  = "#dc2626"
        btn_txt = "View Details"
    else:
        subject = "Your AdAgent report is ready"
        heading = "Your report is ready! &#127881;"
        intro   = "The analysis you started has finished. Click below to view the full report."
        btn_bg  = "#16a34a"
        btn_txt = "View My Report"

    body_text = f"{intro}\n\n{url}"
    body_html = f"""
<html><body style="font-family:sans-serif;max-width:520px;margin:40px auto;color:#1e293b">
  <h2 style="color:#1e3a5f">{heading}</h2>
  <p>{intro}</p>
  <p style="margin:28px 0">
    <a href="{url}"
       style="background:{btn_bg};color:#fff;padding:12px 24px;border-radius:6px;
              text-decoration:none;font-weight:700">
      {btn_txt}
    </a>
  </p>
  <p style="color:#94a3b8;font-size:13px">
    Or copy this link: <a href="{url}">{url}</a>
  </p>
</body></html>"""

    return await asyncio.to_thread(_send, email, subject, body_text, body_html)


async def send_subscription_welcome(email: str, token: str) -> bool:
    url = f"{settings.APP_BASE_URL}/auth/verify/{token}"
    subject = "Welcome to AdAgent Pro!"
    body_text = (
        "Thank you for subscribing to AdAgent Pro!\n\n"
        "Click the link below to sign in and access your report history:\n\n"
        f"{url}\n\n(Link expires in {settings.MAGIC_LINK_TTL_MINUTES} minutes.)"
    )
    body_html = f"""
<html><body style="font-family:sans-serif;max-width:520px;margin:40px auto;color:#1e293b">
  <h2 style="color:#1e3a5f">&#127881; Welcome to AdAgent Pro!</h2>
  <p>Your subscription is now active. You can now analyze all ads in any OLX search.</p>
  <p style="margin:28px 0">
    <a href="{url}"
       style="background:#16a34a;color:#fff;padding:12px 24px;border-radius:6px;
              text-decoration:none;font-weight:700">
      Access My Account
    </a>
  </p>
  <p style="color:#94a3b8;font-size:13px">
    Link expires in {settings.MAGIC_LINK_TTL_MINUTES} minutes.
  </p>
</body></html>"""

    return await asyncio.to_thread(_send, email, subject, body_text, body_html)
