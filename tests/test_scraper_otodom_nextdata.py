"""
Otodom results are read from the server-rendered __NEXT_DATA__, not the live DOM,
because Otodom injects extra cards client-side after hydration (random "top ads",
audience-plus / nearby adverts) — a race that made every crawl return a different,
bigger set. The fixture is trimmed from a real saved page 2 (2026-11): 9 items =
8 real + 1 hidden sentinel, pagination 44 results / 2 pages.
"""
import json
import pathlib
import re

import pytest
import pytest_asyncio

from service import scraper
from service.scraper import _otodom_links_from_next_data

BASE = "https://www.otodom.pl"
FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "otodom_next_data_page2.json"
FILTER = (f"{BASE}/pl/wyniki/sprzedaz/mieszkanie%2C2-pokoje/lodzkie/lodz/lodz/lodz"
          "?distanceRadius=10&limit=36&priceMin=170000&priceMax=240000&areaMin=29")


def _data():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_real_page_yields_exactly_the_8_real_ads():
    urls, pag = _otodom_links_from_next_data(_data(), BASE, FILTER)
    assert len(urls) == 8
    assert urls[0] == f"{BASE}/pl/oferta/40m-do-wlasnej-aranzacji-swietna-lokalizacja-2pok-ID4CDic"
    assert not any("/hpr/" in u for u in urls)
    assert len(set(urls)) == 8
    assert pag == {"itemsPerPage": 36, "totalItems": 44, "totalPages": 2, "currentPage": 2}


def test_sentinel_is_dropped_even_without_hpr_prefix():
    d = _data()
    item = d["props"]["pageProps"]["data"]["searchAds"]["items"][-1]
    item["href"] = "[lang]/ad/whatever-ID1"        # only id / date identify it now
    urls, _ = _otodom_links_from_next_data(d, BASE, FILTER)
    assert len(urls) == 8


def test_item_priced_above_filter_is_dropped():
    d = _data()
    d["props"]["pageProps"]["data"]["searchAds"]["items"][0]["totalPrice"]["value"] = 650000
    urls, _ = _otodom_links_from_next_data(d, BASE, FILTER)
    assert len(urls) == 7
    assert not any("ID4CDic" in u for u in urls)


def test_hidden_price_is_not_dropped():
    d = _data()
    it = d["props"]["pageProps"]["data"]["searchAds"]["items"][0]
    it["totalPrice"]["value"], it["hidePrice"] = 650000, True
    urls, _ = _otodom_links_from_next_data(d, BASE, FILTER)
    assert len(urls) == 8


def test_unexpected_structure_returns_none():
    assert _otodom_links_from_next_data({"props": {}}, BASE, FILTER) is None
    assert _otodom_links_from_next_data(None, BASE, FILTER) is None


# ── Browser-backed: injected DOM ads and a junk page 3 must be ignored ─────────

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


def _page_html(items, page, total_pages, total_items, junk_dom):
    nd = {"props": {"pageProps": {"data": {"searchAds": {
        "items": items,
        "pagination": {"itemsPerPage": 3, "totalItems": total_items,
                       "totalPages": total_pages, "currentPage": page}}}}}}
    cards = "".join(
        f'<li><article><a data-cy="listing-item-link" href="{BASE}/pl/oferta/{s}">{s}</a></article></li>'
        for s in junk_dom)
    return ("<html><body><p>%d-%d ogłoszeń z %d</p>"
            '<div data-cy="search.listing.organic"><ul>%s</ul></div>'
            '<script id="__NEXT_DATA__" type="application/json">%s</script>'
            "</body></html>" % (1, 1, total_items, cards, json.dumps(nd)))


def _item(slug, price=200000):
    return {"id": abs(hash(slug)) % 10**7, "href": f"[lang]/ad/{slug}", "slug": slug,
            "dateCreated": "2026-10-01T00:00:00Z", "totalPrice": {"value": price},
            "areaInSquareMeters": 40}


@pytest.mark.asyncio
async def test_crawl_ignores_injected_dom_ads_and_never_requests_past_last_page(chromium, monkeypatch):
    junk = ["top-ad-1-ID91", "top-ad-2-ID92", "nearby-3-ID93"]   # injected, pricey, not in __NEXT_DATA__
    pages = {
        1: _page_html([_item("a-ID1"), _item("b-ID2"), _item("c-ID3")], 1, 2, 5, junk),
        2: _page_html([_item("d-ID4"), _item("e-ID5")], 2, 2, 5, junk),
    }
    requested = []
    ctx = await chromium.new_context(locale="pl-PL")

    async def handler(route):
        req = route.request
        if req.is_navigation_request() and req.frame.parent_frame is None:
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
        f"{BASE}/pl/wyniki/sprzedaz/mieszkanie,2-pokoje/lodzkie/lodz?priceMax=250000")

    assert links == [f"{BASE}/pl/oferta/{s}" for s in ("a-ID1", "b-ID2", "c-ID3", "d-ID4", "e-ID5")]
    assert requested == [1, 2]
    assert diagnosis == ""
    await ctx.close()
