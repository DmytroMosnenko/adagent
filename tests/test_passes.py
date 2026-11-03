"""
Tests for the one-time pass (service/crud.py pass functions, Stripe checkout/webhook
handling and the pass-aware pages in adagent_server.py). In-memory SQLite, Stripe and
e-mail are mocked — no network.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from service import crud, passes, stripe_client
from service.config import settings
from service.models import AccessPass

PRICES_WITH_PASS = '{"monthly": "price_monthly_fake", "pass": "price_pass_fake"}'


def _txt(r) -> str:
    """Response text with non-breaking spaces ("48\u00a0h") normalised to plain spaces."""
    return r.text.replace("\u00a0", " ")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _user_with_session(db, email="buyer@example.com"):
    user, _ = await crud.get_or_create_user(db, email)
    token = f"session_{uuid.uuid4().hex}"
    await crud.create_session(db, token, user.id, ttl_days=7)
    return user, token


@pytest.fixture()
def pass_enabled(monkeypatch):
    """Stripe 'pass' price configured → the pass is offered in the UI."""
    monkeypatch.setattr(settings, "STRIPE_PRICES_JSON", PRICES_WITH_PASS)


class _Obj:
    """Stands in for a stripe object: the server only calls .to_dict() on event objects."""
    def __init__(self, d):
        self._d = d

    def to_dict(self):
        return self._d


def _event(etype, obj):
    return {"type": etype, "data": {"object": _Obj(obj)}}


def _pass_session(email="buyer@example.com", sid="cs_test_pass_1", paid=True, hours="48", pi="pi_test_1"):
    return {
        "id": sid, "mode": "payment", "payment_status": "paid" if paid else "unpaid",
        "customer_details": {"email": email}, "customer": None, "payment_intent": pi,
        "amount_total": 999, "currency": "pln", "locale": "en",
        "metadata": {"plan": "pass", "pass_hours": hours},
    }


async def _post_webhook(client, event):
    with patch("adagent_server.stripe_client.parse_webhook", return_value=event):
        return await client.post("/webhooks/stripe", content=b"{}",
                                 headers={"stripe-signature": "t=1,v1=fake"})


# ── crud ─────────────────────────────────────────────────────────────────────

class TestPassCrud:

    async def test_create_pass_grants_access_for_the_paid_hours(self, db):
        user, _ = await crud.get_or_create_user(db, "p1@example.com")
        assert await crud.get_pass_until(db, user.id) is None

        row, created = await crud.create_pass(db, user.id, "cs_1", hours=48, amount=999, currency="PLN")

        assert created is True
        assert row.currency == "pln" and row.amount == 999 and row.status == "active"
        until = await crud.get_pass_until(db, user.id)
        assert until is not None
        assert abs((until - (_now() + timedelta(hours=48))).total_seconds()) < 5

    async def test_create_pass_is_idempotent_per_checkout_session(self, db):
        user, _ = await crud.get_or_create_user(db, "p2@example.com")
        first, created1 = await crud.create_pass(db, user.id, "cs_dup", hours=48)
        second, created2 = await crud.create_pass(db, user.id, "cs_dup", hours=48)

        assert (created1, created2) == (True, False)
        assert first.id == second.id
        # a webhook retry must not extend the pass
        assert await crud.get_pass_until(db, user.id) == first.expires_at

    async def test_second_pass_stacks_after_the_first(self, db):
        user, _ = await crud.get_or_create_user(db, "p3@example.com")
        first, _ = await crud.create_pass(db, user.id, "cs_a", hours=48)
        second, _ = await crud.create_pass(db, user.id, "cs_b", hours=48)

        assert second.expires_at == first.expires_at + timedelta(hours=48)
        assert await crud.get_pass_until(db, user.id) == second.expires_at

    async def test_expired_pass_gives_no_access(self, db):
        user, _ = await crud.get_or_create_user(db, "p4@example.com")
        db.add(AccessPass(user_id=user.id, stripe_session_id="cs_old", hours=48, status="active",
                          expires_at=_now() - timedelta(minutes=1)))
        await db.commit()
        assert await crud.get_pass_until(db, user.id) is None

    async def test_pass_belongs_to_one_user_only(self, db):
        a, _ = await crud.get_or_create_user(db, "a@example.com")
        b, _ = await crud.get_or_create_user(db, "b@example.com")
        await crud.create_pass(db, a.id, "cs_only_a", hours=48)
        assert await crud.get_pass_until(db, b.id) is None

    async def test_revoke_by_payment_intent_ends_only_that_pass(self, db):
        user, _ = await crud.get_or_create_user(db, "p5@example.com")
        other, _ = await crud.get_or_create_user(db, "p6@example.com")
        await crud.create_pass(db, user.id, "cs_r1", hours=48, stripe_payment_intent="pi_refund")
        await crud.create_pass(db, other.id, "cs_r2", hours=48, stripe_payment_intent="pi_keep")

        assert await crud.revoke_pass_by_payment_intent(db, "pi_refund") is True
        assert await crud.get_pass_until(db, user.id) is None
        assert await crud.get_pass_until(db, other.id) is not None
        assert await crud.revoke_pass_by_payment_intent(db, "pi_unknown") is False

    async def test_get_or_create_user_survives_a_duplicate_insert(self, db):
        # the unique-email race is covered by the IntegrityError fallback; the
        # plain "already exists" path must keep returning the same row
        u1, c1 = await crud.get_or_create_user(db, "race@example.com")
        u2, c2 = await crud.get_or_create_user(db, "race@example.com")
        assert (c1, c2) == (True, False) and u1.id == u2.id


# ── stripe_client ────────────────────────────────────────────────────────────

class TestStripeClientPass:

    def test_is_pass_session(self):
        assert stripe_client.is_pass_session(_pass_session()) is True
        sub = {"mode": "subscription", "metadata": {}}
        assert stripe_client.is_pass_session(sub) is False
        # metadata alone is not enough — it must really be a one-time payment
        assert stripe_client.is_pass_session({"mode": "subscription", "metadata": {"plan": "pass"}}) is False
        assert stripe_client.is_pass_session({"mode": "payment", "metadata": None}) is False

    def test_pass_hours_from_session_uses_metadata_then_config(self):
        assert stripe_client.pass_hours_from_session(_pass_session(hours="72")) == 72
        assert stripe_client.pass_hours_from_session(_pass_session(hours="nonsense")) == settings.PASS_HOURS
        assert stripe_client.pass_hours_from_session({"metadata": None}) == settings.PASS_HOURS

    def test_summary_requires_a_paid_session(self):
        assert stripe_client.summary_from_session(_pass_session(paid=False)) is None
        assert stripe_client.summary_from_session(None) is None
        s = stripe_client.summary_from_session(_pass_session())
        assert s == {"transaction_id": "cs_test_pass_1", "value": 9.99, "currency": "PLN"}

    async def test_checkout_for_pass_is_a_one_time_payment(self, pass_enabled):
        with patch("service.stripe_client._client") as mc:
            mc.checkout.sessions.create.return_value = MagicMock(url="https://checkout.stripe.test/p")
            url = await stripe_client.create_checkout_session("pass", "buyer@example.com", lang="pl")

        assert url == "https://checkout.stripe.test/p"
        params = mc.checkout.sessions.create.call_args.args[0]
        assert params["mode"] == "payment"
        assert params["line_items"] == [{"price": "price_pass_fake", "quantity": 1}]
        assert params["metadata"]["plan"] == "pass"
        assert params["metadata"]["pass_hours"] == str(settings.PASS_HOURS)
        assert params["payment_intent_data"]["metadata"] == {"plan": "pass"}
        assert params["customer_email"] == "buyer@example.com"
        assert params["locale"] == "pl"

    async def test_checkout_for_monthly_is_still_a_subscription(self, pass_enabled):
        with patch("service.stripe_client._client") as mc:
            mc.checkout.sessions.create.return_value = MagicMock(url="https://checkout.stripe.test/m")
            await stripe_client.create_checkout_session("monthly")
        params = mc.checkout.sessions.create.call_args.args[0]
        assert params["mode"] == "subscription"
        assert "payment_intent_data" not in params
        assert "plan" not in params["metadata"]

    async def test_checkout_for_pass_without_a_price_is_rejected(self):
        # default test env has only a monthly price
        with pytest.raises(ValueError):
            await stripe_client.create_checkout_session("pass")


# ── pages ────────────────────────────────────────────────────────────────────

async def test_pricing_page_hides_the_pass_until_a_price_is_configured(client):
    r = await client.get("/subscribe")
    assert r.status_code == 200
    assert 'value="pass"' not in _txt(r)
    assert "9.99" in _txt(r)


async def test_pricing_page_shows_the_pass_card(client, pass_enabled):
    r = await client.get("/subscribe")
    assert r.status_code == 200
    assert 'value="pass"' in _txt(r)
    assert "$2.99" in _txt(r) and "48 h" in _txt(r)
    assert "9.99" in _txt(r)                      # Pro is still there


async def test_pricing_page_polish_shows_zloty_price(client, pass_enabled):
    r = await client.get("/subscribe?lang=pl")
    assert "9,99 zł" in _txt(r)
    assert "Kup dostęp na 48 h" in _txt(r)


async def test_landing_page_offers_the_pass(client, pass_enabled):
    r = await client.get("/")
    assert r.status_code == 200
    assert "Unlock all ads for 48 h" in _txt(r)
    assert "Do I need a subscription?" in _txt(r)


async def test_landing_page_unchanged_without_a_pass_price(client):
    r = await client.get("/")
    assert "Upgrade to Pro" in _txt(r)
    assert "Do I need a subscription?" not in _txt(r)


async def test_checkout_route_redirects_to_stripe_for_pass(client, pass_enabled):
    with patch("service.stripe_client._client") as mc:
        mc.checkout.sessions.create.return_value = MagicMock(url="https://checkout.stripe.test/go")
        r = await client.post("/subscribe/checkout", data={"plan": "pass"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "https://checkout.stripe.test/go"


async def test_checkout_route_rejects_pass_when_not_configured(client):
    r = await client.post("/subscribe/checkout", data={"plan": "pass"}, follow_redirects=False)
    assert r.status_code == 400


async def test_active_pass_shows_in_ui(client, db, pass_enabled):
    user, token = await _user_with_session(db, "ui@example.com")
    await crud.create_pass(db, user.id, "cs_ui", hours=48)

    home = await client.get("/", cookies={"adagent_session": token})
    assert "Pass active" in _txt(home)

    pricing = await client.get("/subscribe", cookies={"adagent_session": token})
    assert "Your pass is active until" in _txt(pricing)
    assert "Add 48 h more" in _txt(pricing)


# ── analyze: pass == full access ─────────────────────────────────────────────

async def _analyze(client, token):
    return await client.post(
        "/analyze",
        data={"filter_url": "https://www.olx.pl/motoryzacja/", "prompt_preset": "vehicles"},
        cookies={"adagent_session": token}, follow_redirects=False)


async def test_analyze_with_active_pass_is_not_limited(client, db):
    user, token = await _user_with_session(db, "full@example.com")
    await crud.create_pass(db, user.id, "cs_full", hours=48)

    r = await _analyze(client, token)
    report = await crud.get_report(db, r.headers["location"].split("/report/")[1])
    assert report.is_limited is False


async def test_analyze_after_the_pass_expired_is_limited_again(client, db):
    user, token = await _user_with_session(db, "expired@example.com")
    db.add(AccessPass(user_id=user.id, stripe_session_id="cs_exp", hours=48, status="active",
                      expires_at=_now() - timedelta(minutes=1)))
    await db.commit()

    r = await _analyze(client, token)
    report = await crud.get_report(db, r.headers["location"].split("/report/")[1])
    assert report.is_limited is True


async def test_analyze_after_refund_is_limited_again(client, db):
    user, token = await _user_with_session(db, "refunded@example.com")
    await crud.create_pass(db, user.id, "cs_ref", hours=48, stripe_payment_intent="pi_ref")
    await crud.revoke_pass_by_payment_intent(db, "pi_ref")

    r = await _analyze(client, token)
    report = await crud.get_report(db, r.headers["location"].split("/report/")[1])
    assert report.is_limited is True


# ── webhook ──────────────────────────────────────────────────────────────────

async def test_webhook_completed_pass_grants_access_and_sends_one_email(client, db):
    mail = AsyncMock(return_value=True)
    with patch("adagent_server.email_client.send_pass_welcome", new=mail):
        r = await _post_webhook(client, _event("checkout.session.completed", _pass_session()))
        assert r.status_code == 200
        # Stripe retries / the success page races the webhook → must not grant or mail twice
        await _post_webhook(client, _event("checkout.session.completed", _pass_session()))

    user = await crud.get_user_by_email(db, "buyer@example.com")
    assert user is not None
    assert await crud.get_pass_until(db, user.id) is not None
    assert mail.await_count == 1
    assert mail.await_args.kwargs["hours"] == 48


async def test_webhook_unpaid_session_waits_for_async_payment(client, db):
    mail = AsyncMock(return_value=True)
    with patch("adagent_server.email_client.send_pass_welcome", new=mail):
        await _post_webhook(client, _event("checkout.session.completed",
                                           _pass_session(email="late@example.com", paid=False)))
        user = await crud.get_user_by_email(db, "late@example.com")
        assert user is None or await crud.get_pass_until(db, user.id) is None
        assert mail.await_count == 0

        await _post_webhook(client, _event("checkout.session.async_payment_succeeded",
                                           _pass_session(email="late@example.com", paid=True)))

    user = await crud.get_user_by_email(db, "late@example.com")
    assert await crud.get_pass_until(db, user.id) is not None
    assert mail.await_count == 1


async def test_webhook_honours_the_duration_fixed_at_purchase(client, db):
    with patch("adagent_server.email_client.send_pass_welcome", new=AsyncMock(return_value=True)):
        await _post_webhook(client, _event("checkout.session.completed",
                                           _pass_session(email="long@example.com", hours="72")))
    user = await crud.get_user_by_email(db, "long@example.com")
    until = await crud.get_pass_until(db, user.id)
    assert abs((until - (_now() + timedelta(hours=72))).total_seconds()) < 5


async def test_webhook_subscription_checkout_still_uses_the_subscription_flow(client):
    sub_session = {"id": "cs_sub_1", "mode": "subscription", "payment_status": "paid",
                   "metadata": {}, "subscription": "sub_123",
                   "customer_details": {"email": "pro@example.com"}}
    handler = AsyncMock()
    with patch("adagent_server._handle_checkout_completed", new=handler):
        r = await _post_webhook(client, _event("checkout.session.completed", sub_session))
    assert r.status_code == 200
    handler.assert_awaited_once()


async def test_webhook_refund_revokes_the_pass(client, db):
    user, _ = await crud.get_or_create_user(db, "refund@example.com")
    await crud.create_pass(db, user.id, "cs_wr", hours=48, stripe_payment_intent="pi_wr")
    assert await crud.get_pass_until(db, user.id) is not None

    r = await _post_webhook(client, _event("charge.refunded", {"id": "ch_1", "payment_intent": "pi_wr"}))
    assert r.status_code == 200
    assert await crud.get_pass_until(db, user.id) is None


async def test_webhook_refund_of_unrelated_payment_is_ignored(client):
    r = await _post_webhook(client, _event("charge.refunded", {"id": "ch_2", "payment_intent": "pi_nope"}))
    assert r.status_code == 200


# ── success page ─────────────────────────────────────────────────────────────

async def test_success_page_fulfils_the_pass_for_a_signed_in_buyer(client, db):
    user, token = await _user_with_session(db, "buyer@example.com")
    with patch("adagent_server.stripe_client.get_checkout_session",
               new=AsyncMock(return_value=_pass_session())), \
         patch("adagent_server.email_client.send_pass_welcome", new=AsyncMock(return_value=True)):
        r = await client.get("/subscribe/success?session_id=cs_test_pass_1",
                             cookies={"adagent_session": token})

    assert r.status_code == 200
    assert "Your 48 h pass is active!" in _txt(r)
    assert "Full access until" in _txt(r)
    assert await crud.get_pass_until(db, user.id) is not None


async def test_success_page_for_an_anonymous_buyer_asks_to_check_email(client, db):
    with patch("adagent_server.stripe_client.get_checkout_session",
               new=AsyncMock(return_value=_pass_session(email="anon@example.com"))), \
         patch("adagent_server.email_client.send_pass_welcome", new=AsyncMock(return_value=True)):
        r = await client.get("/subscribe/success?session_id=cs_test_pass_1")

    assert r.status_code == 200
    assert "sign-in link" in _txt(r)
    user = await crud.get_user_by_email(db, "anon@example.com")
    assert await crud.get_pass_until(db, user.id) is not None     # paid → pass exists, usable after sign-in


async def test_success_page_never_grants_for_an_unpaid_or_unknown_session(client, db):
    with patch("adagent_server.stripe_client.get_checkout_session",
               new=AsyncMock(return_value=_pass_session(email="nopay@example.com", paid=False))):
        r = await client.get("/subscribe/success?session_id=cs_test_pass_1")
    assert r.status_code == 200
    assert "Waiting for payment confirmation" in _txt(r)        # not "pass is active"
    assert "pass is active" not in _txt(r)
    assert await crud.get_user_by_email(db, "nopay@example.com") is None

    with patch("adagent_server.stripe_client.get_checkout_session", new=AsyncMock(return_value=None)):
        r = await client.get("/subscribe/success?session_id=cs_forged")
    assert r.status_code == 200
    assert "AdAgent Pro" in _txt(r)                                 # falls back to the subscription page


async def test_success_page_survives_a_fulfilment_error(client):
    with patch("adagent_server.stripe_client.get_checkout_session",
               new=AsyncMock(return_value=_pass_session())), \
         patch("adagent_server._fulfill_pass", new=AsyncMock(side_effect=RuntimeError("db down"))):
        r = await client.get("/subscribe/success?session_id=cs_test_pass_1")
    assert r.status_code == 200


# ── plan comparison: limits come from config, not from copy ──────────────────

async def test_plan_matrix_takes_limits_from_config(client, pass_enabled, monkeypatch):
    monkeypatch.setattr(settings, "FREE_ADS_LIMIT", 7)
    monkeypatch.setattr(settings, "FREE_REPORTS_PER_CLIENT_PER_DAY", 3)
    monkeypatch.setattr(settings, "PASS_HOURS", 72)
    for url in ("/subscribe", "/"):
        text = _txt(await client.get(url))
        assert "First 7 ads" in text
        assert "3 / day" in text
        assert "72 hours" in text
        assert "Until you cancel" in text


async def test_plan_matrix_daily_limit_disabled_reads_unlimited(client, pass_enabled, monkeypatch):
    monkeypatch.setattr(settings, "FREE_REPORTS_PER_CLIENT_PER_DAY", 0)   # 0 = no limit
    text = _txt(await client.get("/subscribe"))
    assert "/ day" not in text


# ── checkout entry points ────────────────────────────────────────────────────

async def test_landing_cards_post_straight_to_checkout(client, pass_enabled):
    html = (await client.get("/")).text
    assert html.count('action="/subscribe/checkout"') >= 2          # pass + Pro, no hop via /subscribe
    assert 'name="plan" value="pass"' in html and 'name="plan" value="monthly"' in html


async def test_checkout_stripe_error_is_a_502_not_a_crash(client, pass_enabled):
    import stripe
    with patch("service.stripe_client._client") as mc:
        mc.checkout.sessions.create.side_effect = stripe.StripeError("product tax code is missing")
        r = await client.post("/subscribe/checkout", data={"plan": "pass"}, follow_redirects=False)
    assert r.status_code == 502
