"""
Ad-thumbnail extraction (scraper._extract_image_url / _valid_image_url /
_resize_image_url).

What is REAL here (from the saved 2026-10 dumps under tests/fixtures/):
  - listing_head_*.html : the <meta>/<link> head tags of the saved OLX /
    Otomoto / Otodom *listing* pages. On Otomoto and Otodom og:image is the
    site logo (fb-image200x200.png) and OLX has no og:image at all, so these
    prove the logo/placeholder rejection against real markup.
  - cdn_photo_urls.txt  : real photo URLs from the Otomoto/Otodom dumps — the
    same file id served at several /image;s=WxH sizes, which is what the
    resize helper relies on.

What is SYNTHETIC: the "ad page" HTML below. The dumps supplied were listing
pages, not ad pages, so og:image on a real ad page has NOT been verified
against a saved page for any of the three sites.
"""
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from service import scraper
from service.scraper import (
    _extract_image_url, _resize_image_url, _valid_image_url,
    _IMG_SIZE_MEDIUM, _IMG_SIZE_THUMB,
)
from service.ai_client import _format_ad

FIX = Path(__file__).parent / "fixtures"
PHOTO = "https://ireland.apollo.olxcdn.com/v1/files/0d9nrq0g79or-OTOMOTOPL/image;s=644x461"


class FakePage:
    """Minimal stand-in for playwright Page.get_attribute() over static HTML."""
    def __init__(self, html: str):
        self.html = html
        self.calls: list[str] = []

    async def get_attribute(self, selector, name, timeout=None):
        self.calls.append(selector)
        m = re.match(r'(\w+)\[(\w+)="([^"]+)"\]', selector)
        tag, key, val = m.groups()
        for t in re.findall(rf"<{tag}\b[^>]*>", self.html):
            if re.search(rf'{key}="{re.escape(val)}"', t):
                a = re.search(rf'{name}="([^"]*)"', t)
                if a:
                    return a.group(1)
        raise TimeoutError("no such element")   # what Playwright does on timeout


# ── _valid_image_url ──────────────────────────────────────────────────────────

class TestValidImageUrl:
    def test_cdn_photo_accepted(self):
        assert _valid_image_url(PHOTO, "https://www.otomoto.pl") == PHOTO

    def test_protocol_relative_made_absolute(self):
        u = "//ireland.apollo.olxcdn.com/v1/files/abc/image;s=200x150"
        assert _valid_image_url(u, "https://www.olx.pl") == "https:" + u

    def test_relative_uses_origin(self):
        assert _valid_image_url("/img/p.jpg", "https://www.olx.pl") == "https://www.olx.pl/img/p.jpg"

    @pytest.mark.parametrize("bad", [
        "", None, "   ", "http://ireland.apollo.olxcdn.com/v1/files/a/image;s=1x1",
        "data:image/png;base64,AAAA", "javascript:alert(1)", "ftp://x/y.jpg",
        "https://x.example/" + "a" * 3000,
    ])
    def test_unusable_rejected(self, bad):
        assert _valid_image_url(bad, "https://www.olx.pl") == ""

    @pytest.mark.parametrize("fixture,base", [
        ("listing_head_otomoto.html", "https://www.otomoto.pl"),
        ("listing_head_otodom.html", "https://www.otodom.pl"),
    ])
    def test_real_site_logo_og_image_rejected(self, fixture, base):
        html = (FIX / fixture).read_text(encoding="utf-8")
        raw = re.search(r'property="og:image" content="([^"]+)"', html).group(1)
        assert "fb-image200x200" in raw          # sanity: it really is the logo
        assert _valid_image_url(raw, base) == ""

    def test_generic_placeholders_rejected(self):
        for u in ("https://www.olx.pl/static/no-photo.png",
                  "https://www.olx.pl/img/logo.png",
                  "https://www.olx.pl/img/placeholder.jpg",
                  "https://www.olx.pl/img/icon.svg"):
            assert _valid_image_url(u, "https://www.olx.pl") == ""

    def test_cdn_ids_are_not_pattern_matched(self):
        # opaque ids on the photo CDN may contain any substring
        u = "https://ireland.apollo.olxcdn.com/v1/files/xlogox-placeholder/image;s=200x150"
        assert _valid_image_url(u, "https://www.olx.pl") == u


# ── _resize_image_url ─────────────────────────────────────────────────────────

class TestResize:
    def test_real_urls_all_have_size_param(self):
        urls = (FIX / "cdn_photo_urls.txt").read_text().split()
        assert urls
        for u in urls:
            out = _resize_image_url(u, _IMG_SIZE_THUMB)
            assert out.endswith(f"/image;s={_IMG_SIZE_THUMB}" ) or f"/image;s={_IMG_SIZE_THUMB};" in out
            assert out.split("/image;s=")[0] == u.split("/image;s=")[0]   # same file id / token

    def test_quality_param_preserved(self):
        u = "https://ireland.apollo.olxcdn.com/v1/files/abc/image;s=1280x1024;q=80"
        assert _resize_image_url(u, "360x270") == "https://ireland.apollo.olxcdn.com/v1/files/abc/image;s=360x270;q=80"

    def test_non_cdn_unchanged(self):
        u = "https://example.com/photo/image;s=1000x700"
        assert _resize_image_url(u, "200x150") == u

    def test_cdn_without_size_unchanged_and_empty(self):
        u = "https://ireland.apollo.olxcdn.com/v1/files/abc/photo.jpg"
        assert _resize_image_url(u, "200x150") == u
        assert _resize_image_url("", "200x150") == ""


# ── _extract_image_url ────────────────────────────────────────────────────────

AD_PAGE = f'<html><head><meta property="og:image" content="{PHOTO}"></head></html>'


@pytest.mark.parametrize("site,url", [
    ("olx", "https://www.olx.pl/d/oferta/x-CID5-IDabc.html"),
    ("otomoto", "https://www.otomoto.pl/osobowe/oferta/x-ID6abc.html"),
    ("otodom", "https://www.otodom.pl/pl/oferta/x-ID4abc"),
])
class TestExtractImageUrl:
    @pytest.mark.asyncio
    async def test_og_image_used(self, site, url):
        assert await _extract_image_url(FakePage(AD_PAGE), url, site) == PHOTO

    @pytest.mark.asyncio
    async def test_falls_back_to_twitter_image(self, site, url):
        html = f'<head><meta name="twitter:image" content="{PHOTO}"></head>'
        assert await _extract_image_url(FakePage(html), url, site) == PHOTO

    @pytest.mark.asyncio
    async def test_logo_og_image_skipped_for_next_candidate(self, site, url):
        html = ('<meta property="og:image" content="//statics.otomoto.pl/x/fb-image200x200.png">'
                f'<meta name="twitter:image" content="{PHOTO}">')
        assert await _extract_image_url(FakePage(html), url, site) == PHOTO

    @pytest.mark.asyncio
    async def test_nothing_found_returns_empty_never_raises(self, site, url):
        assert await _extract_image_url(FakePage("<html></html>"), url, site) == ""


@pytest.mark.parametrize("fixture,site,url", [
    ("listing_head_olx.html", "olx", "https://www.olx.pl/d/oferta/x.html"),
    ("listing_head_otomoto.html", "otomoto", "https://www.otomoto.pl/osobowe/oferta/x.html"),
    ("listing_head_otodom.html", "otodom", "https://www.otodom.pl/pl/oferta/x"),
])
@pytest.mark.asyncio
async def test_real_listing_heads_yield_no_photo(fixture, site, url):
    """Real saved pages whose og:image is a logo / absent must give no photo."""
    page = FakePage((FIX / fixture).read_text(encoding="utf-8"))
    assert await _extract_image_url(page, url, site) == ""


@pytest.mark.asyncio
async def test_page_error_is_swallowed():
    page = MagicMock()
    page.get_attribute = AsyncMock(side_effect=RuntimeError("boom"))
    assert await _extract_image_url(page, "https://www.olx.pl/d/oferta/x.html", "olx") == ""


# ── _extract_one integration: retry / "no data" logic must be unaffected ──────

def _fake_ctx(og_html: str, field_visible: bool):
    """ctx whose pages 'render' og_html; field locators visible or not."""
    pages = []

    def new_page():
        page = FakePage(og_html)
        resp = MagicMock(status=200)
        page.url = "https://www.olx.pl/d/oferta/x.html"     # final URL after redirects
        page.goto = AsyncMock(return_value=resp)
        page.close = AsyncMock()
        page.content = AsyncMock(return_value="<html></html>")
        page.inner_text = AsyncMock(return_value="body text")
        page.query_selector_all = AsyncMock(return_value=[])
        page.wait_for_selector = AsyncMock()
        loc = MagicMock()
        loc.first.is_visible = AsyncMock(return_value=field_visible)
        loc.first.inner_text = AsyncMock(return_value="Some title")
        page.locator = MagicMock(return_value=loc)
        pages.append(page)
        return page

    ctx = MagicMock()
    ctx.new_page = AsyncMock(side_effect=new_page)
    return ctx, pages


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(scraper.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(scraper, "_dump_page_state", AsyncMock(return_value=("", "")))


@pytest.mark.asyncio
async def test_extract_one_adds_image_url(no_sleep):
    ctx, _ = _fake_ctx(AD_PAGE, field_visible=True)
    data = await scraper._extract_one("https://www.olx.pl/d/oferta/x.html", ctx, TimeoutError)
    assert data["image_url"] == PHOTO
    assert data["title"] == "Some title"


@pytest.mark.asyncio
async def test_extract_one_without_photo_still_succeeds(no_sleep):
    ctx, _ = _fake_ctx("<html></html>", field_visible=True)
    data = await scraper._extract_one("https://www.olx.pl/d/oferta/x.html", ctx, TimeoutError)
    assert "image_url" not in data and "error" not in data


@pytest.mark.asyncio
async def test_all_fields_failed_still_retries_even_with_og_image(no_sleep):
    """og:image present must NOT make len(data) > 1 and mask 'no data found'."""
    ctx, pages = _fake_ctx(AD_PAGE, field_visible=False)
    await scraper._extract_one("https://www.olx.pl/d/oferta/x.html", ctx, TimeoutError)
    assert len(pages) == scraper._MAX_RETRIES + 1     # retried every attempt


# ── image_url must never reach the AI prompt ──────────────────────────────────

def test_image_url_not_in_ai_prompt():
    data = {"url": "https://www.olx.pl/d/oferta/x.html", "title": "T", "price": "1 zł",
            "page_text": "text", "image_url": PHOTO}
    assert PHOTO not in _format_ad(data) and "image" not in _format_ad(data).lower()
