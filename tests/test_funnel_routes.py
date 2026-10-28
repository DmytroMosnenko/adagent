"""Funnel tracking through the real app: beacon endpoint, /analyze events, report outcome rows,
internal report endpoints, logging-failure isolation, landing page content.
Uses the in-memory SQLite fixtures from conftest.py."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from service import crud, funnel
from service.config import settings

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
SID = "b" * 32
FAR_PAST, FAR_FUTURE = datetime(2000, 1, 1), datetime(2100, 1, 1)


@pytest.fixture()
def funnel_db(db_engine):
    """Point funnel's lazily-imported service.db.async_session at the test SQLite engine."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    with patch("service.db.async_session", factory):
        yield factory


@pytest.fixture(autouse=True)
def _fresh_limiter():
    import adagent_server
    adagent_server._event_limiter._hits.clear()


async def _events(db):
    return await crud.get_funnel_events(db, FAR_PAST, FAR_FUTURE)


def _beacon(**kw):
    d = {"event": "landing", "sid": SID, "gclid": True, "lang": "pl"}
    d.update(kw)
    import json
    return json.dumps(d)


# ── POST /api/e ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_beacon_records_event_after_204(client, db, funnel_db):
    r = await client.post("/api/e", content=_beacon(utm_source="google"),
                          headers={"user-agent": UA, "content-type": "text/plain"})
    assert r.status_code == 204
    rows = await _events(db)
    assert len(rows) == 1
    e = rows[0]
    assert (e.event, e.sid, e.has_gclid, e.device, e.ui_lang, e.utm_source) == ("landing", SID, True, "mobile", "pl", "google")


@pytest.mark.asyncio
@pytest.mark.parametrize("headers,content", [
    ({"user-agent": "python-httpx/0.27"}, None),          # bot UA
    ({"user-agent": ""}, None),                            # empty UA
    ({"user-agent": UA}, "not json"),                      # malformed
    ({"user-agent": UA}, '{"event":"analyze_started","sid":"%s"}' % SID),   # forged server-only event
    ({"user-agent": UA}, "x" * 5000),                      # oversized
])
async def test_beacon_ignores_bad_input_but_still_204(client, db, funnel_db, headers, content):
    r = await client.post("/api/e", content=content if content is not None else _beacon(), headers=headers)
    assert r.status_code == 204
    assert await _events(db) == []


@pytest.mark.asyncio
async def test_beacon_is_rate_limited_per_client(client, db, funnel_db):
    for _ in range(125):
        r = await client.post("/api/e", content=_beacon(), headers={"user-agent": UA})
        assert r.status_code == 204
    assert len(await _events(db)) == 120


@pytest.mark.asyncio
async def test_beacon_db_failure_never_breaks_the_request(client, db, funnel_db):
    with patch("service.crud.add_funnel_event", new=AsyncMock(side_effect=RuntimeError("db down"))):
        r = await client.post("/api/e", content=_beacon(), headers={"user-agent": UA})
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_beacon_disabled_flag(client, db, funnel_db):
    with patch.object(settings, "FUNNEL_ENABLED", False):
        r = await client.post("/api/e", content=_beacon(), headers={"user-agent": UA})
    assert r.status_code == 204 and await _events(db) == []


# ── /analyze server-side events ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analyze_invalid_url_is_logged_with_reason_and_inherits_ad_source(client, db, funnel_db):
    await client.post("/api/e", content=_beacon(), headers={"user-agent": UA})          # landing, gclid=True
    r = await client.post("/analyze", data={"filter_url": "https://www.allegro.pl/oferty/", "prompt_preset": "vehicles",
                                            "sid": SID}, headers={"user-agent": UA}, follow_redirects=False)
    assert r.status_code == 303 and "error=invalid_url" in r.headers["location"]
    bad = [e for e in await _events(db) if e.event == "invalid_url"]
    assert len(bad) == 1
    assert (bad[0].detail, bad[0].sid, bad[0].has_gclid, bad[0].device, bad[0].url_host) == \
           ("bad_host", SID, True, "mobile", "other")


@pytest.mark.asyncio
async def test_analyze_no_scheme_reason(client, db, funnel_db):
    await client.post("/analyze", data={"filter_url": "olx.pl/motoryzacja", "prompt_preset": "vehicles"},
                      headers={"user-agent": UA}, follow_redirects=False)
    bad = [e for e in await _events(db) if e.event == "invalid_url"]
    assert [b.detail for b in bad] == ["no_scheme"] and bad[0].sid is None     # JS off -> unattributed, still logged


@pytest.mark.asyncio
async def test_analyze_started_row_has_report_id_and_no_full_url(client, db, funnel_db):
    url = "https://www.olx.pl/motoryzacja/samochody/q-secret/"
    r = await client.post("/analyze", data={"filter_url": url, "prompt_preset": "vehicles", "sid": SID},
                          headers={"user-agent": UA}, follow_redirects=False)
    report_id = r.headers["location"].split("/report/")[1]
    started = [e for e in await _events(db) if e.event == "analyze_started"]
    assert len(started) == 1 and started[0].detail == report_id
    assert (started[0].url_host, started[0].url_label) == ("olx.pl", "motoryzacja:filter")
    assert "filter_url" not in {c.name for c in started[0].__table__.columns}      # the URL itself is never stored


@pytest.mark.asyncio
async def test_analyze_still_works_when_logging_is_broken(client, db, funnel_db):
    with patch("service.crud.add_funnel_event", new=AsyncMock(side_effect=RuntimeError("db down"))):
        ok = await client.post("/analyze", data={"filter_url": "https://www.olx.pl/motoryzacja/", "prompt_preset": "vehicles"},
                               headers={"user-agent": UA}, follow_redirects=False)
        bad = await client.post("/analyze", data={"filter_url": "https://x.example/", "prompt_preset": "vehicles"},
                                headers={"user-agent": UA}, follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"].startswith("/report/")
    assert bad.status_code == 303 and "error=invalid_url" in bad.headers["location"]


# ── report outcome (tasks.py) ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_outcome_copies_attribution_from_started(db, funnel_db):
    await crud.add_funnel_event(db, event="analyze_started", sid=SID, has_gclid=True, device="mobile",
                                ui_lang="pl", url_host="olx.pl", url_label="motoryzacja:filter", detail="r" * 32)
    await funnel.record_report_outcome(funnel_db, "r" * 32, "analyze_done")
    done = [e for e in await _events(db) if e.event == "analyze_done"]
    assert len(done) == 1 and (done[0].sid, done[0].has_gclid, done[0].device) == (SID, True, "mobile")


@pytest.mark.asyncio
async def test_report_outcome_without_started_row_writes_nothing_and_never_raises(db, funnel_db):
    await funnel.record_report_outcome(funnel_db, "z" * 32, "analyze_failed")
    assert await _events(db) == []

    class Broken:
        def __call__(self): raise RuntimeError("no db")
    await funnel.record_report_outcome(Broken(), "z" * 32, "analyze_failed")      # must not raise


# ── /internal/* ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_internal_disabled_without_token(client, funnel_db):
    with patch.object(settings, "INTERNAL_TOKEN", ""):
        assert (await client.get("/internal/funnel-report", headers={"x-internal-token": "x"})).status_code == 404


@pytest.mark.asyncio
async def test_internal_requires_token_and_rejects_proxied_requests(client, funnel_db):
    with patch.object(settings, "INTERNAL_TOKEN", "secret-token"):
        assert (await client.get("/internal/funnel-report")).status_code == 403
        assert (await client.get("/internal/funnel-report", headers={"x-internal-token": "nope"})).status_code == 403
        # anything that came through nginx carries X-Real-IP -> treated as non-local
        r = await client.get("/internal/funnel-report", headers={"x-internal-token": "secret-token", "x-real-ip": "1.2.3.4"})
        assert r.status_code == 404
        ok = await client.get("/internal/funnel-report", headers={"x-internal-token": "secret-token"})
        assert ok.status_code == 200 and "AdAgent funnel" in ok.text


@pytest.mark.asyncio
async def test_internal_report_formats_and_bad_date(client, db, funnel_db):
    await crud.add_funnel_event(db, event="landing", sid=SID, has_gclid=True, device="mobile")
    h = {"x-internal-token": "t"}
    with patch.object(settings, "INTERNAL_TOKEN", "t"):
        today = datetime.now(ZoneInfo(settings.REPORT_TIMEZONE)).date().isoformat()   # the report's own "day"
        j = (await client.get(f"/internal/funnel-report?date={today}&format=json", headers=h)).json()
        assert j["columns"]["Ads·mobile"]["landing"]["sessions"] == 1
        assert "<table" in (await client.get(f"/internal/funnel-report?date={today}&format=html", headers=h)).text
        assert (await client.get("/internal/funnel-report?date=garbage", headers=h)).status_code == 400


@pytest.mark.asyncio
async def test_email_endpoint_sends_to_admin_and_purges_old_rows(client, db, funnel_db):
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=settings.FUNNEL_RETENTION_DAYS + 5)
    await crud.add_funnel_event(db, event="landing", sid=SID, device="mobile", created_at=old)
    await crud.add_funnel_event(db, event="landing", sid="c" * 32, device="mobile")
    h = {"x-internal-token": "t"}
    with patch.object(settings, "INTERNAL_TOKEN", "t"):
        with patch.object(settings, "ADMIN_EMAIL", ""):
            assert (await client.post("/internal/funnel-report/email", headers=h)).status_code == 503
        with patch.object(settings, "ADMIN_EMAIL", "me@example.com"), \
             patch("service.email_client._send", return_value=True) as send:
            r = await client.post("/internal/funnel-report/email", headers=h)
        assert r.status_code == 200 and r.json()["purged"] == 1
        assert send.call_args.args[0] == "me@example.com" and "AdAgent funnel" in send.call_args.args[1]
        with patch.object(settings, "ADMIN_EMAIL", "me@example.com"), \
             patch("service.email_client._send", return_value=False):
            assert (await client.post("/internal/funnel-report/email", headers=h)).status_code == 502
    assert len(await _events(db)) == 1


# ── landing page ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_landing_has_demo_helper_and_sample_links_work_without_js(client):
    pl = (await client.get("/pl")).text
    assert 'id="demo"' in pl and 'href="#demo-car1"' in pl and 'id="demo-flat1"' in pl      # anchors = no-JS fallback
    assert "Udostępnij → Kopiuj link" in pl and "Przykład" in pl
    assert 'action="/analyze"' in pl and 'name="filter_url"' in pl                           # form untouched
    assert "/static/funnel.js" in pl and "/static/demo.js" in pl
    en = (await client.get("/en")).text
    assert "Share → Copy link" in en and "Octavia estate diesel" in en


@pytest.mark.asyncio
async def test_landing_omits_tracker_when_disabled(client):
    with patch.object(settings, "FUNNEL_ENABLED", False):
        html = (await client.get("/pl")).text
    assert "funnel.js" not in html and 'id="demo"' in html
