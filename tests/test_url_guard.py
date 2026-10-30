"""
URL validation (allowlist, parsed with urlsplit) and the browser-side SSRF guard.
Pure-Python: no DB, no browser.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from service import funnel, scraper, url_guard
from service.schemas import AnalyzeRequest

GOOD = [
    "https://olx.pl/",
    "https://www.olx.pl/motoryzacja/samochody/q-mini/?search%5Bfilter_float_price:to%5D=60000",
    "https://www.otomoto.pl/osobowe/mini",
    "https://www.otodom.pl/pl/wyniki/sprzedaz/mieszkanie/cala-polska",
    "http://m.olx.pl/motoryzacja/",
    "HTTPS://WWW.OLX.PL/auta/",          # scheme/host case-insensitive
    "https://www.olx.pl:443/auta/",      # explicit default port is fine
    "  https://www.olx.pl/auta/  ",      # surrounding whitespace is stripped
]

BAD = [
    ("https://anything/?olx.pl",              "bad_host"),
    ("https://olx.pl.evil.com/",              "bad_host"),
    ("https://evil.com/olx.pl",               "bad_host"),
    ("https://evil.com/?u=https://olx.pl/",   "bad_host"),
    ("https://evil-olx.pl/",                  "bad_host"),
    ("https://notolx.pl/",                    "bad_host"),
    ("https://user@olx.pl@evil.com/",         "userinfo"),
    ("https://user:pass@olx.pl/",             "userinfo"),
    ("https://olx.pl@evil.com/",              "userinfo"),
    ("https://evil.com\\@olx.pl/",            "bad_chars"),
    ("https://olx.pl\\.evil.com/",            "bad_chars"),
    ("http://127.0.0.1/",                     "ip_literal"),
    ("http://127.0.0.1/olx.pl",               "ip_literal"),
    ("http://2130706433/",                    "ip_literal"),
    ("http://0x7f.1/",                        "ip_literal"),
    ("http://[::1]/",                         "ip_literal"),
    ("http://169.254.169.254/latest/meta-data/", "ip_literal"),
    ("https://olx.pl:8443/",                  "bad_port"),
    ("http://olx.pl:443/",                    "bad_port"),
    ("https://olx.pl:abc/",                   "bad_url"),
    ("https://olx.pl:/",                      "bad_port"),
    ("https://olx.pl./",                      "bad_host"),      # trailing dot
    ("https://.olx.pl/",                      "bad_host"),
    ("https://olx.pl\n.evil.com/",            "bad_chars"),
    ("https://olx.pl/a b",                    "bad_chars"),
    ("ftp://olx.pl/",                         "bad_scheme"),
    ("javascript://olx.pl/%0aalert(1)",       "bad_scheme"),
    ("file://olx.pl/etc/passwd",              "bad_scheme"),
    ("//olx.pl/",                             "no_scheme"),
    ("olx.pl/motoryzacja/",                   "no_scheme"),
    ("www.olx.pl",                            "no_scheme"),
    ("https:olx.pl",                          "no_scheme"),
    ("https:///olx.pl",                       "no_host"),
    ("",                                      "empty"),
    ("   ",                                   "empty"),
    ("https://www.olx.pl/" + "a" * 2100,      "too_long"),
]


@pytest.mark.parametrize("url", GOOD)
def test_good_urls_accepted(url):
    assert url_guard.rejection_reason(url) is None
    assert url_guard.validate_url(url) == url.strip()


@pytest.mark.parametrize("url,reason", BAD)
def test_bad_urls_rejected(url, reason):
    assert url_guard.rejection_reason(url) == reason
    assert not url_guard.is_allowed_url(url)
    with pytest.raises(ValueError):
        url_guard.validate_url(url)


def test_required_acceptance_cases_from_spec():
    for u in ("https://anything/?olx.pl", "https://olx.pl.evil.com/", "https://evil.com/olx.pl",
              "https://user@olx.pl@evil.com/", "http://127.0.0.1/"):
        assert not url_guard.is_allowed_url(u)
    for u in ("https://olx.pl/", "https://www.otomoto.pl/", "https://www.otodom.pl/"):
        assert url_guard.is_allowed_url(u)


def test_none_is_rejected():
    assert url_guard.rejection_reason(None) == "empty"


def test_site_of_uses_hostname_not_substring():
    assert url_guard.site_of("https://www.otomoto.pl/oferta/x") == "otomoto"
    assert url_guard.site_of("https://www.otodom.pl/pl/oferta/x") == "otodom"
    assert url_guard.site_of("https://www.olx.pl/d/oferta/x") == "olx"
    assert url_guard.site_of("https://www.olx.pl/d/oferta/?q=otodom.pl") == "olx"
    assert url_guard.site_of("https://evil.com/otomoto.pl") is None
    assert scraper._site("https://www.olx.pl/?ref=otomoto.pl") == "olx"


# ── Schema + funnel use the same rules ────────────────────────────────────────

@pytest.mark.parametrize("url", [u for u, _ in BAD])
def test_schema_rejects_bad_urls(url):
    with pytest.raises(ValidationError):
        AnalyzeRequest(filter_url=url)


@pytest.mark.parametrize("url", GOOD)
def test_schema_accepts_good_urls(url):
    assert AnalyzeRequest(filter_url=url).filter_url == url.strip()


@pytest.mark.parametrize("url,reason", [("https://olx.pl.evil.com/", "bad_host"),
                                        ("https://user@olx.pl@evil.com/", "userinfo"),
                                        ("http://127.0.0.1/", "ip_literal"),
                                        ("https://olx.pl:8080/", "bad_port")])
def test_funnel_reason_codes(url, reason):
    assert funnel.invalid_url_reason(url) == reason
    assert funnel._clean(reason, funnel._DETAIL_RE, 64) == reason     # storable as funnel detail


# ── Browser-side SSRF guard ───────────────────────────────────────────────────

def _safe(url, **kw):
    return asyncio.run(url_guard.request_is_safe(url, **kw))


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://127.1/", "http://2130706433/", "http://0x7f.0.0.1/",
    "http://localhost/", "http://foo.localhost/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
    "http://10.0.0.5/", "http://172.16.3.4/", "http://192.168.1.1/", "http://100.64.0.1/",
    "http://169.254.169.254/latest/meta-data/", "http://[fe80::1]/", "http://[fd00::1]/",
    "http://0.0.0.0/", "http://printer.local/", "http://db.internal/", "http://intranet/",
    "file:///etc/passwd", "ftp://example.com/", "chrome://settings",
])
def test_browser_refuses_non_public_targets(url):
    assert _safe(url, resolve_dns=False) is False


@pytest.mark.parametrize("url", [
    "https://www.olx.pl/", "https://8.8.8.8/", "data:text/plain,hi", "blob:https://www.olx.pl/abc",
    "about:blank", "wss://www.olx.pl/socket",
])
def test_browser_allows_public_and_non_network_targets(url):
    assert _safe(url, resolve_dns=False) is True


def test_dns_resolving_to_private_address_is_refused():
    url_guard._dns_cache.clear()

    async def fake_getaddrinfo(host, *a, **kw):
        ip = {"rebind.example.com": "10.1.2.3", "ok.example.com": "93.184.216.34"}[host]
        return [(2, 1, 6, "", (ip, 0))]

    async def run():
        asyncio.get_running_loop().getaddrinfo = fake_getaddrinfo
        return (await url_guard.request_is_safe("https://rebind.example.com/"),
                await url_guard.request_is_safe("https://ok.example.com/"))

    loop = asyncio.new_event_loop()
    try:
        assert loop.run_until_complete(run()) == (False, True)
    finally:
        loop.close()
        url_guard._dns_cache.clear()


def test_route_handler_aborts_private_requests_and_passes_public(monkeypatch):
    monkeypatch.setattr(scraper.settings, "SCRAPER_PROXY_ADDRESS", "http://proxy:1", raising=False)

    def route_for(url, rtype="document"):
        route = MagicMock()
        route.request.url = url
        route.request.resource_type = rtype
        route.abort = AsyncMock()
        route.continue_ = AsyncMock()
        return route

    private = route_for("http://169.254.169.254/latest/meta-data/", "xhr")
    asyncio.run(scraper._block_unneeded_requests(private))
    private.abort.assert_awaited_once()
    private.continue_.assert_not_awaited()

    loopback = route_for("http://127.0.0.1:8000/admin")
    asyncio.run(scraper._block_unneeded_requests(loopback))
    loopback.abort.assert_awaited_once()

    public = route_for("https://www.olx.pl/d/oferta/x.html")
    asyncio.run(scraper._block_unneeded_requests(public))
    public.continue_.assert_awaited_once()
    public.abort.assert_not_awaited()

    image = route_for("https://www.olx.pl/a.jpg", "image")       # existing behaviour kept
    asyncio.run(scraper._block_unneeded_requests(image))
    image.abort.assert_awaited_once()


# ── Final URL after redirects is re-checked before scraping ───────────────────

def _page_landing_on(final_url):
    page = MagicMock()
    page.url = final_url
    page.goto = AsyncMock(return_value=MagicMock(status=200))
    page.close = AsyncMock()
    page.inner_text = AsyncMock(return_value="SECRET INTERNAL PAGE")
    page.content = AsyncMock(return_value="<html>SECRET</html>")
    page.locator = MagicMock(side_effect=AssertionError("page content must not be read"))
    page.query_selector_all = AsyncMock(side_effect=AssertionError("page content must not be read"))
    return page


def test_extract_one_refuses_ad_that_redirected_off_allowlist():
    page = _page_landing_on("https://evil.com/olx.pl")
    ctx = MagicMock()
    ctx.new_page = AsyncMock(return_value=page)
    data = asyncio.run(scraper._extract_one("https://www.olx.pl/d/oferta/x.html", ctx, TimeoutError))
    assert "error" in data and "title" not in data
    assert ctx.new_page.await_count == 1                          # no retries
    page.close.assert_awaited()


def test_extract_one_refuses_disallowed_input_url_without_opening_a_page():
    ctx = MagicMock()
    ctx.new_page = AsyncMock()
    data = asyncio.run(scraper._extract_one("http://127.0.0.1/", ctx, TimeoutError))
    assert data["error"] == "URL not allowed"
    ctx.new_page.assert_not_awaited()


def test_listing_crawl_refuses_disallowed_filter_url_without_a_browser(monkeypatch):
    pool = MagicMock()
    pool.acquire = AsyncMock(side_effect=AssertionError("no browser expected"))
    monkeypatch.setattr(scraper, "context_pool", pool)
    for bad in ("http://127.0.0.1/", "https://olx.pl.evil.com/", "https://user@olx.pl@evil.com/"):
        links, diagnosis = asyncio.run(scraper._collect_all_links_once(bad))
        assert (links, diagnosis) == ([], "invalid_url")


def test_listing_crawl_refuses_redirect_off_allowlist(monkeypatch):
    page = _page_landing_on("https://evil.com/olx.pl")
    ctx = MagicMock()
    ctx.new_page = AsyncMock(return_value=page)
    pool = MagicMock()
    pool.acquire = AsyncMock(return_value=ctx)
    pool.release = AsyncMock()
    monkeypatch.setattr(scraper, "context_pool", pool)
    links, diagnosis = asyncio.run(scraper._collect_all_links_once("https://www.olx.pl/motoryzacja/"))
    assert (links, diagnosis) == ([], "invalid_url")
    page.inner_text.assert_not_awaited()
    pool.release.assert_awaited_once()


def test_collect_all_links_does_not_retry_invalid_url(monkeypatch):
    calls = []

    async def run(owner, fn):
        calls.append(1)
        return await fn()

    monkeypatch.setattr(scraper.scrape_gate, "run", run)
    sleep = AsyncMock()
    monkeypatch.setattr(scraper.asyncio, "sleep", sleep)
    out = asyncio.run(scraper.collect_all_links("https://olx.pl.evil.com/", "rid"))
    assert out == ([], "invalid_url")
    assert len(calls) == 1
    sleep.assert_not_awaited()
