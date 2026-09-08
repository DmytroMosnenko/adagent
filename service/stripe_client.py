from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Optional
import stripe
from .config import settings
from .logger import get_logger

logger = get_logger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY
_client = stripe.StripeClient(api_key=settings.STRIPE_SECRET_KEY)


async def create_checkout_session(
    plan: str,
    customer_email: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> str:
    """Create a Stripe Checkout Session and return the redirect URL."""
    prices = settings.stripe_prices
    price_id = prices.get(plan)
    if not price_id:
        raise ValueError(f"No Stripe price configured for plan '{plan}'")

    params: dict = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": settings.STRIPE_SUCCESS_URL + "?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": settings.STRIPE_CANCEL_URL,
        "metadata": metadata or {},
    }
    if customer_email:
        params["customer_email"] = customer_email

    session = await asyncio.to_thread(
        _client.checkout.sessions.create, params
    )
    return session.url


def parse_webhook(payload: bytes, sig_header: str) -> stripe.Event:
    """
    Verify Stripe webhook signature and return the parsed Event.

    Raises stripe.error.SignatureVerificationError if the signature is invalid.
    Must receive the raw request bytes — do NOT parse the body before calling this.
    """
    return stripe.Webhook.construct_event(
        payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
    )


def get_session_dict(event: stripe.Event) -> dict:
    """
    Extract the checkout.Session from a webhook event as a plain dict.

    stripe.Session is not a dict — calling .get() on it raises AttributeError.
    Use this helper in webhook handlers instead of accessing session fields directly,
    so standard dict access (.get, subscript) works as expected.
    """
    return event["data"]["object"].to_dict()


def period_end_to_datetime(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def detect_plan_period(price_id: str) -> str:
    """Map price_id back to plan period name for display."""
    for period, pid in settings.stripe_prices.items():
        if pid == price_id:
            return period
    return "monthly"
