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
from . import crud, scraper, ai_client, report_builder
from .logger import get_logger

logger = get_logger(__name__)


def _load_prompt(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def _prompt_paths(preset: str) -> tuple[str, str]:
    return (
        f"prompts/{preset}_ad.txt",
        f"prompts/{preset}_summary.txt",
    )


async def run_analysis(report_id: str) -> None:
    """
    Main background task.  Steps:
      1. Collect all ad links
      2. Store total count; apply freemium limit
      3. Extract ad content
      4. AI analysis per ad (update progress after each)
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
        all_links = await scraper.collect_all_links(report.filter_url)
        ads_found = len(all_links)
        logger.info("[task] %s found %d links", report_id, ads_found)

        async with async_session() as db:
            await crud.update_report(db, report_id, ads_found=ads_found)

        # ── 2. Apply freemium limit ────────────────────────────────────────────
        links = all_links[:settings.FREE_ADS_LIMIT] if report.is_limited else all_links

        # ── 3. Extract content ─────────────────────────────────────────────────
        logger.info("[task] %s extracting %d ads", report_id, len(links))
        ad_data_list = await scraper.extract_ads(links)

        # ── 4. AI analysis per ad ──────────────────────────────────────────────
        if report.prompt_preset:
            ad_prompt_path, summary_prompt_path = _prompt_paths(report.prompt_preset)
            ad_prompt      = _load_prompt(ad_prompt_path)
            summary_prompt = _load_prompt(summary_prompt_path)
        else:
            ad_prompt      = report.custom_ad_prompt or ""
            summary_prompt = report.custom_summary_prompt or ""

        results: list[dict] = []
        for i, item in enumerate(ad_data_list):
            logger.debug("[task] %s analyzing ad %d/%d", report_id, i + 1, len(ad_data_list))
            try:
                analysis = await ai_client.analyze_ad(item["data"], ad_prompt)
            except Exception as exc:
                analysis = f"AI ERROR: {exc}"
                logger.warning("[task] ad analysis failed: %s", exc)

            results.append({
                "url":      item["url"],
                "data":     item["data"],
                "analysis": analysis,
            })

            # Update progress after each ad
            async with async_session() as db:
                await crud.update_report(db, report_id, ads_analyzed=i + 1)

            await asyncio.sleep(0.1)  # slight yield to event loop

        # ── 5. AI summary ──────────────────────────────────────────────────────
        logger.info("[task] %s generating summary", report_id)
        try:
            summary_text = await ai_client.analyze_summary(results, summary_prompt)
        except Exception as exc:
            summary_text = json.dumps({"market_summary": f"Summary failed: {exc}",
                                       "price_range": "", "average_price": "", "recommendation": ""})
            logger.warning("[task] summary failed: %s", exc)

        # ── 6. Build output ────────────────────────────────────────────────────
        if report.prompt_preset:
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
        else:
            # Custom prompts — store JSON
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
