from __future__ import annotations
import asyncio
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from .config import settings
from .logger import get_logger

logger = get_logger(__name__)

_ses = None


def _get_ses():
    global _ses
    if _ses is None:
        _ses = boto3.client(
            "ses",
            region_name=settings.AWS_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )
    return _ses


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
  <h2 style="color:#1e3a5f">🎉 Welcome to AdAgent Pro!</h2>
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


def _send(to: str, subject: str, body_text: str, body_html: str) -> bool:
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
