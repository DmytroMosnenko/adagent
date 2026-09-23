from __future__ import annotations
import re
from datetime import datetime
from typing import Optional
from markupsafe import Markup, escape
from .ai_client import parse_json_safe
from .logger import get_logger

from .config import settings
logger = get_logger(__name__)

_REF_PAIR_RE = re.compile(r"\{\{ref:([^}]*)\}\}(.*?)\{\{/ref\}\}", re.S)
_REF_STRAY_RE = re.compile(r"\{\{/?ref[^}]*\}\}")

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


def _rating_color(r: float) -> str:
    if r >= 7: return "#16a34a"
    if r >= 5: return "#d97706"
    return "#dc2626"


def _site_label(url: str) -> str:
    if "otomoto.pl" in url: return "Otomoto"
    if "otodom.pl"  in url: return "Otodom"
    return "OLX"


def _detect_category(specs: dict) -> str:
    """
    Which vertical this ad belongs to, based on which spec fields the AI
    populated. Used by the template to pick the right deep_dive labels/icons
    (vehicle vs real estate) without needing a separate field in the JSON
    schema the AI has to fill in — specs already disambiguate this reliably.
    """
    if specs.get("property_type") or specs.get("area_m2") or specs.get("rooms"):
        return "realestate"
    if specs.get("make") or specs.get("model") or specs.get("mileage_km"):
        return "vehicle"
    return "other"


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
        add("Location", specs.get("district")),
        {"label": "Area", "value": f"{specs['area_m2']} m²"} if specs.get("area_m2") else None,
        add("Rooms",    specs.get("rooms")),
        add("Floor",    specs.get("floor")),
        add("Built",    specs.get("year_built")),
        add("Condition",specs.get("condition", "").replace("_", " ").capitalize() if specs.get("condition") else None),
        add("Heating",  specs.get("heating")),
        {"label": "Elevator", "value": "Yes"} if specs.get("elevator") is True else
        ({"label": "Elevator", "value": "No"} if specs.get("elevator") is False else None),
        add("Parking",  specs.get("parking", "").replace("_", " ").capitalize() if specs.get("parking") else None),
        add("Monthly fees", specs.get("monthly_fees")),
    ]
    return [c for c in candidates if c]


def _fallback_analysis(raw: str) -> dict:
    return {
        "rating": 5.0, "verdict": "unknown", "verdict_note": "",
        "summary": (raw or "")[:400], "specs": {}, "asking_price": None,
        "price_assessment": "unknown", "price_per_m2": None,
        "red_flags": [], "positives": [], "deep_dive": {},
    }


def _fallback_summary() -> dict:
    return {
        "market_summary": "", "price_range": "", "average_price": "",
        "average_price_m2": None, "recommendation": "", "recommended_ads": [],
    }


def _norm_url(url: str) -> str:
    """Normalize a URL for equality checks (dedup + recommendation matching)."""
    return (url or "").split("?")[0].rstrip("/")


def _build_recommended_links(recommended_ads: list, ads: list[dict], limit: int = 3) -> list[dict]:
    """
    Match the AI's {label, url} pairs against the final deduped + rating-sorted
    `ads` list, so links always point at an ad's real, currently-displayed rank
    — never a stale number from before sorting. `label` must be the literal
    substring used in the recommendation prose, so _linkify_recommendation can
    find and wrap it; entries with no matching ad or empty label are dropped.
    """
    by_url = {_norm_url(ad["url"]): (i, ad) for i, ad in enumerate(ads)}
    links, seen = [], set()
    for item in (recommended_ads or []):
        if not isinstance(item, dict):
            continue
        label = (item.get("label") or "").strip()
        key = _norm_url(item.get("url") if isinstance(item.get("url"), str) else "")
        match = by_url.get(key)
        if not label or not match or key in seen:
            continue
        seen.add(key)
        idx, ad = match
        links.append({
            "ad_anchor": f"ad-{idx}", "lb_anchor": f"lb-{idx}",
            "rank": idx + 1, "title": ad["title"], "label": label,
        })
        if len(links) >= limit:
            break
    return links


def _title_candidates(ad: dict, ambiguous_districts: set[str]) -> list[str]:
    """
    Candidate substrings to look for in the recommendation text when an ad has
    no explicit {label, url} entry from the AI — most specific first.

    Vehicles: uses the AI-extracted make/model rather than the raw scraped
    title, since the prose tends to say "Nissan Maxima", not the full messy
    ad headline.

    Real estate: make/model are empty, so falls back to the AI-extracted
    district. Unlike a car's make/model, a district is often shared by many
    ads in the same batch (e.g. ten listings all in "Śródmieście") — linking
    on an ambiguous district would point at an arbitrary one of them, so
    `ambiguous_districts` (precomputed by the caller across the whole batch)
    excludes any district string that isn't unique to this ad.
    """
    make, model = ad.get("make", ""), ad.get("model", "")
    candidates = []
    if make and model:
        candidates.append(f"{make} {model}")
        first_word = model.split()[0] if model.split() else ""
        if first_word and first_word != model:
            candidates.append(f"{make} {first_word}")
    elif model:
        candidates.append(model)

    district = ad.get("district", "")
    if district:
        # a district often reads as "City, District" — try the more specific
        # full form first, then just the neighborhood part
        last_part = district.split(",")[-1].strip()
        for candidate in (district, last_part):
            if candidate and candidate not in ambiguous_districts and candidate not in candidates:
                candidates.append(candidate)

    return candidates


def _linkify_recommendation(recommendation: str, recommended_links: list[dict], ads: list[dict], results: list[dict]) -> Markup:
    """
    Turn each recommended ad's mention inside the recommendation prose into an
    inline jump-link, instead of a separate row of buttons. Escapes the whole
    text first (untrusted AI output), then resolves links in priority order
    against that ORIGINAL escaped text — before building the final string in
    one pass. Spans are never allowed to overlap: once a stretch of text is
    claimed, a later match that falls inside it is skipped rather than
    wrapped a second time (matching against text already mutated by an
    earlier replacement used to nest anchors and stack up duplicate arrow
    spans — this collect-then-splice-once approach can't do that).

    Three linking mechanisms, tried in order, each covering ads the previous
    one missed:

    1. {{ref:N}}...{{/ref}} marker pairs — the AI wraps its own descriptive
       mention of a listing in these (see realestate_summary.txt), where N is
       the ad's 1-indexed position in `results`, i.e. its "Ad #N" position in
       the exact batch order the summary call was given. The wrapped text
       becomes the visible, underlined link label (same look as the vehicle
       recommended_ads links) and N resolves to a URL, not a text label, so
       it can't be broken by translation, a different grammatical case, or an
       ambiguous shared district — the failure modes that make free-text
       label matching fundamentally unreliable once the report's language
       differs from the ad's own language. A pair whose N doesn't resolve
       (malformed, out of range, no matching ad) still keeps its wrapped text
       visible, just unlinked — the marker tags are stripped either way, so
       nothing like "{{ref:9}}" can ever leak into what the buyer reads.
    2. Explicit {label, url} pairs from recommended_ads (vehicles; legacy
       fallback for any other preset, harmless when the array is absent).
       A label that isn't found verbatim (the model paraphrased instead of
       copying it) is silently skipped.
    3. Any ad still unlinked: checked against the leftover text using its own
       make/model (vehicles) or district (real estate, only when unique in
       this batch — a district shared by several ads can't safely be guessed,
       so those are left unlinked rather than pointing at an arbitrary one).

    The AI sometimes names an ad in the prose but forgets to wrap it / give it
    a recommended_ads entry (seen in practice). Mechanisms 2 and 3 exist to
    still catch that rather than leaving silently dead text.
    """
    text = str(escape(recommendation or ""))
    if not text:
        return Markup(text)

    spans: list[tuple[int, int, str]] = []
    linked_anchors: set[str] = set()

    def claim_span(start: int, end: int, replacement: str, ad_anchor: str | None) -> bool:
        for s, e, _ in spans:
            if start < e and s < end:    # overlaps a span already claimed
                return False
        spans.append((start, end, replacement))
        if ad_anchor:
            linked_anchors.add(ad_anchor)
        return True

    def claim_label(label: str, ad_anchor: str, lb_anchor: str, rank: int) -> bool:
        esc_label = str(escape(label))
        if not esc_label:
            return False
        idx = text.find(esc_label)
        if idx == -1:
            return False
        anchor = (
            f'<a href="#{ad_anchor}" class="rec-inline-link" '
            f'data-lb-id="{lb_anchor}" title="Jump to ad #{rank}">'
            f'{esc_label}<span class="rec-inline-arrow"> ↓</span></a>'
        )
        return claim_span(idx, idx + len(esc_label), anchor, ad_anchor)

    # 1. {{ref:N}}...{{/ref}} pairs. The wrapped text is kept either way (as
    # a link when N resolves, as plain unwrapped text when it doesn't) — only
    # the marker tags themselves are ever removed.
    by_url = {_norm_url(ad["url"]): (i, ad) for i, ad in enumerate(ads)}
    for m in _REF_PAIR_RE.finditer(text):
        n_str, inner = m.group(1), _REF_STRAY_RE.sub("", m.group(2))
        ad_anchor = None
        replacement = inner  # default: unwrap, keep the text, drop the tags
        if n_str.isdigit():
            n = int(n_str)
            if 1 <= n <= len(results):
                match = by_url.get(_norm_url(results[n - 1].get("url", "")))
                if match:
                    idx, _ad = match
                    ad_anchor = f"ad-{idx}"
                    replacement = (
                        f'<a href="#{ad_anchor}" class="rec-inline-link" '
                        f'data-lb-id="lb-{idx}" title="Jump to ad #{idx + 1}">'
                        f'{inner}<span class="rec-inline-arrow"> ↓</span></a>'
                    )
        claim_span(m.start(), m.end(), replacement, ad_anchor)

    # 2. recommended_ads label matches.
    for link in sorted(recommended_links, key=lambda l: -len(l["label"])):
        if link["ad_anchor"] in linked_anchors:
            continue
        claim_label(link["label"], link["ad_anchor"], link["lb_anchor"], link["rank"])

    # 3. make/model or (unique) district fallback.
    # Districts shared by more than one ad in this batch can't be used to
    # safely guess which ad a bare district mention refers to.
    district_counts: dict[str, int] = {}
    for ad in ads:
        for d in {ad.get("district", ""), (ad.get("district", "") or "").split(",")[-1].strip()}:
            if d:
                district_counts[d] = district_counts.get(d, 0) + 1
    ambiguous_districts = {d for d, n in district_counts.items() if n > 1}

    for idx, ad in enumerate(ads):
        ad_anchor = f"ad-{idx}"
        if ad_anchor in linked_anchors:
            continue
        for candidate in _title_candidates(ad, ambiguous_districts):
            if claim_label(candidate, ad_anchor, f"lb-{idx}", idx + 1):
                break

    if not spans:
        return Markup(_REF_STRAY_RE.sub("", text))

    spans.sort(key=lambda s: s[0])
    out, cursor = [], 0
    for start, end, anchor in spans:
        if start < cursor:
            continue  # extra safety net; shouldn't happen given claim_span()'s check
        out.append(_REF_STRAY_RE.sub("", text[cursor:start]))
        out.append(anchor)
        cursor = end
    out.append(_REF_STRAY_RE.sub("", text[cursor:]))
    return Markup("".join(out))




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
            if specs.get("district"):
                title += f" — {specs['district']}"
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

    rating  = max(1, min(10, float(p.get("rating") or 5.0)))
    verdict = p.get("verdict") or "unknown"
    price_a = p.get("price_assessment") or "unknown"
    url     = result.get("url", "")
    deep_dive = p.get("deep_dive") or {}

    return {
        "url":                    url,
        "site_label":             _site_label(url),
        "category":               _detect_category(specs),
        "title":                  title,
        "make":                   (specs.get("make") or "").strip(),
        "model":                  (specs.get("model") or "").strip(),
        "district":               (specs.get("district") or "").strip(),
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
        key = _norm_url(r.get("url", ""))
        if key and key not in seen:
            seen.add(key)
            unique.append(r)

    ads = [_prepare_ad(r) for r in unique]
    ads.sort(key=lambda a: a["rating"], reverse=True)

    recommended_links = _build_recommended_links(summary.get("recommended_ads"), ads)

    env = Environment(loader=FileSystemLoader(template_dir), autoescape=True)
    env.filters["thousands"] = lambda v: f"{int(v):,}" if v else ""

    ctx = {
        "report_id":      report_id,
        "adagent_url":    settings.APP_BASE_URL,
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
        "recommendation": _linkify_recommendation(summary.get("recommendation", ""), recommended_links, ads, results),
    }
    return env.get_template(template_file).render(**ctx)
