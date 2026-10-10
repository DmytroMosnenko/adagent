from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Optional
import stripe
from .config import settings
from .i18n import translate
from .logger import get_logger

logger = get_logger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY
_client = stripe.StripeClient(api_key=settings.STRIPE_SECRET_KEY)


async def create_checkout_session(
    plan: str,
    customer_email: Optional[str] = None,
    metadata: Optional[dict] = None,
    lang: str = "en",
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

    # Checkout page in the visitor's language. The webhook reads session.locale back
    # to pick the language of the welcome e-mail.
    params["currency"] = settings.STRIPE_CHECKOUT_CURRENCY_USD.lower()
    if lang == "pl":
        params["locale"] = "pl"
        # Only valid when the Stripe price has a matching entry in currency_options,
        # otherwise Stripe rejects the session — hence opt-in via env.
        if settings.STRIPE_CHECKOUT_CURRENCY_PL:
            params["currency"] = settings.STRIPE_CHECKOUT_CURRENCY_PL.lower()

    # Consumer-law text next to the pay button (Polish/EU digital-service withdrawal waiver).
    consent_text = translate(
        "By subscribing you accept the [Terms of Service]({terms}) and the [Privacy Policy]({privacy}), "
        "and you ask for the digital service to start immediately, acknowledging that you lose your "
        "right of withdrawal once it has begun.",
        lang,
        terms=f"{settings.APP_BASE_URL}/terms",
        privacy=f"{settings.APP_BASE_URL}/privacy",
    )

    # TODO(amid): error on custom_text:
    # error_message = 'custom_text cannot be used with Managed Payments, which is enabled by default on your account.
    # Remove custom_text, or pass managed_payments[enabled]=false to disable it for this request
    # if settings.STRIPE_REQUIRE_TOS_CONSENT:
    #     params["consent_collection"] = {"terms_of_service": "required"}
    #     params["custom_text"] = {"terms_of_service_acceptance": {"message": consent_text}}
    # else:
    #     params["custom_text"] = {"submit": {"message": consent_text}}

    if settings.STRIPE_COLLECT_TAX_ID:
        params["tax_id_collection"] = {"enabled": True}

    session = await asyncio.to_thread(
        _client.checkout.sessions.create, params
    )
    return session.url


async def get_checkout_summary(session_id: Optional[str]) -> Optional[dict]:
    """
    Verified purchase data for the conversion tag on /subscribe/success.

    Returns {"transaction_id", "value", "currency"} only when Stripe confirms the
    session was actually paid — so opening /subscribe/success?session_id=anything
    cannot fire a fake purchase conversion. Returns None otherwise (tag is skipped).
    """
    if not session_id or not session_id.startswith("cs_") or len(session_id) > 255:
        return None
    try:
        session = await asyncio.to_thread(_client.checkout.sessions.retrieve, session_id)
    except stripe.StripeError as exc:
        logger.warning("[stripe] checkout session lookup failed (%s): %s", session_id, exc)
        return None
    data = session.to_dict()
    if data.get("payment_status") not in ("paid", "no_payment_required"):
        return None
    amount, currency = data.get("amount_total"), data.get("currency")
    if amount is None or not currency:
        return None
    return {
        "transaction_id": data.get("id"),
        "value": round(amount / 100, 2),
        "currency": currency.upper(),
    }


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


def extract_current_period_end(sub: dict) -> Optional[int]:
    """
    Return the current period end (unix timestamp) from a Subscription dict,
    handling both the old and new Stripe API shapes.

    As of API version 2026-07-29 ("dahlia"), Stripe removed
    `current_period_end` / `current_invoice_period` from the top-level
    Subscription object. The field now lives per subscription item, at
    `items.data[N].current_period_end`. This checks, in order:
      1. `current_invoice_period.end`      (older transitional shape)
      2. top-level `current_period_end`    (pre-dahlia shape)
      3. `items.data[0].current_period_end` (dahlia+ shape)
    """
    current_invoice_period = sub.get("current_invoice_period")
    if isinstance(current_invoice_period, dict) and current_invoice_period.get("end"):
        return current_invoice_period["end"]

    if sub.get("current_period_end"):
        return sub["current_period_end"]

    items = sub.get("items")
    items_data = items.get("data") if isinstance(items, dict) else None
    if items_data:
        first_item = items_data[0]
        if isinstance(first_item, dict) and first_item.get("current_period_end"):
            return first_item["current_period_end"]

    return None


def detect_plan_period(price_id: str) -> str:
    """Map price_id back to plan period name for display."""
    for period, pid in settings.stripe_prices.items():
        if pid == price_id:
            return period
    return "monthly"
