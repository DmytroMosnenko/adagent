"""
Tests for service/tasks.py — the analysis pipeline.
Playwright (scraper) and OpenAI (ai_client) are fully mocked.
"""
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import pytest

from service import crud
from service.tasks import run_analysis


# ── Fake data factories ────────────────────────────────────────────────────────

def _fake_links(n: int = 8) -> list[str]:
    return [f"https://www.otomoto.pl/oferta/ad-{i}-ID{i}.html" for i in range(n)]


def _fake_ad_data(links: list[str]) -> list[dict]:
    return [
        {
            "url": url,
            "data": {
                "url": url,
                "title": f"Car Ad #{i}",
                "price": f"{30000 + i * 1000} zł",
                "location": "Warszawa",
                "description": f"Good car number {i}.",
            },
        }
        for i, url in enumerate(links)
    ]


def _fake_analysis(n: int = 1) -> str:
    return json.dumps({
        "rating": 7, "verdict": "worth_viewing",
        "verdict_note": "Decent car.", "summary": f"Ad {n} is good.",
        "specs": {"make": "Mini", "model": "Cooper", "year": 2018, "mileage_km": 50000},
        "asking_price": f"{30000 + n * 1000} zł",
        "price_assessment": "fair", "red_flags": [], "positives": ["Good condition"],
    })


def _fake_summary() -> str:
    return json.dumps({
        "market_summary": "Good market.", "price_range": "30–45k zł",
        "average_price": "37k zł", "recommendation": "Buy the Mini.",
    })


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _create_report(db, **kwargs) -> str:
    report_id = uuid.uuid4().hex
    defaults = dict(
        filter_url="https://www.olx.pl/motoryzacja/samochody/q-mini/",
        prompt_preset="vehicles",
        is_limited=True,
        status="pending",
    )
    defaults.update(kwargs)
    await crud.create_report(db, id=report_id, **defaults)
    return report_id


# ── Tests ──────────────────────────────────────────────────────────────────────

async def test_task_completes_successfully_with_preset(db, patch_task_db, tmp_path):
    """Full pipeline: scrape → analyze per-ad → summarize → write HTML file."""
    all_links = _fake_links(8)
    analyzed_links = all_links[:5]   # freemium limit = 5
    ad_data = _fake_ad_data(analyzed_links)

    report_id = await _create_report(db, is_limited=True)

    with (
        patch("service.tasks.scraper.collect_all_links",
              new=AsyncMock(return_value=all_links)),
        patch("service.tasks.scraper.extract_ads",
              new=AsyncMock(return_value=ad_data)),
        patch("service.tasks.ai_client.analyze_ad",
              new=AsyncMock(side_effect=lambda data, prompt: _fake_analysis())),
        patch("service.tasks.ai_client.analyze_summary",
              new=AsyncMock(return_value=_fake_summary())),
        patch("service.tasks.settings.REPORT_STORAGE_PATH", str(tmp_path)),
        patch("service.tasks.settings.FREE_ADS_LIMIT", 5),
    ):
        await run_analysis(report_id)

    report = await crud.get_report(db, report_id)
    assert report.status == "done"
    assert report.ads_found == 8          # total scraped
    assert report.ads_analyzed == 5       # freemium limit applied
    assert report.report_path is not None
    assert Path(report.report_path).exists()
    html = Path(report.report_path).read_text()
    assert "leaderboard" in html
    assert "AdAgent" in html


async def test_task_applies_freemium_limit(db, patch_task_db, tmp_path):
    """Free users: scraper finds 10 ads, but only 5 are analyzed."""
    all_links = _fake_links(10)
    extracted_capture = []

    async def fake_extract(links):
        extracted_capture.extend(links)
        return _fake_ad_data(links)

    report_id = await _create_report(db, is_limited=True)

    with (
        patch("service.tasks.scraper.collect_all_links",
              new=AsyncMock(return_value=all_links)),
        patch("service.tasks.scraper.extract_ads", side_effect=fake_extract),
        patch("service.tasks.ai_client.analyze_ad",
              new=AsyncMock(return_value=_fake_analysis())),
        patch("service.tasks.ai_client.analyze_summary",
              new=AsyncMock(return_value=_fake_summary())),
        patch("service.tasks.settings.REPORT_STORAGE_PATH", str(tmp_path)),
        patch("service.tasks.settings.FREE_ADS_LIMIT", 5),
    ):
        await run_analysis(report_id)

    assert len(extracted_capture) == 5   # only 5 links passed to extract_ads
    report = await crud.get_report(db, report_id)
    assert report.ads_found == 10
    assert report.ads_analyzed == 5


async def test_task_no_limit_for_subscribed_user(db, patch_task_db, tmp_path):
    """Pro users: all 10 ads are analyzed."""
    all_links = _fake_links(10)
    extracted_capture = []

    async def fake_extract(links):
        extracted_capture.extend(links)
        return _fake_ad_data(links)

    report_id = await _create_report(db, is_limited=False)

    with (
        patch("service.tasks.scraper.collect_all_links",
              new=AsyncMock(return_value=all_links)),
        patch("service.tasks.scraper.extract_ads", side_effect=fake_extract),
        patch("service.tasks.ai_client.analyze_ad",
              new=AsyncMock(return_value=_fake_analysis())),
        patch("service.tasks.ai_client.analyze_summary",
              new=AsyncMock(return_value=_fake_summary())),
        patch("service.tasks.settings.REPORT_STORAGE_PATH", str(tmp_path)),
        patch("service.tasks.settings.FREE_ADS_LIMIT", 5),
    ):
        await run_analysis(report_id)

    assert len(extracted_capture) == 10   # all links extracted
    report = await crud.get_report(db, report_id)
    assert report.ads_analyzed == 10


async def test_task_marks_failed_on_scraper_error(db, patch_task_db):
    """If the scraper throws, the report is marked as failed."""
    report_id = await _create_report(db)

    with patch("service.tasks.scraper.collect_all_links",
               new=AsyncMock(side_effect=RuntimeError("Playwright crashed"))):
        await run_analysis(report_id)

    report = await crud.get_report(db, report_id)
    assert report.status == "failed"
    assert "Playwright crashed" in (report.error_message or "")


async def test_task_handles_ai_error_per_ad_gracefully(db, patch_task_db, tmp_path):
    """If AI fails for one ad, others still succeed; report is marked done."""
    links = _fake_links(3)
    call_count = [0]

    async def flaky_ai(data, prompt):
        call_count[0] += 1
        if call_count[0] == 2:
            raise RuntimeError("OpenAI rate limit")
        return _fake_analysis(call_count[0])

    report_id = await _create_report(db, is_limited=False)

    with (
        patch("service.tasks.scraper.collect_all_links",
              new=AsyncMock(return_value=links)),
        patch("service.tasks.scraper.extract_ads",
              new=AsyncMock(return_value=_fake_ad_data(links))),
        patch("service.tasks.ai_client.analyze_ad", side_effect=flaky_ai),
        patch("service.tasks.ai_client.analyze_summary",
              new=AsyncMock(return_value=_fake_summary())),
        patch("service.tasks.settings.REPORT_STORAGE_PATH", str(tmp_path)),
        patch("service.tasks.settings.FREE_ADS_LIMIT", 5),
    ):
        await run_analysis(report_id)

    report = await crud.get_report(db, report_id)
    # Report is still done — one bad ad doesn't kill the whole run
    assert report.status == "done"
    assert report.ads_analyzed == 3


async def test_task_custom_prompts_stores_json(db, patch_task_db):
    """Custom-prompt reports store result_json, not a file."""
    links = _fake_links(2)
    report_id = await _create_report(
        db, prompt_preset=None,
        custom_ad_prompt="Analyze this ad.",
        custom_summary_prompt="Summarize these.",
        is_limited=False,
    )

    with (
        patch("service.tasks.scraper.collect_all_links",
              new=AsyncMock(return_value=links)),
        patch("service.tasks.scraper.extract_ads",
              new=AsyncMock(return_value=_fake_ad_data(links))),
        patch("service.tasks.ai_client.analyze_ad",
              new=AsyncMock(return_value="This ad looks OK.")),
        patch("service.tasks.ai_client.analyze_summary",
              new=AsyncMock(return_value="Overall decent market.")),
        patch("service.tasks.settings.FREE_ADS_LIMIT", 5),
    ):
        await run_analysis(report_id)

    report = await crud.get_report(db, report_id)
    assert report.status == "done"
    assert report.report_path is None         # no HTML file
    assert report.result_json is not None
    data = json.loads(report.result_json)
    assert len(data["ads"]) == 2
    assert data["ads"][0]["analysis"] == "This ad looks OK."
    assert data["summary"] == "Overall decent market."


async def test_task_skips_nonexistent_report(db, patch_task_db):
    """Calling run_analysis with a bad ID should not crash."""
    await run_analysis("nonexistent_id_xyz")  # must not raise


async def test_task_updates_progress_during_analysis(db, patch_task_db, tmp_path):
    """
    ads_analyzed is incremented to the final count after all ads are done.
    The task commits ads_analyzed=N *after* each AI call, so we check
    the final persisted value equals the number of ads processed.
    """
    links = _fake_links(3)
    report_id = await _create_report(db, is_limited=False)

    with (
        patch("service.tasks.scraper.collect_all_links",
              new=AsyncMock(return_value=links)),
        patch("service.tasks.scraper.extract_ads",
              new=AsyncMock(return_value=_fake_ad_data(links))),
        patch("service.tasks.ai_client.analyze_ad",
              new=AsyncMock(return_value=_fake_analysis())),
        patch("service.tasks.ai_client.analyze_summary",
              new=AsyncMock(return_value=_fake_summary())),
        patch("service.tasks.settings.REPORT_STORAGE_PATH", str(tmp_path)),
        patch("service.tasks.settings.FREE_ADS_LIMIT", 5),
    ):
        await run_analysis(report_id)

    report = await crud.get_report(db, report_id)
    assert report.ads_analyzed == 3          # final count persisted correctly
    assert report.status == "done"


class TestScraperIntegration:
    """Tests for scraper helpers that don't require a real browser."""

    def test_next_page_by_url_increments_page_param(self):
        from service.scraper import _next_page_by_url
        url = "https://www.otomoto.pl/osobowe/mini?page=1&order=created_at%3Adesc"
        result = _next_page_by_url(url)
        assert "page=2" in result

    def test_next_page_by_url_starts_from_one(self):
        from service.scraper import _next_page_by_url
        url = "https://www.otomoto.pl/osobowe/mini"
        result = _next_page_by_url(url)
        assert "page=2" in result

    def test_next_page_by_url_increments_higher_pages(self):
        from service.scraper import _next_page_by_url
        url = "https://www.otodom.pl/pl/wyniki/sprzedaz/?page=5"
        result = _next_page_by_url(url)
        assert "page=6" in result

    def test_is_ad_accepts_olx(self):
        from service.scraper import _is_ad
        assert _is_ad("https://www.olx.pl/d/oferta/mini-paceman-ID123.html")

    def test_is_ad_accepts_otomoto(self):
        from service.scraper import _is_ad
        assert _is_ad("https://www.otomoto.pl/oferta/mini-cooper-ID456.html")

    def test_is_ad_accepts_otodom(self):
        from service.scraper import _is_ad
        assert _is_ad("https://www.otodom.pl/oferta/mieszkanie-ID789.html")

    def test_is_ad_rejects_navigation_links(self):
        from service.scraper import _is_ad
        assert not _is_ad("https://www.olx.pl/motoryzacja/samochody/")
        assert not _is_ad("https://www.olx.pl/")
        assert not _is_ad("https://www.otomoto.pl/osobowe/mini")

    def test_site_detection(self):
        from service.scraper import _site
        assert _site("https://www.otomoto.pl/oferta/x") == "otomoto"
        assert _site("https://www.otodom.pl/oferta/x")  == "otodom"
        assert _site("https://www.olx.pl/d/oferta/x")   == "olx"


# ── Single-ad mode ─────────────────────────────────────────────────────────────

AD_URL = "https://www.olx.pl/d/oferta/mini-cooper-s-2018-CID5-ID123abc.html"


def _ad_page(url=AD_URL):
    return {"url": url, "title": "Mini Cooper S 2018", "price": "45 000 zł",
            "page_text": "Mini Cooper S, pierwszy właściciel."}


async def test_single_ad_skips_collection_and_market_summary(db, patch_task_db, tmp_path):
    report_id = await _create_report(db, filter_url=AD_URL + "?reason=share#x", prompt_preset="vehicles")
    scrape = AsyncMock(return_value=_ad_page())
    collect, summary = AsyncMock(), AsyncMock(return_value=_fake_summary())

    with (
        patch("service.tasks.scraper.collect_all_links", new=collect),
        patch("service.tasks.scraper.scrape_ad", new=scrape),
        patch("service.tasks.ai_client.analyze_ad", new=AsyncMock(return_value=_fake_analysis())),
        patch("service.tasks.ai_client.analyze_summary", new=summary),
        patch("service.tasks.settings.REPORT_STORAGE_PATH", str(tmp_path)),
    ):
        await run_analysis(report_id)

    collect.assert_not_called()
    summary.assert_not_called()
    assert scrape.call_args.args[1] == AD_URL            # tracking params / fragment stripped
    assert scrape.call_args.kwargs == {"single": True}
    report = await crud.get_report(db, report_id)
    assert report.status == "done" and report.ads_found == 1 and report.ads_analyzed == 1
    html = Path(report.report_path).read_text()
    assert "View ad" in html and "Market Overview" not in html.split("<body>")[1]


async def test_failed_single_ad_gives_a_clear_reason_and_frees_the_free_slot(db, patch_task_db):
    from datetime import datetime, timedelta
    report_id = await _create_report(db, filter_url=AD_URL, is_limited=True)
    since = datetime.utcnow() - timedelta(days=1)
    assert await crud.count_free_reports_since(db, since) == 1

    gone = {"url": AD_URL, "error": "HTTP 404", "error_code": "gone"}
    with patch("service.tasks.scraper.scrape_ad", new=AsyncMock(return_value=gone)):
        await run_analysis(report_id)

    report = await crud.get_report(db, report_id)
    assert report.status == "failed"
    assert report.error_message.startswith("This ad is no longer available")
    assert report.is_limited is False
    assert await crud.count_free_reports_since(db, since) == 0


@pytest.mark.parametrize("code", ["blocked", "timeout", "unparseable"])
async def test_failed_single_ad_codes_are_logged_and_never_cost_a_slot(db, patch_task_db, code):
    report_id = await _create_report(db, filter_url=AD_URL, is_limited=True)
    bad = {"url": AD_URL, "error": "raw scraper text", "error_code": code}
    outcome = AsyncMock()
    with (
        patch("service.tasks.scraper.scrape_ad", new=AsyncMock(return_value=bad)),
        patch("service.tasks.funnel.record_report_outcome", new=outcome),
    ):
        await run_analysis(report_id)

    report = await crud.get_report(db, report_id)
    assert report.status == "failed" and report.is_limited is False
    assert "raw scraper text" not in report.error_message
    args, kwargs = outcome.call_args
    assert args[2] == "analyze_failed" and kwargs["reason"] == code
    assert isinstance(kwargs["duration_ms"], int)


async def test_single_ad_ai_failure_fails_cleanly(db, patch_task_db):
    report_id = await _create_report(db, filter_url=AD_URL, is_limited=True)
    with (
        patch("service.tasks.scraper.scrape_ad", new=AsyncMock(return_value=_ad_page())),
        patch("service.tasks.ai_client.analyze_ad", new=AsyncMock(return_value="AI ERROR: boom")),
    ):
        await run_analysis(report_id)
    report = await crud.get_report(db, report_id)
    assert report.status == "failed" and report.is_limited is False


async def test_failed_filter_report_still_counts_against_the_free_tier(db, patch_task_db):
    """Only single-ad failures release a slot; filter-mode behaviour is unchanged."""
    from datetime import datetime, timedelta
    report_id = await _create_report(db, is_limited=True)
    with patch("service.tasks.scraper.collect_all_links",
               new=AsyncMock(side_effect=RuntimeError("boom"))):
        await run_analysis(report_id)
    report = await crud.get_report(db, report_id)
    assert report.status == "failed" and report.is_limited is True
    assert await crud.count_free_reports_since(db, datetime.utcnow() - timedelta(days=1)) == 1
