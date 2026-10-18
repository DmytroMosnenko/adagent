"""
Regression tests: an Otodom filter search must yield exactly the ads the
site lists for that filter — no promoted (/hpr/) slots, no duplicates, and
no crawling past the site-reported total.

The HTML below mirrors the structure of a real saved Otodom results page
(2026-10): an organic <ul> whose cards each hold several <a> tags to the
same ad (only one has data-cy="listing-item-link"), ad-slot placeholder
<li>s, and a trailing hidden <li> pointing at a /hpr/ promoted ad that does
NOT match the filter.

Browser-backed tests are skipped automatically when Chromium isn't installed.
"""
import pytest
import pytest_asyncio

from service import scraper
from service.scraper import (
    _is_promoted_url,
    _next_page_by_url,
    _parse_total_results,
    _reset_to_first_page,
)

BASE = "https://www.otodom.pl"


# ── Pure helpers ──────────────────────────────────────────────────────────────

class TestParseTotalResults:
    def test_basic_counter(self):
        assert _parse_total_results("1-36 ogłoszeń z 67 ") == 67

    def test_later_page(self):
        assert _parse_total_results("37-67 ogłoszeń z 67") == 67

    def test_does_not_swallow_following_pagination_digits(self):
        assert _parse_total_results("1-36 ogłoszeń z 67\n1\n2\nNastępna") == 67

    def test_thousands_separator_space_and_nbsp(self):
        assert _parse_total_results("1-36 ogłoszeń z 1 234") == 1234
        assert _parse_total_results("1-36 ogłoszeń z 1\u00a0234") == 1234

    def test_en_dash(self):
        assert _parse_total_results("1–36 ogłoszeń z 67") == 67

    def test_absent_or_empty(self):
        assert _parse_total_results("") is None
        assert _parse_total_results(None) is None
        assert _parse_total_results("Mieszkania na sprzedaż: Łódź") is None


class TestPromotedUrl:
    def test_hpr_is_promoted(self):
        assert _is_promoted_url(f"{BASE}/hpr/pl/oferta/mieszkanie-4-pokoje-ID9P5lk5")

    def test_regular_ad_is_not(self):
        assert not _is_promoted_url(f"{BASE}/pl/oferta/2-pokoje-lodz-gorna-ID4DrX0")

    def test_other_sites_unaffected(self):
        assert not _is_promoted_url("https://www.olx.pl/d/oferta/mini-ID123.html")
        assert not _is_promoted_url("https://www.otomoto.pl/oferta/mini-ID456.html")


class TestFilterParamsPreserved:
    def test_next_page_keeps_repeated_params(self):
        url = f"{BASE}/pl/wyniki/sprzedaz/mieszkanie?extras=BALCONY&extras=GARAGE&page=1"
        nxt = _next_page_by_url(url)
        assert "page=2" in nxt
        assert nxt.count("extras=") == 2

    def test_reset_to_first_page_keeps_repeated_params(self):
        url = f"{BASE}/pl/wyniki/sprzedaz/mieszkanie?extras=BALCONY&extras=GARAGE&page=3"
        reset = _reset_to_first_page(url)
        assert "page=" not in reset
        assert reset.count("extras=") == 2


# ── Browser-backed ────────────────────────────────────────────────────────────

def _card(slug: str, hidden: bool = False) -> str:
    href = f"{BASE}{slug}"
    style = ' style="display: none;"' if hidden else ""
    return (
        f"<li{style}><article>"
        f'<a href="{href}"><img alt=""></a>'
        f'<a href="{href}"><img alt=""></a>'
        f'<a href="{href}">1 / 6</a>'
        f'<a data-cy="listing-item-link" href="{href}">Title {slug}</a>'
        f"</article></li>"
    )


def _page(slugs: list[str], hpr_slug: str, counter: str) -> str:
    cards = "".join(_card(s) for s in slugs)
    slot = '<li><div data-cy="baxter-slot-baxter-l-nat1"></div></li>'
    hidden_promoted = _card(hpr_slug, hidden=True)
    return (
        "<html><body>"
        f"<p>{counter}</p>"
        '<div data-cy="search.listing.organic"><ul>'
        f"{cards}{slot}{hidden_promoted}"
        "</ul></div>"
        '<section><a href="https://www.otodom.pl/pl/oferta/outside-container-ID1">x</a></section>'
        "</body></html>"
    )


@pytest_asyncio.fixture()
async def chromium(monkeypatch):
    pw_mod = pytest.importorskip("playwright.async_api")
    # conftest points PLAYWRIGHT_BROWSERS_PATH at a fake dir so no test
    # launches a browser by accident; these tests need a real one, so use
    # Playwright's default location (skip below if nothing is installed).
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    async with pw_mod.async_playwright() as p:
        try:
            browser = await p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:  # browser binary not installed in this env
            pytest.skip(f"Chromium unavailable: {exc}")
        yield browser
        await browser.close()


@pytest.mark.asyncio
async def test_collect_otodom_hrefs_excludes_hidden_promoted_and_outside(chromium):
    page = await chromium.new_page()
    await page.set_content(_page(
        ["/pl/oferta/a-ID1", "/pl/oferta/b-ID2", "/pl/oferta/c-ID3"],
        "/hpr/pl/oferta/mieszkanie-4-pokoje-136-m2-ID9",
        "1-3 ogłoszeń z 3",
    ))

    hrefs = await scraper._collect_otodom_hrefs(page)

    assert hrefs == [f"{BASE}/pl/oferta/a-ID1", f"{BASE}/pl/oferta/b-ID2", f"{BASE}/pl/oferta/c-ID3"]
    assert await scraper._read_otodom_total(page) == 3

    # Documents the original bug: the generic selectors DO pick up the
    # hidden promoted card (and each ad 4x).
    generic = await scraper._collect_listing_hrefs(page, scraper._LISTING_SELECTORS)
    assert any("/hpr/" in h for h in generic)


@pytest.mark.asyncio
async def test_collect_otodom_hrefs_none_when_container_missing(chromium):
    page = await chromium.new_page()
    await page.set_content("<html><body><article><a href='/pl/oferta/x-ID1'>x</a></article></body></html>")
    assert await scraper._collect_otodom_hrefs(page) is None


@pytest.mark.asyncio
async def test_crawl_returns_exactly_the_reported_total(chromium, monkeypatch):
    """
    5-result search over 2 pages, each page carrying its own hidden /hpr/ slot.
    Expect exactly 5 links, no promoted ones, and no request for page 3.
    """
    pages = {
        1: _page(["/pl/oferta/a-ID1", "/pl/oferta/b-ID2", "/pl/oferta/c-ID3"],
                 "/hpr/pl/oferta/promo-one-ID91", "1-3 ogłoszeń z 5"),
        2: _page(["/pl/oferta/d-ID4", "/pl/oferta/e-ID5"],
                 "/hpr/pl/oferta/promo-two-ID92", "4-5 ogłoszeń z 5"),
    }
    requested: list[int] = []

    ctx = await chromium.new_context(locale="pl-PL")

    async def handler(route):
        req = route.request
        if req.is_navigation_request() and req.frame.parent_frame is None:
            import re
            m = re.search(r"[?&]page=(\d+)", req.url)
            n = int(m.group(1)) if m else 1
            requested.append(n)
            await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                body=pages.get(n, pages[2]))
        else:
            await route.abort()

    await ctx.route("**/*", handler)

    class _Pool:
        async def acquire(self): return ctx
        async def release(self, c): pass

    monkeypatch.setattr(scraper, "context_pool", _Pool())
    monkeypatch.setattr(scraper, "_PAGE_BETWEEN_DELAY", 0)

    links, diagnosis = await scraper._collect_all_links_once(
        f"{BASE}/pl/wyniki/sprzedaz/mieszkanie,2-pokoje/lodzkie/lodz?priceMax=250000"
    )

    assert links == [f"{BASE}/pl/oferta/{s}" for s in
                     ("a-ID1", "b-ID2", "c-ID3", "d-ID4", "e-ID5")]
    assert not any("/hpr/" in l for l in links)
    assert requested == [1, 2]          # stopped at the reported total
    assert diagnosis == ""
    await ctx.close()
