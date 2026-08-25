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
