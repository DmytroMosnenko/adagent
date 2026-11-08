"""
Which listing-page results actually belong to the user's filter.

Marketplaces pad a filtered result set with cards that do NOT match the filter
but use the same card markup as real matches:

  * OLX "extended search" — once the real matches run out (or when there are
    few), OLX appends ads from a wider area ("extended_distance"), ads that can
    be shipped from anywhere in the country ("extended_delivery"), spell-checked
    or description-only matches, ... Every one of those links carries
    ``?reason=extended_search_<kind>``. The block is also appended to the
    PAGINATION: a filter with 65 matches (2 pages) shows pages 1-2, but on
    page 2 the paginator suddenly offers pages 3..25 of the extension list.
  * Otodom sponsored slots under a ``/hpr/`` path prefix.

Everything here is pure (no browser, no I/O) and category-agnostic — vehicles,
real estate, phones or any future OLX category behave the same way — so the
rules can be unit-tested and extended in one place. scraper.py applies them.

Adding a rule for a new kind of padding: write a small ``(url_parts, query) ->
reason | None`` function and add it to ``_URL_RULES``. Return a short stable
reason string; it only ends up in logs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import ParseResult, parse_qs, urlparse


# ── 1. Per-link rules ─────────────────────────────────────────────────────────
#
# These MUST be evaluated on the raw, absolute href — before any query-string
# stripping (scraper._clean drops everything after "?", which is exactly where
# OLX puts its marker).

# Otodom serves paid "highlighted promoted" slots under /hpr/ (e.g.
# https://www.otodom.pl/hpr/pl/oferta/...). They are injected regardless of the
# user's filter — a 4-room flat on a "2 rooms" search — and are not matches.
_PROMOTED_URL_RE = re.compile(r"^https?://[^/]*otodom\.pl/hpr/", re.IGNORECASE)


def _rule_promoted_path(url: str, parts: ParseResult, query: dict) -> Optional[str]:
    return "promoted" if _PROMOTED_URL_RE.match(url) else None


def _rule_extended_search(url: str, parts: ParseResult, query: dict) -> Optional[str]:
    """OLX: reason=extended_search_extended_distance / _extended_delivery / ..."""
    for value in query.get("reason", []):
        if value.lower().startswith("extended_search"):
            return value.lower()
    return None


_URL_RULES: tuple[Callable[[str, ParseResult, dict], Optional[str]], ...] = (
    _rule_promoted_path,
    _rule_extended_search,
)


def extra_result_reason(url: str) -> Optional[str]:
    """
    Why this result link is NOT a genuine match for the user's filter, or None
    when it looks like a real match (or when nothing is known against it —
    unknown links are kept, never guessed away).
    """
    if not url:
        return None
    parts = urlparse(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    for rule in _URL_RULES:
        reason = rule(url, parts, query)
        if reason:
            return reason
    return None


# ── 2. The site's own idea of how big the real result set is ──────────────────

# "Znaleźliśmy 65 ogłoszeń" / "Znaleźliśmy ponad 1000 ogłoszeń" (note the
# occasional double space). Thousands may be separated by a space / nbsp.
_COUNT_RE = re.compile(
    r"znaleźliśmy\s+(ponad\s+)?(\d+(?:[ \u00a0]\d{3})*)\s+ogłosz",
    re.IGNORECASE,
)

# OLX stops counting (and paginating) at 1000: "ponad 1000" is a floor, not the
# real total, and the state's totalElements is capped at the same number.
_OLX_COUNT_CAP = 1000


def parse_result_count(text: Optional[str]) -> Optional[tuple[int, bool]]:
    """(count, is_exact) from the on-page counter, or None. 'ponad N' is inexact."""
    m = _COUNT_RE.search(text or "")
    if not m:
        return None
    n = int(re.sub(r"[ \u00a0]", "", m.group(2)))
    return n, m.group(1) is None


@dataclass(frozen=True)
class ListingMeta:
    """
    What the site says about the filter's OWN results (extension lists excluded).

    total        number of real matches, or None when unknown.
    exact        False when `total` is only a lower bound ("ponad 1000") —
                 never use an inexact total as a stop condition.
    total_pages  number of pages the real matches span, or None when unknown.
    """
    total: Optional[int]
    exact: bool
    total_pages: Optional[int]


def listing_meta(probe: Optional[dict]) -> Optional[ListingMeta]:
    """
    Build ListingMeta from what scraper._read_olx_listing_meta pulled out of the
    page: ``{"total", "totalPages"}`` from the server-rendered listing state and
    ``{"countText"}`` from the visible counter. The state wins; the counter is
    the fallback when the state's shape changes.
    """
    if not isinstance(probe, dict):
        return None

    def _int(v) -> Optional[int]:
        return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None

    total = _int(probe.get("total"))
    pages = _int(probe.get("totalPages")) or None      # 0 pages == unknown
    counted = parse_result_count(probe.get("countText"))

    exact = True
    if counted is not None:
        exact = counted[1]
        if total is None:
            total = counted[0]
    if total is not None and total >= _OLX_COUNT_CAP:
        exact = False

    if total is None and pages is None:
        return None
    return ListingMeta(total=total, exact=exact, total_pages=pages)


def is_last_page(page_number: int, meta: Optional[ListingMeta]) -> bool:
    """True once `page_number` is the last page of the filter's own results."""
    return bool(meta and meta.total_pages and page_number >= meta.total_pages)
