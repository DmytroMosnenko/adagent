"""
Background analysis task. Runs inside FastAPI's BackgroundTasks pool (asyncio).
One task per report — fully async, no threads needed except for Stripe/SES calls.
"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import settings
from .db import async_session
from . import crud, scraper, ai_client, report_builder, email_client
from .prompts_registry import PRESETS
from .languages import build_language_instruction
from .logger import get_logger

logger = get_logger(__name__)


def _load_prompt(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def _prompt_paths(preset: str) -> tuple[str, str]:
    return (
        f"prompts/{preset}_ad.txt",
        f"prompts/{preset}_summary.txt",
    )


async def _maybe_notify_finished(report_id: str, status: str) -> None:
    """
    Send the opt-in "your report is ready" email, if the user checked the
    box on the progress page. Re-reads the report so we pick up a flag
    that was toggled after the task started. Never raises — a failed
    notification email must not affect the report's own status.
    """
    try:
        async with async_session() as db:
            report = await crud.get_report(db, report_id)
            if not report or not report.notify_email or not report.user_id:
                return
            user = await crud.get_user_by_id(db, report.user_id)
            if not user:
                return
        await email_client.send_report_ready(user.email, report_id, status=status)
    except Exception as exc:
        logger.warning("[task] %s finished-notification email failed: %s", report_id, exc)


async def run_analysis(report_id: str) -> None:
    """
    Main background task.  Steps:
      1. Collect all ad links
      2. Store total count; apply freemium limit
      3+4. Extract + AI-analyze all ads concurrently (progress updated as
           each ad finishes — see _process_one_ad)
      5. AI summary
      6. Build HTML report (preset) or store JSON (custom)
      7. Mark report done
    """
    logger.info("[task] starting report %s", report_id)

    async with async_session() as db:
        report = await crud.get_report(db, report_id)
        if not report:
            logger.error("[task] report %s not found", report_id)
            return

        await crud.update_report(db, report_id, status="running")

    try:
        # ── 1. Collect all links ───────────────────────────────────────────────
        logger.info("[task] %s collecting links from %s", report_id, report.filter_url)
        all_links, link_diagnosis = await scraper.collect_all_links(report.filter_url, report_id)
        ads_found = len(all_links)
        logger.info("[task] %s found %d links (diagnosis=%s)", report_id, ads_found, link_diagnosis)

        async with async_session() as db:
            await crud.update_report(db, report_id, ads_found=ads_found)

        if ads_found == 0:
            # collect_all_links() already retried internally and, on a
            # confirmed-empty diagnosis, gave up immediately — either way,
            # surface this as a distinct failure (not a silent empty report)
            # so the UI can offer a one-click retry instead of showing a
            # report with nothing in it. Wording depends on *why* it's empty:
            # a real 0-match search shouldn't tell the user it was "blocked".
            logger.warning("[task] %s found 0 ads (diagnosis=%s) — failing so the UI can respond", report_id, link_diagnosis)
            if link_diagnosis == "confirmed_empty":
                message = (
                    "No ads match this search. Your filters are valid — the site "
                    "just doesn't currently have anything listed that fits them. "
                    "Try widening your filters (price range, category, etc.)."
                )
            else:
                message = (
                    "No ads found for this search. This is usually a temporary "
                    "block by the site rather than a real empty result — "
                    "it's worth trying again."
                )
            async with async_session() as db:
                await crud.update_report(
                    db, report_id,
                    status="failed",
                    error_message=message,
                    finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            await _maybe_notify_finished(report_id, "failed")
            return

        # ── 2. Apply freemium limit ────────────────────────────────────────────
        links = all_links[:settings.FREE_ADS_LIMIT] if report.is_limited else all_links
        total = len(links)

        # ── Load prompts ───────────────────────────────────────────────────────
        if report.prompt_preset:
            ad_prompt_path, summary_prompt_path = _prompt_paths(report.prompt_preset)
            ad_prompt      = _load_prompt(ad_prompt_path)
            summary_prompt = _load_prompt(summary_prompt_path)
        else:
            ad_prompt      = report.custom_ad_prompt or ""
            summary_prompt = report.custom_summary_prompt or ""

        # Preset metadata drives which AI-call variant and which output path
        # to use below. Unknown/None preset (i.e. user-typed custom prompts)
        # defaults to non-structured, non-templated — same as before.
        preset_meta   = PRESETS.get(report.prompt_preset, {})
        is_structured = preset_meta.get("output", "raw") == "structured"
        is_templated  = preset_meta.get("templated", False)

        lang_instruction = build_language_instruction(report.report_language, is_structured)
        if lang_instruction:
            ad_prompt      += lang_instruction
            summary_prompt += lang_instruction

        # ── 3+4. Extract content + AI-analyze all ads concurrently ────────────
        # Each ad is its own task: scrape (via scraper.scrape_ad, gated by the
        # global proxy-concurrency limit) then AI-analyze (via ai_client,
        # gated by the global OpenAI-concurrency limit). Ads run concurrently
        # with each other — and concurrently with every other report's ads —
        # while the two fair gates enforce the global limits and share them
        # round-robin across reports. Order of completion is no longer
        # index-based, so progress is tracked by a completion counter instead.
        logger.info("[task] %s extracting and analyzing %d ads", report_id, total)

        progress_lock = asyncio.Lock()
        completed = 0

        async def _process_one_ad(url: str) -> dict:
            nonlocal completed

            # -- Extract --
            data = await scraper.scrape_ad(report_id, url)

            # -- Analyze --
            try:
                if is_templated:
                    analysis = await ai_client.analyze_ad_templated(data, ad_prompt, report_id)
                else:
                    analysis = await ai_client.analyze_ad(data, ad_prompt, report_id)
            except Exception as exc:
                analysis = f"AI ERROR: {exc}"
                logger.warning("[task] ad analysis failed: %s", exc)

            # Serialize the increment + DB write so progress is always
            # written in increasing order even though ads finish out of order.
            async with progress_lock:
                completed += 1
                async with async_session() as db:
                    await crud.update_report(db, report_id, ads_analyzed=completed)
            logger.info("[task] %s ad %d/%d done: %s", report_id, completed, total, url)

            return {"url": url, "data": data, "analysis": analysis}

        results: list[dict] = await asyncio.gather(*[_process_one_ad(u) for u in links])

        # ── 5. AI summary ──────────────────────────────────────────────────────
        logger.info("[task] %s generating summary", report_id)
        try:
            if is_templated:
                summary_text = await ai_client.analyze_summary_templated(results, summary_prompt, report_id)
            else:
                summary_text = await ai_client.analyze_summary(results, summary_prompt, report_id)
        except Exception as exc:
            summary_text = json.dumps({"market_summary": f"Summary failed: {exc}",
                                       "price_range": "", "average_price": "", "recommendation": ""})
            logger.warning("[task] summary failed: %s", exc)

        # ── 6. Build output ────────────────────────────────────────────────────
        if is_structured:
            # Full HTML report
            html = report_builder.build_html_report(
                results=results,
                summary_text=summary_text,
                filter_url=report.filter_url,
                report_id=report_id,
                ads_found=ads_found,
                is_limited=report.is_limited,
                template_dir="templates",
                template_file="report.html.j2",
            )
            report_dir = Path(settings.REPORT_STORAGE_PATH)
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = str(report_dir / f"{report_id}.html")
            Path(report_path).write_text(html, encoding="utf-8")

            async with async_session() as db:
                await crud.update_report(
                    db, report_id,
                    status="done",
                    report_path=report_path,
                    finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            await _maybe_notify_finished(report_id, "done")
        else:
            # Custom prompts, or a preset with output="raw" (e.g. vehicles_detailed) — store JSON
            payload = json.dumps({
                "ads": [{"url": r["url"], "analysis": r["analysis"]} for r in results],
                "summary": summary_text,
            }, ensure_ascii=False)

            async with async_session() as db:
                await crud.update_report(
                    db, report_id,
                    status="done",
                    result_json=payload,
                    finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            await _maybe_notify_finished(report_id, "done")

        logger.info("[task] %s done", report_id)

    except Exception as exc:
        logger.exception("[task] %s failed: %s", report_id, exc)
        async with async_session() as db:
            await crud.update_report(
                db, report_id,
                status="failed",
                error_message=str(exc),
                finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        await _maybe_notify_finished(report_id, "failed")
