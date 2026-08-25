"""
OLX / Otomoto / Otodom scraper — async Playwright.
"""
from __future__ import annotations
import asyncio
import os
from typing import Optional
from urllib.parse import urlparse

from .config import settings
from .logger import get_logger

logger = get_logger(__name__)

# ── URL helpers ────────────────────────────────────────────────────────────────

_AD_PATTERNS = (
    "/d/oferta/",           # OLX native
    "otomoto.pl/oferta/",   # Otomoto — appears as full URL in OLX mixed listings
    "otodom.pl/oferta/",    # Otodom  — appears as full URL in OLX mixed listings
    "/oferta/",             # Catch-all for relative /oferta/ paths on any OLX sub-site
)

_LISTING_SELECTORS = (
    '[data-cy="l-card"] a[href]',
    '[data-testid="listing-grid"] a[href]',
    'div.offer-wrapper a[href]',
    'article a[href]',
)
_NEXT_PAGE_SELECTORS = (
    '[data-cy="pagination-forward"]',
    '[data-testid="pagination-forward"]',
    'a[rel="next"]',
    'a[aria-label*="następna" i]',
    'a[aria-label*="next" i]',
)
_CONSENT_SELECTORS = (
    "#onetrust-accept-btn-handler",
    'button:has-text("Akceptuj wszystkie")',
    'button:has-text("Accept all")',
    'button:has-text("Zgadzam się")',
    'button:has-text("Akceptuj")',
)
_FIELDS: dict[str, dict[str, tuple]] = {
    "olx": {
        "title":       ('h1[data-cy="ad_title"]', 'h1.css-1juynto', 'h1'),
        "price":       ('[data-testid="ad-price-container"]', '[data-cy="ad-price"]', 'strong.css-8ti3ya'),
        "description": ('[data-cy="ad_description"]', 'div.css-bgzo2k'),
        "location":    ('[data-cy="ad-contact-location"]', 'p.css-7ft27d'),
    },
    "otomoto": {
        "title":       ('h1.offer-title', 'h1[data-testid="ad-title"]', 'h1'),
        "price":       ('strong.offer-price__number', '[data-testid="ad-price"]', '.offer-price'),
        "description": ('div.offer-description__description',),
        "location":    ('.offer-details__item--geo',),
    },
    "otodom": {
        "title":       ('h1[data-cy="adPageAdTitle"]', 'h1'),
        "price":       ('[data-cy="adPageHeaderPrice"]',),
        "description": ('[data-cy="adPageAdDescription"]',),
        "location":    ('[data-cy="adPageHeaderLocation"]',),
    },
}
_PARAM_SELECTORS: dict[str, tuple] = {
    "olx":     ('[data-testid="ad-params"] li', 'ul.css-rn93um li'),
    "otomoto": ('[data-testid="advert-details-item"]', '.parametersCell', '.offer-params__item'),
    "otodom":  ('ul.css-lh6pbj li',),
}
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _site(url: str) -> str:
    if "otomoto.pl" in url: return "otomoto"
    if "otodom.pl"  in url: return "otodom"
    return "olx"


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _absolute(href: str, base: str) -> str:
    if not href:          return ""
    if href.startswith("//"): return "https:" + href
    if href.startswith("/"): return base + href
    if href.startswith("http"): return href
    return ""


def _clean(url: str) -> str:
    return url.split("?")[0].rstrip("/")


def _is_ad(url: str) -> bool:
    return any(p in url for p in _AD_PATTERNS)


def _ctx_opts() -> dict:
    env: dict = {"user_agent": _UA, "viewport": {"width": 1280, "height": 900}, "locale": "pl-PL"}
    return env


async def _dismiss(page) -> None:
    for sel in _CONSENT_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1500):
                await btn.click()
                await asyncio.sleep(0.5)
                return
        except Exception:
            continue


# ── Public API ─────────────────────────────────────────────────────────────────

async def collect_all_links(filter_url: str) -> list[str]:
    """
    Crawl all listing pages and return unique ad URLs (no limit applied here).
    The caller is responsible for applying the freemium limit.
    """
    from playwright.async_api import async_playwright

    # Point playwright at the pre-installed browser location
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", settings.PLAYWRIGHT_BROWSERS_PATH)

    links: list[str] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=settings.PLAYWRIGHT_HEADLESS)
        ctx     = await browser.new_context(**_ctx_opts())
        page    = await ctx.new_page()
        current = filter_url
        page_n  = 0

        while current:
            page_n += 1
            logger.debug("[scraper] listing page %d: %s", page_n, current)
            try:
                await page.goto(current, wait_until="domcontentloaded", timeout=30_000)
            except Exception as exc:
                logger.warning("[scraper] goto failed: %s", exc)
                break
            await asyncio.sleep(1.5)
            await _dismiss(page)

            # Collect links on this page — iterate ALL selectors (no early break)
            # because OLX listing pages can mix OLX, Otomoto, and Otodom cards
            # that may use different DOM structures.
            base = _origin(page.url)
            for sel in _LISTING_SELECTORS:
                try:
                    for el in await page.query_selector_all(sel):
                        raw  = await el.get_attribute("href") or ""
                        full = _clean(_absolute(raw, base))
                        if full and _is_ad(full) and full not in links:
                            links.append(full)
                except Exception:
                    continue

            # Next page
            nxt: str | None = None
            for sel in _NEXT_PAGE_SELECTORS:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        href = _clean(_absolute(await el.get_attribute("href") or "", base))
                        if href:
                            nxt = href
                            break
                except Exception:
                    continue

            if not nxt:
                break
            current = nxt
            await asyncio.sleep(2.0)

        await browser.close()
    return links


async def extract_ads(links: list[str]) -> list[dict]:
    """Open each ad URL, extract structured content. Returns list of {url, data} dicts."""
    from playwright.async_api import async_playwright

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", settings.PLAYWRIGHT_BROWSERS_PATH)

    results: list[dict] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=settings.PLAYWRIGHT_HEADLESS)
        ctx     = await browser.new_context(**_ctx_opts())

        for i, url in enumerate(links):
            logger.debug("[scraper] extracting %d/%d: %s", i + 1, len(links), url)
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(1.0)
                await _dismiss(page)

                site       = _site(url)
                fields     = _FIELDS.get(site, _FIELDS["olx"])
                param_sels = _PARAM_SELECTORS.get(site, _PARAM_SELECTORS["olx"])
                data: dict = {"url": url}

                for field, sels in fields.items():
                    for sel in sels:
                        try:
                            el = page.locator(sel).first
                            if await el.is_visible(timeout=2000):
                                text = (await el.inner_text()).strip()
                                if text:
                                    data[field] = text
                                    break
                        except Exception:
                            continue

                params: list[str] = []
                for sel in param_sels:
                    try:
                        for el in await page.query_selector_all(sel):
                            t = (await el.inner_text()).strip()
                            if t:
                                params.append(t)
                        if params:
                            break
                    except Exception:
                        continue
                if params:
                    data["parameters"] = "\n".join(params)

                if len(data) <= 2:
                    try:
                        data["raw_text"] = (await page.inner_text("main, body"))[:5000]
                    except Exception:
                        pass

                results.append({"url": url, "data": data})

            except Exception as exc:
                logger.warning("[scraper] extract failed for %s: %s", url, exc)
                results.append({"url": url, "data": {"url": url, "error": str(exc)}})
            finally:
                await page.close()
            await asyncio.sleep(0.5)

        await browser.close()
    return results
