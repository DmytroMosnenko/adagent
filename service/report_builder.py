from __future__ import annotations
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from .ai_client import parse_json_safe
from .logger import get_logger

logger = get_logger(__name__)

_VERDICT_LABELS = {
    "worth_viewing": "Worth Viewing",
    "maybe":         "Maybe",
    "skip":          "Skip",
    "unknown":       "Unknown",
}
_PRICE_LABELS = {
    "fair": "Fair price", "high": "High price",
    "low":  "Low price",  "unknown": "",
}


def _rating_color(r: int) -> str:
    if r >= 7: return "#16a34a"
    if r >= 5: return "#d97706"
    return "#dc2626"


def _site_label(url: str) -> str:
    if "otomoto.pl" in url: return "Otomoto"
    if "otodom.pl"  in url: return "Otodom"
    return "OLX"


def _build_spec_items(specs: dict) -> list[dict]:
    def add(label: str, val) -> Optional[dict]:
        if val is None or val == "":
            return None
        return {"label": label, "value": str(val)}

    candidates = [
        # Vehicle
        add("Make",     specs.get("make")),
        add("Model",    specs.get("model")),
        add("Year",     specs.get("year")),
        {"label": "Mileage",  "value": f"{int(specs['mileage_km']):,} km"} if specs.get("mileage_km") else None,
        add("Engine",   specs.get("engine")),
        add("Fuel",     specs.get("fuel")),
        add("Gearbox",  specs.get("gearbox")),
        add("Body",     specs.get("body_type", "").replace("_", " ").capitalize() if specs.get("body_type") else None),
        add("Color",    specs.get("color")),
        # Real estate
        add("Type",     specs.get("property_type", "").replace("_", " ").capitalize() if specs.get("property_type") else None),
        {"label": "Area", "value": f"{specs['area_m2']} m²"} if specs.get("area_m2") else None,
        add("Rooms",    specs.get("rooms")),
        add("Floor",    specs.get("floor")),
        add("Built",    specs.get("year_built")),
        add("Condition",specs.get("condition", "").replace("_", " ").capitalize() if specs.get("condition") else None),
        add("Heating",  specs.get("heating")),
        add("Parking",  specs.get("parking", "").replace("_", " ").capitalize() if specs.get("parking") else None),
    ]
    return [c for c in candidates if c]


def _fallback_analysis(raw: str) -> dict:
    return {
        "rating": 5, "verdict": "unknown", "verdict_note": "",
        "summary": (raw or "")[:400], "specs": {}, "asking_price": None,
        "price_assessment": "unknown", "price_per_m2": None,
        "red_flags": [], "positives": [], "deep_dive": {},
    }


def _fallback_summary() -> dict:
    return {
        "market_summary": "", "price_range": "", "average_price": "",
        "average_price_m2": None, "recommendation": "",
    }




def _short_reason(verdict_note: str) -> str:
    """
    Return the full verdict_note for the leaderboard micro-description.
    Truncation (with '…') was removed — the template now uses CSS
    text-overflow:ellipsis for display and a tooltip (title attribute)
    to reveal the full text on hover.
    """
    return verdict_note.strip()


def _parse_raw_parameters(params_text: str) -> list[dict]:
    """Parse OLX/Otomoto parameter strings 'Label: Value\n...' into spec items."""
    items = []
    for line in (params_text or "").splitlines():
        if ":" in line:
            label, _, value = line.partition(":")
            label, value = label.strip(), value.strip()
            if label and value:
                items.append({"label": label, "value": value})
    return items


def _prepare_ad(result: dict) -> dict:
    """Convert a raw pipeline result into a Jinja2-ready dict."""
    raw_analysis = result.get("analysis", "")
    p   = parse_json_safe(raw_analysis, _fallback_analysis(raw_analysis))
    raw = result.get("data", {})
    specs = p.get("specs") or {}

    # Title: always prefer the original scraped title.
    # AI-built titles like "Apartment 32.0 m²" don't match the real ad headline.
    title = (raw.get("title") or "").strip()
    if not title:
        # Fallback only when scraper found nothing
        if specs.get("make") or specs.get("model"):
            parts = [specs.get("make", ""), specs.get("model", "")]
            if specs.get("year"):
                parts.append(str(specs["year"]))
            title = " ".join(x for x in parts if x)
        elif specs.get("property_type"):
            title = specs["property_type"].replace("_", " ").capitalize()
            if specs.get("area_m2"):
                title += f" {specs['area_m2']} m²"
        else:
            title = result.get("url", "Unknown ad")
    if len(title) > 90:
        title = title[:87] + "…"

    # Spec items: AI extraction first; supplement with raw params for fields
    # the AI missed (e.g. mileage present in the original parameters text but
    # not extracted into the JSON schema).
    ai_items  = _build_spec_items(specs)
    raw_items = _parse_raw_parameters(raw.get("parameters", ""))
    if not ai_items:
        spec_items = raw_items                   # AI found nothing → show everything raw
    elif raw_items:
        ai_labels  = {i["label"].lower() for i in ai_items}
        spec_items = ai_items + [              # merge, no duplicates
            i for i in raw_items if i["label"].lower() not in ai_labels
        ]
    else:
        spec_items = ai_items

    rating  = max(1, min(10, int(p.get("rating") or 5)))
    verdict = p.get("verdict") or "unknown"
    price_a = p.get("price_assessment") or "unknown"
    url     = result.get("url", "")
    deep_dive = p.get("deep_dive") or {}

    return {
        "url":                    url,
        "site_label":             _site_label(url),
        "title":                  title,
        "rating":                 rating,
        "rating_pct":             rating * 10,
        "rating_color":           _rating_color(rating),
        "verdict":                verdict,
        "verdict_label":          _VERDICT_LABELS.get(verdict, verdict),
        "verdict_note":           p.get("verdict_note") or "",
        "short_reason":           _short_reason(p.get("verdict_note") or ""),
        "summary":                p.get("summary") or "",
        "spec_items":             spec_items,
        "asking_price":           p.get("asking_price") or raw.get("price") or "",
        "price_per_m2":           p.get("price_per_m2") or "",
        "price_assessment":       price_a,
        "price_assessment_label": _PRICE_LABELS.get(price_a, ""),
        "red_flags":              p.get("red_flags") or [],
        "positives":              p.get("positives") or [],
        "deep_dive": {
            "general_specs":          deep_dive.get("general_specs") or "",
            "mentioned_fixes":        deep_dive.get("mentioned_fixes") or [],
            "known_weak_points":      deep_dive.get("known_weak_points") or "",
            "real_world_consumption": deep_dive.get("real_world_consumption") or "",
        },
        "raw": {
            "title":       raw.get("title", ""),
            "price":       raw.get("price", ""),
            "location":    raw.get("location", ""),
            "parameters":  raw.get("parameters", ""),
            "description": raw.get("description", ""),
            # page_text is the full visible text captured from the page.
            # Shown in the raw section when CSS selectors missed the description
            # (common on Otomoto / Otodom whose DOM layout varies by language).
            "page_text":   raw.get("page_text", "")[:3_000],
            "error":       raw.get("error", ""),
        },
    }


def build_html_report(
    results: list[dict],
    summary_text: str,
    filter_url: str,
    report_id: str,
    ads_found: int,
    is_limited: bool,
    template_dir: str = "templates",
    template_file: str = "report.html.j2",
) -> str:
    """Render Jinja2 template → return HTML string (caller writes the file)."""
    try:
        from jinja2 import Environment, FileSystemLoader
    except ImportError:
        logger.error("jinja2 not installed")
        raise

    summary = parse_json_safe(summary_text, _fallback_summary())

    # Deduplicate by normalized URL
    seen: set[str] = set()
    unique: list[dict] = []
    for r in results:
        key = r.get("url", "").split("?")[0].rstrip("/")
        if key and key not in seen:
            seen.add(key)
            unique.append(r)

    ads = [_prepare_ad(r) for r in unique]
    ads.sort(key=lambda a: a["rating"], reverse=True)

    env = Environment(loader=FileSystemLoader(template_dir), autoescape=True)
    env.filters["thousands"] = lambda v: f"{int(v):,}" if v else ""

    ctx = {
        "report_id":      report_id,
        "adagent_url":    "https://adagent.dimosense.com",  # will be overridden by config at runtime
        "subscribe_url":  "/subscribe",
        "filter_url":     filter_url,
        "filter_site":    _site_label(filter_url),
        "generated_at":   datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_ads":      len(ads),
        "ads_found":      ads_found,
        "is_limited":     is_limited,
        "ads":            ads,
        "market_summary": summary.get("market_summary", ""),
        "price_range":    summary.get("price_range", ""),
        "average_price":  summary.get("average_price", ""),
        "average_price_m2": summary.get("average_price_m2", ""),
        "recommendation": summary.get("recommendation", ""),
    }
    return env.get_template(template_file).render(**ctx)
