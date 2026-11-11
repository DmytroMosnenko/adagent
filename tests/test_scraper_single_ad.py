"""
Single-ad scraping (scrape_ad(single=True) / _extract_one(single=True)) against SAVED HTML
fixtures (tests/fixtures/single_ad/) served through Chromium's request routing — no live
site is ever contacted. Browser-backed: skipped automatically when Chromium isn't installed.

What is pinned here:
  * a real ad page of each site is read (title, price, photo URL),
  * a clearly dead ad fails at once with error_code "gone" (ONE request, no retries):
    HTTP 404/410, a redirect to a search page, a 200 page saying "ogłoszenie nieaktualne",
  * everything transient (403, block page, empty page) keeps the unchanged retry count,
  * filter mode (single=False) behaves exactly as before,
  * the final URL after redirects is re-checked against the allowlist.
"""
from pathlib import Path

import pytest
import pytest_asyncio

from service import scraper

FIX = Path(__file__).parent / "fixtures" / "single_ad"

OLX_URL = "https://www.olx.pl/d/oferta/mini-cooper-s-2018-CID5-ID123abc.html"
OTOMOTO_URL = "https://www.otomoto.pl/osobowe/oferta/mini-paceman-cooper-s-ID6HxYz1.html"
OTODOM_URL = "https://www.otodom.pl/pl/oferta/mieszkanie-3-pokoje-mokotow-ID4abcd"
SEARCH_URL = "https://www.olx.pl/motoryzacja/samochody/"


def _html(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


@pytest_asyncio.fixture()
async def chromium(monkeypatch):
    pw_mod = pytest.importorskip("playwright.async_api")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    async with pw_mod.async_playwright() as p:
        try:
            browser = await p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:  # browser binary not installed in this env
            pytest.skip(f"Chromium unavailable: {exc}")
        yield browser
        await browser.close()


class _Gate:
    async def run(self, owner_id, fn):
        return await fn()


class _FinalUrlPage:
    """A real page whose .url reports where a redirect chain 'ended' (Chromium does not
    re-route a fulfilled 302 offline, so the redirect itself is simulated; what the scraper
    does with the final URL is real)."""
    def __init__(self, page, final_url):
        self._page, self.url = page, final_url
    def __getattr__(self, name):
        return getattr(self._page, name)


class _FinalUrlCtx:
    def __init__(self, ctx, final_url):
        self._ctx, self._final = ctx, final_url
    async def new_page(self):
        return _FinalUrlPage(await self._ctx.new_page(), self._final)


@pytest_asyncio.fixture()
async def serve(chromium, monkeypatch):
    """serve({url: (status, body)}, final_url=None) -> list of navigation requests made"""
    contexts = []

    async def _serve(routes: dict, final_url: str | None = None):
        hits: list[str] = []
        ctx = await chromium.new_context(locale="pl-PL")
        contexts.append(ctx)

        async def handler(route):
            req = route.request
            if not (req.is_navigation_request() and req.frame.parent_frame is None):
                await route.abort()
                return
            hits.append(req.url)
            status, body = routes.get(req.url.split("?")[0].split("#")[0], (404, "<html>nope</html>"))
            await route.fulfill(status=status, content_type="text/html; charset=utf-8", body=body)

        await ctx.route("**/*", handler)

        class _Pool:
            async def acquire(self): return _FinalUrlCtx(ctx, final_url) if final_url else ctx
            async def release(self, c): pass

        monkeypatch.setattr(scraper, "context_pool", _Pool())
        monkeypatch.setattr(scraper, "scrape_gate", _Gate())
        return hits

    # no real waiting between retries / ads, and no debug dumps on disk
    monkeypatch.setattr(scraper, "_RETRY_BASE", 0.0)
    monkeypatch.setattr(scraper, "_AD_BETWEEN_DELAY", 0.0)
    monkeypatch.setattr(scraper.random, "uniform", lambda a, b: 0.0)

    async def _no_dump(*a, **k):
        return "", ""
    monkeypatch.setattr(scraper, "_dump_page_state", _no_dump)

    yield _serve
    for c in contexts:
        await c.close()


# ── real-looking ad pages are read ────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("url,fixture,title_part,price_part", [
    (OLX_URL, "olx_ad.html", "Mini Cooper S", "45 000"),
    (OTOMOTO_URL, "otomoto_ad.html", "Mini Paceman", "38 900"),
    (OTODOM_URL, "otodom_ad.html", "Mieszkanie 3 pokoje", "789 000"),
])
async def test_ad_page_is_scraped(serve, url, fixture, title_part, price_part):
    hits = await serve({url: (200, _html(fixture))})
    data = await scraper.scrape_ad("rid", url, single=True)
    assert "error" not in data, data
    assert title_part in data["title"] and price_part in data["price"]
    assert data["image_url"].startswith("https://ireland.apollo.olxcdn.com/")
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_redirect_to_the_canonical_ad_url_is_fine(serve):
    canonical = "https://www.olx.pl/d/oferta/mini-cooper-s-2018-CID5-ID123abc-canonical.html"
    await serve({OLX_URL: (200, _html("olx_ad.html"))}, final_url=canonical)
    data = await scraper.scrape_ad("rid", OLX_URL, single=True)
    assert "error" not in data and "Mini Cooper" in data["title"]


# ── clearly dead ads fail at once ─────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 410])
async def test_removed_ad_fails_fast_without_retries(serve, status):
    hits = await serve({OLX_URL: (status, _html("gone_404.html"))})
    data = await scraper.scrape_ad("rid", OLX_URL, single=True)
    assert data["error_code"] == "gone"
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_ad_redirecting_to_a_search_page_is_gone_not_scraped_as_the_ad(serve):
    """The reported bug's cousin: the FINAL url is a listing page — never read it as the ad."""
    hits = await serve({OLX_URL: (200, _html("search_page.html"))}, final_url=SEARCH_URL)
    data = await scraper.scrape_ad("rid", OLX_URL, single=True)
    assert data["error_code"] == "gone"
    assert "title" not in data and "page_text" not in data
    assert len(hits) == 1          # no retry


@pytest.mark.asyncio
async def test_expired_banner_on_a_200_page_is_gone(serve):
    hits = await serve({OLX_URL: (200, _html("olx_ad_expired.html"))})
    data = await scraper.scrape_ad("rid", OLX_URL, single=True)
    assert data["error_code"] == "gone"
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_a_seller_writing_nieaktualne_in_the_description_is_not_gone(serve):
    page = _html("olx_ad.html").replace(
        "Cena do negocjacji.", "Cena do negocjacji. " + ("Opis szczegółowy. " * 120) + "Dane w dowodzie nieaktualne.")
    await serve({OLX_URL: (200, page)})
    data = await scraper.scrape_ad("rid", OLX_URL, single=True)
    assert "error" not in data


# ── transient problems: retry behaviour is UNCHANGED (_MAX_RETRIES + 1 attempts) ─

@pytest.mark.asyncio
async def test_http_403_is_retried_like_before_then_blocked(serve):
    hits = await serve({OLX_URL: (403, "<html>forbidden</html>")})
    data = await scraper.scrape_ad("rid", OLX_URL, single=True)
    assert data["error_code"] == "blocked"
    assert len(hits) == scraper._MAX_RETRIES + 1


@pytest.mark.asyncio
async def test_block_page_is_retried_like_before_then_blocked(serve):
    hits = await serve({OTOMOTO_URL: (200, _html("blocked.html"))})
    data = await scraper.scrape_ad("rid", OTOMOTO_URL, single=True)
    assert data["error_code"] == "blocked"
    assert len(hits) == scraper._MAX_RETRIES + 1


@pytest.mark.asyncio
async def test_unreadable_page_is_retried_like_before_then_unparseable(serve):
    hits = await serve({OLX_URL: (200, _html("empty_shell.html"))})
    data = await scraper.scrape_ad("rid", OLX_URL, single=True)
    assert data["error_code"] == "unparseable"
    assert len(hits) == scraper._MAX_RETRIES + 1


# ── allowlist / SSRF protection stays intact ──────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("final", [
    "https://evil.example/d/oferta/x-ID1.html",
    "https://olx.pl.evil.com/d/oferta/x-ID1.html",
    "http://127.0.0.1/d/oferta/x-ID1.html",
])
async def test_redirect_off_the_allowlist_is_refused(serve, final):
    await serve({OLX_URL: (200, _html("olx_ad.html"))}, final_url=final)
    data = await scraper.scrape_ad("rid", OLX_URL, single=True)
    assert data["error_code"] == "invalid_url"
    assert "title" not in data


@pytest.mark.asyncio
async def test_non_allowlisted_start_url_is_never_opened(serve):
    hits = await serve({})
    data = await scraper.scrape_ad("rid", "http://127.0.0.1/d/oferta/x-ID1.html", single=True)
    assert data["error_code"] == "invalid_url"
    assert hits == []


# ── filter mode is untouched ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_filter_mode_does_not_short_circuit_a_404(serve):
    """Only single-ad mode fails a dead ad at once; the per-ad scrape of a search keeps its retries."""
    hits = await serve({OLX_URL: (404, _html("empty_shell.html"))})
    data = await scraper.scrape_ad("rid", OLX_URL)          # single=False (default)
    assert len(hits) == scraper._MAX_RETRIES + 1
    assert data.get("error_code") != "gone"

    hits.clear()
    data = await scraper.scrape_ad("rid", OLX_URL, single=True)
    assert data["error_code"] == "gone" and len(hits) == 1
