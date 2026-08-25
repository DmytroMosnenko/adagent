"""Tests for service/crud.py — all DB operations against in-memory SQLite."""
import uuid
from datetime import datetime, timedelta, timezone
import pytest
import pytest_asyncio
from service import crud
from service.models import Subscription


class TestUsers:

    async def test_get_or_create_creates_new(self, db):
        user, created = await crud.get_or_create_user(db, "alice@example.com")
        assert created is True
        assert user.id is not None
        assert user.email == "alice@example.com"

    async def test_get_or_create_returns_existing(self, db):
        user1, _ = await crud.get_or_create_user(db, "bob@example.com")
        user2, created = await crud.get_or_create_user(db, "bob@example.com")
        assert created is False
        assert user1.id == user2.id

    async def test_get_user_by_email(self, db):
        await crud.get_or_create_user(db, "carol@example.com")
        user = await crud.get_user_by_email(db, "carol@example.com")
        assert user is not None
        assert user.email == "carol@example.com"

    async def test_get_user_by_email_missing(self, db):
        result = await crud.get_user_by_email(db, "nobody@example.com")
        assert result is None

    async def test_get_user_by_id(self, db):
        user, _ = await crud.get_or_create_user(db, "dave@example.com")
        fetched = await crud.get_user_by_id(db, user.id)
        assert fetched is not None
        assert fetched.email == "dave@example.com"

    async def test_set_stripe_customer(self, db):
        user, _ = await crud.get_or_create_user(db, "eve@example.com")
        await crud.set_stripe_customer(db, user.id, "cus_fake123")
        refreshed = await crud.get_user_by_id(db, user.id)
        assert refreshed.stripe_customer_id == "cus_fake123"


class TestSessions:

    async def test_create_and_get_session(self, db):
        user, _ = await crud.get_or_create_user(db, "sess@example.com")
        token = "test_token_abc"
        await crud.create_session(db, token, user.id, ttl_days=7)
        session = await crud.get_session(db, token)
        assert session is not None
        assert session.user_id == user.id

    async def test_get_session_expired_returns_none(self, db):
        user, _ = await crud.get_or_create_user(db, "expired@example.com")
        token = "expired_token"
        # Insert session that expired in the past by patching TTL
        from service.models import Session
        s = Session(token=token, user_id=user.id,
                    expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1))
        db.add(s)
        await db.commit()
        result = await crud.get_session(db, token)
        assert result is None

    async def test_delete_session(self, db):
        user, _ = await crud.get_or_create_user(db, "del@example.com")
        token = "to_delete"
        await crud.create_session(db, token, user.id, ttl_days=1)
        await crud.delete_session(db, token)
        assert await crud.get_session(db, token) is None


class TestMagicLinks:

    async def test_create_and_consume(self, db):
        token = "ml_valid_token"
        await crud.create_magic_link(db, token, "magic@example.com", ttl_minutes=15)
        email = await crud.consume_magic_link(db, token)
        assert email == "magic@example.com"

    async def test_consume_marks_used(self, db):
        token = "ml_once_only"
        await crud.create_magic_link(db, token, "once@example.com", ttl_minutes=15)
        await crud.consume_magic_link(db, token)
        # Second consume returns None
        result = await crud.consume_magic_link(db, token)
        assert result is None

    async def test_consume_expired_returns_none(self, db):
        from service.models import MagicLink
        token = "ml_expired"
        ml = MagicLink(token=token, email="exp@example.com",
                       expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1), used=False)
        db.add(ml)
        await db.commit()
        result = await crud.consume_magic_link(db, token)
        assert result is None

    async def test_consume_nonexistent_returns_none(self, db):
        result = await crud.consume_magic_link(db, "ghost_token")
        assert result is None


class TestSubscriptions:

    async def _make_user(self, db, email):
        user, _ = await crud.get_or_create_user(db, email)
        return user

    async def test_upsert_creates_subscription(self, db):
        user = await self._make_user(db, "sub1@example.com")
        sub = await crud.upsert_subscription(
            db, user_id=user.id,
            stripe_customer_id="cus_1",
            stripe_subscription_id="sub_1",
            stripe_price_id="price_monthly",
            plan_period="monthly",
            status="active",
            current_period_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        )
        assert sub.status == "active"
        assert sub.user_id == user.id

    async def test_is_subscribed_active(self, db):
        user = await self._make_user(db, "sub2@example.com")
        await crud.upsert_subscription(
            db, user_id=user.id, stripe_customer_id="cus_2",
            stripe_subscription_id="sub_2", stripe_price_id="price_monthly",
            plan_period="monthly", status="active",
            current_period_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        )
        assert await crud.is_subscribed(db, user.id) is True

    async def test_is_subscribed_expired_period(self, db):
        user = await self._make_user(db, "sub3@example.com")
        await crud.upsert_subscription(
            db, user_id=user.id, stripe_customer_id="cus_3",
            stripe_subscription_id="sub_3", stripe_price_id="price_monthly",
            plan_period="monthly", status="active",
            current_period_end=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),  # expired
        )
        assert await crud.is_subscribed(db, user.id) is False

    async def test_is_subscribed_cancelled(self, db):
        user = await self._make_user(db, "sub4@example.com")
        await crud.upsert_subscription(
            db, user_id=user.id, stripe_customer_id="cus_4",
            stripe_subscription_id="sub_4", stripe_price_id="price_monthly",
            plan_period="monthly", status="canceled",
            current_period_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        )
        assert await crud.is_subscribed(db, user.id) is False

    async def test_is_subscribed_no_subscription(self, db):
        user = await self._make_user(db, "nosub@example.com")
        assert await crud.is_subscribed(db, user.id) is False

    async def test_update_subscription_status(self, db):
        user = await self._make_user(db, "sub5@example.com")
        await crud.upsert_subscription(
            db, user_id=user.id, stripe_customer_id="cus_5",
            stripe_subscription_id="sub_5", stripe_price_id="price_monthly",
            plan_period="monthly", status="active",
            current_period_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        )
        await crud.update_subscription_status(db, "sub_5", "past_due")
        sub = await crud.get_subscription(db, user.id)
        assert sub.status == "past_due"


class TestReports:

    async def test_create_and_get_report(self, db):
        report_id = uuid.uuid4().hex
        await crud.create_report(
            db, id=report_id,
            filter_url="https://www.olx.pl/test/",
            prompt_preset="vehicles",
            is_limited=True, status="pending",
        )
        r = await crud.get_report(db, report_id)
        assert r is not None
        assert r.filter_url == "https://www.olx.pl/test/"
        assert r.is_limited is True
        assert r.status == "pending"

    async def test_get_report_missing_returns_none(self, db):
        assert await crud.get_report(db, "nonexistent") is None

    async def test_update_report_status(self, db):
        report_id = uuid.uuid4().hex
        await crud.create_report(db, id=report_id,
                                 filter_url="https://olx.pl/x",
                                 is_limited=True, status="pending")
        await crud.update_report(db, report_id, status="running")
        r = await crud.get_report(db, report_id)
        assert r.status == "running"

    async def test_update_report_ads_found(self, db):
        report_id = uuid.uuid4().hex
        await crud.create_report(db, id=report_id,
                                 filter_url="https://olx.pl/x",
                                 is_limited=True, status="pending")
        await crud.update_report(db, report_id, ads_found=47, ads_analyzed=5)
        r = await crud.get_report(db, report_id)
        assert r.ads_found == 47
        assert r.ads_analyzed == 5

    async def test_fail_stale_reports(self, db):
        r1 = uuid.uuid4().hex
        r2 = uuid.uuid4().hex
        await crud.create_report(db, id=r1, filter_url="https://olx.pl/a",
                                 is_limited=True, status="running")
        await crud.create_report(db, id=r2, filter_url="https://olx.pl/b",
                                 is_limited=True, status="done")
        await crud.fail_stale_reports(db)
        assert (await crud.get_report(db, r1)).status == "failed"
        assert (await crud.get_report(db, r2)).status == "done"  # not touched

    async def test_get_user_reports(self, db):
        user, _ = await crud.get_or_create_user(db, "hist@example.com")
        for i in range(3):
            await crud.create_report(
                db, id=uuid.uuid4().hex,
                user_id=user.id, filter_url=f"https://olx.pl/{i}",
                is_limited=False, status="done",
            )
        reports = await crud.get_user_reports(db, user.id)
        assert len(reports) == 3

    async def test_get_user_reports_excludes_non_done(self, db):
        user, _ = await crud.get_or_create_user(db, "hist2@example.com")
        await crud.create_report(db, id=uuid.uuid4().hex, user_id=user.id,
                                 filter_url="https://olx.pl/x", is_limited=True, status="failed")
        await crud.create_report(db, id=uuid.uuid4().hex, user_id=user.id,
                                 filter_url="https://olx.pl/y", is_limited=True, status="done")
        reports = await crud.get_user_reports(db, user.id)
        assert len(reports) == 1
