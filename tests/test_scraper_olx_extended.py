"""
OLX pads a filter's results with an "extended search" list (wider area,
nationwide delivery) and appends it to the pagination. A filter with 65
matches shows 2 pages; on page 2 the paginator suddenly offers pages 3..25 of
the extension list. None of those ads may end up in a report, and the crawl
must not walk those pages.

Markup comes from a real saved page (tests/fixtures/olx_phones_page2_extended.html,
elektronika/telefony/lodz, 2026-11). Browser-backed tests skip when Chromium
isn't installed.
"""
import re
from pathlib import Path

import pytest
import pytest_asyncio

from service import result_scope, scraper

BASE = "https://www.olx.pl"
FIXTURE = Path(__file__).parent / "fixtures" / "olx_phones_page2_extended.html"
START = f"{BASE}/elektronika/telefony/lodz/?search%5Bfilter_enum_state%5D%5B0%5D=used"


@pytest_asyncio.fixture()
async def chromium(monkeypatch):
    pw_mod = pytest.importorskip("playwright.async_api")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    async with pw_mod.async_playwright() as p:
        try:
            browser = await p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        yield browser
        await browser.close()


def _kept(raw_hrefs):
    """What the crawl loop keeps from raw hrefs (same steps, same order)."""
    out = []
    for raw in raw_hrefs:
        absolute = scraper._absolute(raw, BASE)
        full = scraper._clean(absolute)
        if (full and scraper._is_ad(full) and not result_scope.extra_result_reason(absolute)
                and full not in out):
            out.append(full)
    return out


# ── Real markup ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_real_page_two_keeps_only_the_filters_own_results(chromium):
    page = await chromium.new_page()
    await page.set_content(FIXTURE.read_text(encoding="utf-8"))
    kept = _kept(await scraper._collect_listing_hrefs(page, scraper._LISTING_SELECTORS))
    assert len(kept) == 3
    assert all("extended" not in u for u in kept)
    meta = await scraper._read_olx_listing_meta(page)
    assert meta == result_scope.ListingMeta(total=65, exact=True, total_pages=2)


@pytest.mark.asyncio
async def test_real_extension_only_page_yields_nothing(chromium):
    """Page 3+: the organic grid is gone, so there is no second-grid boundary."""
    doc = FIXTURE.read_text(encoding="utf-8")
    first = doc.index('<div data-testid="listing-grid">')
    second = doc.index('<div data-testid="listing-grid">', first + 10)
    msg_start = doc.rfind("<p", 0, second)
    page = await chromium.new_page()
    await page.set_content(doc[:first] + doc[msg_start:])

    raw = await scraper._collect_listing_hrefs(page, scraper._LISTING_SELECTORS)
    assert raw, "extension cards are on the page"
    assert any("extended_search" in r for r in raw)
    assert _kept(raw) == []


# ── Whole crawl ───────────────────────────────────────────────────────────────

def _card(slug, n, extended):
    q = ("?reason=extended_search_extended_delivery&search_reason=search%7Corganic"
         if extended else "?search_reason=search%7Corganic")
    return (f'<div data-cy="l-card" data-testid="l-card" id="{n}"><a href="/d/oferta/{slug}-CID99-ID{n}.html{q}">'
            f'<h4>{slug}</h4></a></div>')


def _page(organic, extended, count_text, state=True, next_page=None, two_grids=True):
    grids = ""
    if organic:
        grids += f'<div data-testid="listing-grid">{"".join(_card(s, n, False) for s, n in organic)}</div>'
    if extended:
        grids += '<p>To wszystko, co znaleźliśmy w tej lokalizacji...</p>' if two_grids else ""
        grids += f'<div data-testid="listing-grid">{"".join(_card(s, n, True) for s, n in extended)}</div>'
    nxt = f'<a data-testid="pagination-forward" href="/elektronika/telefony/lodz/?page={next_page}">n</a>' if next_page else ""
    st = ('<script>window.__PRERENDERED_STATE__ = JSON.stringify({listing:{listing:'
          '{totalElements:5,totalPages:2,ads:[]}}});</script>') if state else ""
    return (f'<html><body><span data-testid="total-count">{count_text}</span>'
            f'{grids}{nxt}{st}</body></html>')


async def _crawl(chromium, monkeypatch, pages):
    requested: list[int] = []
    ctx = await chromium.new_context(locale="pl-PL")

    async def handler(route):
        req = route.request
        if req.is_navigation_request() and req.frame.parent_frame is None:
            m = re.search(r"[?&]page=(\d+)", req.url)
            n = int(m.group(1)) if m else 1
            requested.append(n)
            await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                body=pages.get(n, pages[max(pages)]))
        else:
            await route.abort()

    await ctx.route("**/*", handler)

    class _Pool:
        async def acquire(self): return ctx
        async def release(self, c): pass

    monkeypatch.setattr(scraper, "context_pool", _Pool())
    monkeypatch.setattr(scraper, "_PAGE_BETWEEN_DELAY", 0)
    links, diagnosis = await scraper._collect_all_links_once(START)
    await ctx.close()
    return links, diagnosis, requested


ORG1 = [("a", 1), ("b", 2), ("c", 3)]
ORG2 = [("d", 4), ("e", 5)]
EXT = [("x", 91), ("y", 92), ("z", 93)]
EXPECTED = [f"{BASE}/d/oferta/{s}-CID99-ID{n}.html" for s, n in ORG1 + ORG2]


@pytest.mark.asyncio
async def test_crawl_stops_at_filters_last_page(chromium, monkeypatch):
    pages = {
        1: _page(ORG1, [], "Znaleźliśmy 5 ogłoszeń", next_page=2),
        2: _page(ORG2, EXT, "Znaleźliśmy 5 ogłoszeń", next_page=3),   # paginator leaks into extension
        3: _page([], EXT, "Znaleźliśmy 5 ogłoszeń", next_page=4, two_grids=False),
    }
    links, diagnosis, requested = await _crawl(chromium, monkeypatch, pages)
    assert links == EXPECTED
    assert requested == [1, 2]
    assert diagnosis == ""


@pytest.mark.asyncio
async def test_crawl_without_any_meta_still_excludes_extension(chromium, monkeypatch):
    """No state, no counter: the reason= marker alone must keep extras out."""
    pages = {
        1: _page(ORG1, [], "", state=False, next_page=2),
        2: _page(ORG2, EXT, "", state=False, next_page=3),
        3: _page([], EXT, "", state=False, next_page=4, two_grids=False),
    }
    links, _, requested = await _crawl(chromium, monkeypatch, pages)
    assert links == EXPECTED
    assert requested == [1, 2, 3]       # page 3 fetched, yielded nothing -> stop


@pytest.mark.asyncio
async def test_next_page_keeps_filter_and_page_params(chromium, monkeypatch):
    """The forward button's href is all query (?page=N + filters); it must survive."""
    seen: list[str] = []
    ctx = await chromium.new_context(locale="pl-PL")

    async def handler(route):
        req = route.request
        if req.is_navigation_request() and req.frame.parent_frame is None:
            seen.append(req.url)
            n = 2 if "page=2" in req.url else 1
            body = (_page(ORG1, [], "Znaleźliśmy 5 ogłoszeń", state=False, next_page=None)
                    .replace("</body>", '<a data-testid="pagination-forward" href="/elektronika/telefony/lodz/'
                            '?page=2&amp;search%5Bfilter_enum_state%5D%5B0%5D=used">n</a></body>')
                    if n == 1 else
                    _page(ORG2, [], "Znaleźliśmy 5 ogłoszeń", state=False))
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)
        else:
            await route.abort()

    await ctx.route("**/*", handler)

    class _Pool:
        async def acquire(self): return ctx
        async def release(self, c): pass

    monkeypatch.setattr(scraper, "context_pool", _Pool())
    monkeypatch.setattr(scraper, "_PAGE_BETWEEN_DELAY", 0)
    await scraper._collect_all_links_once(START)
    await ctx.close()

    assert "page=2" in seen[1]
    assert "filter_enum_state" in seen[1]
