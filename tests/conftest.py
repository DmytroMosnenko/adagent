"""
Shared fixtures.
External services (Stripe, SES, OpenAI, Playwright) are always mocked so
tests never make real network calls and don't need API keys.
"""
import os
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# ── Env vars BEFORE any service module is imported ────────────────────────────
os.environ.update({
    "DB_PASSWORD":              "test",
    "STRIPE_SECRET_KEY":        "sk_test_fake",
    "STRIPE_WEBHOOK_SECRET":    "whsec_fake",
    "STRIPE_PRICES_JSON":       '{"monthly": "price_monthly_fake"}',
    "AWS_ACCESS_KEY_ID":        "FAKE",
    "AWS_SECRET_ACCESS_KEY":    "FAKE",
    "OPENAI_API_KEY":           "sk-fake",
    "SECRET_KEY":               "x" * 64,
    "REPORT_STORAGE_PATH":      "/tmp/adagent_test_reports",
    "PLAYWRIGHT_BROWSERS_PATH": "/tmp/pw_fake",
    "FREE_ADS_LIMIT":           "5",
})

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool
from httpx import AsyncClient, ASGITransport

from service.models import Base
from service.db import get_db

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


# ── DB engine (one in-memory DB per test) ─────────────────────────────────────

@pytest_asyncio.fixture()
async def db_engine():
    engine = create_async_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture()
async def db(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


# ── Patch tasks.async_session → SQLite (sync fixture, no teardown issues) ─────

@pytest.fixture()
def patch_task_db(db_engine):
    """
    Route service.tasks internal DB sessions to the same SQLite engine used
    by the `db` fixture.  Uses patcher.start/stop to avoid pytest-asyncio
    async-generator teardown conflicts.
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    patcher = patch("service.tasks.async_session", factory)
    patcher.start()
    yield factory
    patcher.stop()


# ── FastAPI test client ────────────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def client(db_engine):
    """
    FastAPI TestClient backed by in-memory SQLite.
    run_analysis, Stripe, and SES are all mocked.
    """
    from adagent_server import app

    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def _override_db():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db] = _override_db

    with (
        patch("adagent_server.run_analysis", new=AsyncMock()),
        patch("service.email_client._send", return_value=True),
        patch("stripe.Webhook.construct_event", return_value=MagicMock(type="noop")),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c

    app.dependency_overrides.clear()
