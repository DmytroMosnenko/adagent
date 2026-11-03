"""
Time-boxed access passes ("48 h pass") — pure helpers, no DB / Stripe / settings imports
so they are trivial to unit-test.

A pass is a ONE-TIME Stripe Checkout payment (mode="payment"). It never renews.
While one is active the user gets the same full access as a Pro subscriber.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

# Key used in STRIPE_PRICES_JSON, in the checkout `plan` form field and in session metadata.
PASS_PLAN = "pass"

# Sanity bounds for the duration read back from Stripe metadata (1 h … 30 days).
MIN_PASS_HOURS = 1
MAX_PASS_HOURS = 24 * 30


def utcnow() -> datetime:
    """Naive UTC — the convention used by every DateTime column in this app."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def clamp_hours(value, default: int) -> int:
    """
    Duration from Stripe metadata. Metadata is set by us when the session is created,
    but a bad value must never produce a zero-length or multi-year pass.
    """
    try:
        hours = int(value)
    except (TypeError, ValueError):
        return default
    if hours < MIN_PASS_HOURS or hours > MAX_PASS_HOURS:
        return default
    return hours


def compute_expiry(now: datetime, latest_expiry: Optional[datetime], hours: int) -> datetime:
    """
    When a pass bought `now` ends. If the buyer still has time left on an earlier pass the
    new one is added after it (passes stack), otherwise it starts now.
    """
    start = latest_expiry if latest_expiry and latest_expiry > now else now
    return start + timedelta(hours=hours)


def format_expiry(expires_at: Optional[datetime], tz_name: str = "Europe/Warsaw") -> Optional[str]:
    """Naive-UTC datetime → 'dd.mm HH:MM' in the site's timezone (for the UI and e-mails)."""
    if not expires_at:
        return None
    try:
        zone = ZoneInfo(tz_name)
    except Exception:  # unknown tz name / missing tzdata → fall back to UTC rather than crash a page
        zone = timezone.utc
    return expires_at.replace(tzinfo=timezone.utc).astimezone(zone).strftime("%d.%m %H:%M")
