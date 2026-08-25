"""Tests for adagent_server.py — HTTP routes via in-memory test client."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from service import crud


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_user_session(db, email="test@example.com"):
    """Create a user + active session. Returns (user, token)."""
    user, _ = await crud.get_or_create_user(db, email)
    token = f"session_{uuid.uuid4().hex}"
    await crud.create_session(db, token, user.id, ttl_days=7)
    return user, token


async def _make_subscribed_user_session(db, email="pro@example.com"):
    """Create a user with an active subscription. Returns (user, token)."""
    user, token = await _make_user_session(db, email)
    await crud.upsert_subscription(
        db, user_id=user.id, stripe_customer_id="cus_test",
        stripe_subscription_id="sub_test", stripe_price_id="price_monthly",
        plan_period="monthly", status="active",
        current_period_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
    )
    return user, token


# ── Static / public pages ─────────────────────────────────────────────────────

async def test_index_returns_200(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "AdAgent" in r.text
    assert "olx.pl" in r.text.lower()


async def test_index_shows_free_limit_for_anon(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "5" in r.text            # FREE_ADS_LIMIT
    assert "Upgrade" in r.text


async def test_health_returns_ok(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_subscribe_page_returns_200(client):
    r = await client.get("/subscribe")
    assert r.status_code == 200
    assert "9.99" in r.text


async def test_auth_page_returns_200(client):
    r = await client.get("/auth")
    assert r.status_code == 200
    assert "email" in r.text.lower()


# ── Analyze ────────────────────────────────────────────────────────────────────

async def test_analyze_valid_url_redirects_to_report(client):
    r = await client.post("/analyze", data={
        "filter_url": "https://www.olx.pl/motoryzacja/samochody/",
        "prompt_preset": "vehicles",
    }, follow_redirects=False)
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("/report/")
    assert len(location.split("/report/")[1]) == 32  # uuid4 hex


async def test_analyze_invalid_url_redirects_with_error(client):
    r = await client.post("/analyze", data={
        "filter_url": "https://www.allegro.pl/oferty/",
        "prompt_preset": "vehicles",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "error" in r.headers["location"].lower()


async def test_analyze_missing_custom_prompts_redirects_with_error(client):
    r = await client.post("/analyze", data={
        "filter_url": "https://www.olx.pl/motoryzacja/",
        # no prompt_preset and no custom prompts
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "error" in r.headers["location"].lower()


async def test_analyze_free_user_creates_limited_report(client, db):
    r = await client.post("/analyze", data={
        "filter_url": "https://www.olx.pl/motoryzacja/samochody/",
        "prompt_preset": "vehicles",
    }, follow_redirects=False)
    assert r.status_code == 303
    report_id = r.headers["location"].split("/report/")[1]
    report = await crud.get_report(db, report_id)
    assert report.is_limited is True


async def test_analyze_subscribed_user_creates_unlimited_report(client, db):
    user, token = await _make_subscribed_user_session(db, "subuser@example.com")
    r = await client.post("/analyze",
        data={"filter_url": "https://www.olx.pl/motoryzacja/",
              "prompt_preset": "vehicles"},
        cookies={"adagent_session": token},
        follow_redirects=False,
    )
    assert r.status_code == 303
    report_id = r.headers["location"].split("/report/")[1]
    report = await crud.get_report(db, report_id)
    assert report.is_limited is False


async def test_analyze_custom_prompts_accepted(client, db):
    r = await client.post("/analyze", data={
        "filter_url": "https://www.olx.pl/elektronika/",
        "custom_ad_prompt": "Analyze this electronics ad.",
        "custom_summary_prompt": "Summarize these electronics ads.",
    }, follow_redirects=False)
    assert r.status_code == 303
    report_id = r.headers["location"].split("/report/")[1]
    report = await crud.get_report(db, report_id)
    assert report.prompt_preset is None
    assert report.custom_ad_prompt == "Analyze this electronics ad."


# ── Report view ────────────────────────────────────────────────────────────────

async def test_report_pending_shows_status_page(client, db):
    report_id = uuid.uuid4().hex
    await crud.create_report(db, id=report_id,
        filter_url="https://olx.pl/x", prompt_preset="vehicles",
        is_limited=True, status="pending")
    r = await client.get(f"/report/{report_id}")
    assert r.status_code == 200
    assert "Analyzing" in r.text


async def test_report_running_shows_status_page(client, db):
    report_id = uuid.uuid4().hex
    await crud.create_report(db, id=report_id,
        filter_url="https://olx.pl/x", prompt_preset="vehicles",
        is_limited=True, status="running")
    r = await client.get(f"/report/{report_id}")
    assert r.status_code == 200
    assert "Analyzing" in r.text


async def test_report_failed_shows_error_page(client, db):
    report_id = uuid.uuid4().hex
    await crud.create_report(db, id=report_id,
        filter_url="https://olx.pl/x", prompt_preset="vehicles",
        is_limited=True, status="failed",
        error_message="Playwright crashed.")
    r = await client.get(f"/report/{report_id}")
    assert r.status_code == 200
    assert "failed" in r.text.lower() or "error" in r.text.lower()


async def test_report_done_preset_serves_html_file(client, db, tmp_path):
    report_id = uuid.uuid4().hex
    html_path = tmp_path / f"{report_id}.html"
    html_path.write_text("<html><body>Report content</body></html>")
    await crud.create_report(db, id=report_id,
        filter_url="https://olx.pl/x", prompt_preset="vehicles",
        is_limited=False, status="done",
        report_path=str(html_path))
    r = await client.get(f"/report/{report_id}")
    assert r.status_code == 200
    assert "Report content" in r.text


async def test_report_done_custom_shows_result_page(client, db):
    report_id = uuid.uuid4().hex
    result = json.dumps({
        "ads": [{"url": "https://olx.pl/x", "analysis": "Looks good."}],
        "summary": "One decent ad found.",
    })
    await crud.create_report(db, id=report_id,
        filter_url="https://olx.pl/x", is_limited=True,
        status="done", result_json=result)
    r = await client.get(f"/report/{report_id}")
    assert r.status_code == 200
    assert "Looks good." in r.text
    assert "One decent ad found." in r.text


async def test_report_not_found_returns_404(client):
    r = await client.get("/report/nonexistent_report_id_xyz")
    assert r.status_code == 404


# ── Status API ────────────────────────────────────────────────────────────────

async def test_status_api_returns_json(client, db):
    report_id = uuid.uuid4().hex
    await crud.create_report(db, id=report_id,
        filter_url="https://olx.pl/x", prompt_preset="vehicles",
        is_limited=True, status="running",
        ads_found=42, ads_analyzed=3)
    r = await client.get(f"/api/report/{report_id}/status")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "running"
    assert data["ads_found"] == 42
    assert data["ads_analyzed"] == 3
    assert data["is_limited"] is True
    assert data["has_preset"] is True


async def test_status_api_missing_returns_404(client):
    r = await client.get("/api/report/ghost/status")
    assert r.status_code == 404


# ── Auth ───────────────────────────────────────────────────────────────────────

async def test_auth_post_redirects_to_sent(client):
    with patch("adagent_server.email_client.send_magic_link", new=AsyncMock(return_value=True)):
        r = await client.post("/auth",
            data={"email": "newuser@example.com"},
            follow_redirects=False)
    assert r.status_code == 303
    assert "sent=newuser" in r.headers["location"]


async def test_auth_verify_valid_token_sets_cookie(client, db):
    token = "test_magic_token_abc"
    await crud.create_magic_link(db, token, "verify@example.com", ttl_minutes=15)
    await crud.get_or_create_user(db, "verify@example.com")
    r = await client.get(f"/auth/verify/{token}", follow_redirects=False)
    assert r.status_code == 303
    assert "adagent_session" in r.cookies
    assert r.headers["location"] == "/history"


async def test_auth_verify_invalid_token_returns_400(client):
    r = await client.get("/auth/verify/totally_fake_token")
    assert r.status_code == 400


async def test_auth_verify_used_token_returns_400(client, db):
    token = "used_token_xyz"
    await crud.create_magic_link(db, token, "used@example.com", ttl_minutes=15)
    await crud.get_or_create_user(db, "used@example.com")
    # First use
    await client.get(f"/auth/verify/{token}", follow_redirects=False)
    # Second use — must fail
    r = await client.get(f"/auth/verify/{token}")
    assert r.status_code == 400


async def test_logout_clears_cookie(client, db):
    user, token = await _make_user_session(db, "logout@example.com")
    r = await client.post("/auth/logout",
        cookies={"adagent_session": token},
        follow_redirects=False)
    assert r.status_code == 303
    # Cookie should be deleted (empty value or max-age=0)
    assert r.cookies.get("adagent_session", "") == ""


# ── History ────────────────────────────────────────────────────────────────────

async def test_history_redirects_anon_to_auth(client):
    r = await client.get("/history", follow_redirects=False)
    assert r.status_code == 303
    assert "/auth" in r.headers["location"]


async def test_history_shows_user_reports(client, db):
    user, token = await _make_user_session(db, "history@example.com")
    await crud.create_report(db, id=uuid.uuid4().hex, user_id=user.id,
        filter_url="https://olx.pl/auta/", prompt_preset="vehicles",
        is_limited=False, status="done")
    r = await client.get("/history", cookies={"adagent_session": token})
    assert r.status_code == 200
    assert "olx.pl/auta/" in r.text
