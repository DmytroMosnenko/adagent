"""
OLX / Otomoto / Otodom scraper — async Playwright.

Key behaviours
──────────────
• collect_all_links  — crawls every listing page (OLX button-based AND
                        Otomoto/Otodom ?page=N URL-based pagination).
                        Stops when a page yields zero new links.
• extract_ads        — opens each ad URL, waits for JS content to render,
                        extracts structured fields + parameters.
                        Retries up to MAX_RETRIES times on 403 / timeout,
                        with exponential back-off + random jitter.
"""
from __future__ import annotations

import asyncio
import os
import random
from typing import Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from .config import settings
from .logger import get_logger

logger = get_logger(__name__)

# ── Tuning constants ───────────────────────────────────────────────────────────
_PAGE_LOAD_DELAY   = 1.5    # seconds after domcontentloaded on listing pages
_AD_LOAD_DELAY     = 1.2    # seconds after page load on individual ad pages
_PAGE_BETWEEN_DELAY= 2.0    # seconds between listing pages
_AD_BETWEEN_DELAY  = 1.2    # seconds between successive ad page opens
_MAX_RETRIES       = 2      # retry attempts on 403 / timeout per ad
_RETRY_BASE        = 8.0    # exponential back-off base (seconds)
_JS_WAIT_TIMEOUT   = 10_000 # ms to wait for JS-rendered content selector

# ── URL / site helpers ─────────────────────────────────────────────────────────

_AD_PATTERNS = (
    "/d/oferta/",
    "otomoto.pl/oferta/",
    "otodom.pl/oferta/",
    "/oferta/",             # catch-all for relative paths
)

# Listing page selectors — tried in order, ALL are iterated (no early break)
_LISTING_SELECTORS = (
    '[data-cy="l-card"] a[href]',                         # OLX native cards
    '[data-testid="listing-grid"] a[href]',               # OLX alt
    'article[data-id] a[href]',                           # Otomoto articles
    '[data-testid="listing-container"] article a[href]',  # Otomoto container
    '[data-cy="search.listing"] article a[href]',         # Otodom
    'article[data-cy="listing-item"] a[href]',            # Otodom alt
    'div.offer-wrapper a[href]',                          # Legacy OLX
    'article a[href]',                                    # Generic fallback
)

# Next-page button selectors (button-based pagination)
_NEXT_PAGE_SELECTORS = (
    '[data-cy="pagination-forward"]',                     # OLX
    '[data-testid="pagination-forward"]',                 # OLX alt
    '[data-testid="pagination-step-forwards"]',           # Otomoto
    '[data-cy="pagination.next-page"]',                   # Otodom
    'a[rel="next"]',                                      # HTML standard
    'a[aria-label*="następna" i]',                        # Polish "next"
    'a[aria-label*="next" i]',                            # English "next"
    'button[aria-label*="następna" i]',                   # Button variant
    'li.pagination-item--next a',                         # CSS class variant
    '.pagination__next a',
)

_CONSENT_SELECTORS = (
    "#onetrust-accept-btn-handler",
    'button:has-text("Akceptuj wszystkie")',
    'button:has-text("Accept all")',
    'button:has-text("Zgadzam się")',
    'button:has-text("Akceptuj")',
)

# Per-site content field selectors (multiple candidates, first match wins)
_FIELDS: dict[str, dict[str, tuple]] = {
    "olx": {
        "title":       ('h1[data-cy="ad_title"]', 'h1.css-1juynto', 'h1'),
        "price":       ('[data-testid="ad-price-container"]', '[data-cy="ad-price"]',
                        'strong.css-8ti3ya'),
        "description": ('[data-cy="ad_description"]', 'div.css-bgzo2k'),
        "location":    ('[data-cy="ad-contact-location"]', 'p.css-7ft27d'),
    },
    "otomoto": {
        "title":       ('h1[data-testid="ad-title"]', 'h1.offer-title', 'h1'),
        "price":       ('[data-testid="ad-price-container"]', 'strong.offer-price__number',
                        '[data-testid="ad-price"]', '.offer-price__number', '.offer-price'),
        "description": ('div.offer-description__description',
                        '[data-testid="item-description-value"]',
                        '.description__content'),
        "location":    ('[data-testid="location-date"]', '.offer-details__item--geo',
                        '.seller-box__seller-address__label'),
    },
    "otodom": {
        "title":       ('h1[data-cy="adPageAdTitle"]', 'h1'),
        "price":       ('[data-cy="adPageHeaderPrice"]',),
        "description": ('[data-cy="adPageAdDescription"]',),
        "location":    ('[data-cy="adPageHeaderLocation"]',),
    },
}

# Selectors for the spec/parameter table on each site.
# Format: tuple of CSS selectors for the CONTAINER element of each parameter row.
_PARAM_ITEM_SELECTORS: dict[str, tuple] = {
    "olx":     ('[data-testid="ad-params"] li', 'ul.css-rn93um li', '.css-ae6b8c li'),
    "otomoto": ('[data-testid="advert-details-item"]', '.offer-params__item',
                '.parametersCell', 'li[data-testid]'),
    "otodom":  ('ul.css-lh6pbj li', '[data-testid="table.value"]',
                '.css-1ftqasz li'),
}

# Within a parameter item, selectors for label and value sub-elements.
# Tried in order; if none match we fall back to splitting inner_text by newline.
_PARAM_LABEL_VALUE: tuple[tuple[str, str], ...] = (
    ('[data-testid*="name"], [data-testid*="label"]',
     '[data-testid*="value"]'),
    ('.offer-params__label, label',
     '.offer-params__value, strong'),
    ('.parameterName, span:first-child',
     '.parameterValue, strong'),
)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ── URL utilities ──────────────────────────────────────────────────────────────

def _site(url: str) -> str:
    if "otomoto.pl" in url: return "otomoto"
    if "otodom.pl"  in url: return "otodom"
    return "olx"


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _absolute(href: str, base: str) -> str:
    if not href:               return ""
    if href.startswith("//"): return "https:" + href
    if href.startswith("/"):  return base + href
    if href.startswith("http"): return href
    return ""


def _clean(url: str) -> str:
    return url.split("?")[0].rstrip("/")


def _is_ad(url: str) -> bool:
    return any(p in url for p in _AD_PATTERNS)


def _next_page_by_url(url: str) -> str:
    """
    Increment ?page=N in the URL query string.
    Used as a fallback when no next-page button is found on the page
    (Otomoto and Otodom rely on this pattern).
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    current_page = int(params.get("page", ["1"])[0])
    params["page"] = [str(current_page + 1)]
    new_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=new_query))


def _ctx_opts() -> dict:
    return {
        "user_agent": _UA,
        "viewport":   {"width": 1280, "height": 900},
        "locale":     "pl-PL",
    }


# ── Shared page helpers ────────────────────────────────────────────────────────

async def _dismiss_consent(page) -> None:
    for sel in _CONSENT_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1_500):
                await btn.click()
                await asyncio.sleep(0.6)
                return
        except Exception:
            continue


async def _wait_for_content(page, site: str) -> None:
    """
    Wait for JS-rendered content to appear.
    Otomoto and Otodom are React SPAs — domcontentloaded fires before
    the actual ad content is injected into the DOM.
    """
    if site in ("otomoto", "otodom"):
        try:
            await page.wait_for_selector(
                "h1, .offer-title, [data-testid='ad-title'], [data-cy='adPageAdTitle']",
                timeout=_JS_WAIT_TIMEOUT,
            )
        except Exception:
            logger.debug("[scraper] JS content wait timed out — continuing anyway")


# ── Parameter extraction ───────────────────────────────────────────────────────

async def _extract_params(page, site: str) -> list[str]:
    """
    Extract ad parameter rows as 'Label: Value' strings.
    Tries structured label+value extraction first (Otomoto/Otodom),
    falls back to raw inner_text() with heuristic parsing.
    """
    item_sels = _PARAM_ITEM_SELECTORS.get(site, _PARAM_ITEM_SELECTORS["olx"])
    params: list[str] = []

    for item_sel in item_sels:
        try:
            items = await page.query_selector_all(item_sel)
            if not items:
                continue

            for item in items:
                # Try structured label + value child extraction first
                extracted = False
                for label_sel, value_sel in _PARAM_LABEL_VALUE:
                    try:
                        label_el = await item.query_selector(label_sel)
                        value_el = await item.query_selector(value_sel)
                        if label_el and value_el:
                            label = (await label_el.inner_text()).strip()
                            value = (await value_el.inner_text()).strip()
                            if label and value and label != value:
                                params.append(f"{label}: {value}")
                                extracted = True
                                break
                    except Exception:
                        continue

                if not extracted:
                    # Fallback: parse raw inner_text
                    try:
                        text = (await item.inner_text()).strip()
                        lines = [l.strip() for l in text.splitlines() if l.strip()]
                        if len(lines) >= 2:
                            # "Label\nValue" format (Otomoto often uses this)
                            params.append(f"{lines[0]}: {lines[1]}")
                        elif len(lines) == 1 and ":" in lines[0]:
                            params.append(lines[0])
                    except Exception:
                        continue

            if params:
                break   # first working selector set is sufficient
        except Exception:
            continue

    return params


# ── Public API ─────────────────────────────────────────────────────────────────

async def collect_all_links(filter_url: str) -> list[str]:
    """
    Crawl all listing pages and return unique ad URLs (no limit applied).
    The caller is responsible for applying the freemium limit.

    Pagination strategy:
      1. Try standard next-page button selectors (works for OLX).
      2. Fall back to incrementing ?page=N in the URL (works for Otomoto/Otodom).
      3. Stop when a page yields zero new links (safe for both strategies).
    """
    from playwright.async_api import async_playwright

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", settings.PLAYWRIGHT_BROWSERS_PATH)
    logger.info("[scraper] starting link collection: %s", filter_url)

    links: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=settings.PLAYWRIGHT_HEADLESS)
        ctx     = await browser.new_context(**_ctx_opts())
        page    = await ctx.new_page()
        current = filter_url
        page_n  = 0

        while current:
            page_n += 1
            logger.info("[scraper] listing page %d: %s", page_n, current)

            try:
                resp = await page.goto(current, wait_until="domcontentloaded",
                                       timeout=30_000)
                status = resp.status if resp else 0
                logger.debug("[scraper] listing page HTTP %d", status)
                if status == 403:
                    logger.warning("[scraper] 403 on listing page — stopping pagination")
                    break
            except Exception as exc:
                logger.error("[scraper] listing page load failed: %s", exc)
                break

            await asyncio.sleep(_PAGE_LOAD_DELAY)
            await _dismiss_consent(page)

            # Collect links from ALL selectors (no early break — catches
            # cross-domain cards: OLX native + Otomoto + Otodom in one page)
            prev_count = len(links)
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

            new_count = len(links) - prev_count
            logger.info("[scraper] page %d: +%d links (total %d)", page_n, new_count, len(links))

            # Stop condition: no new links means we've exhausted the results
            if new_count == 0:
                logger.info("[scraper] no new links on page %d — end of results", page_n)
                break

            # ── Find next page ──────────────────────────────────────────────
            nxt: Optional[str] = None

            # 1. Try button selectors
            for sel in _NEXT_PAGE_SELECTORS:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2_000):
                        href = await el.get_attribute("href") or ""
                        if not href:
                            # Some sites use <button> with JS, try clicking
                            logger.debug("[scraper] next-page button found (no href): %s", sel)
                        else:
                            candidate = _clean(_absolute(href, base))
                            if candidate and candidate != current:
                                nxt = candidate
                                logger.debug("[scraper] next page via button: %s", nxt)
                                break
                except Exception:
                    continue

            # 2. URL-based fallback (Otomoto, Otodom use ?page=N)
            if not nxt:
                site = _site(current)
                if site in ("otomoto", "otodom"):
                    nxt = _next_page_by_url(current)
                    logger.debug("[scraper] next page via URL increment: %s", nxt)

            if not nxt:
                logger.info("[scraper] no next page found — done after %d pages", page_n)
                break

            current = nxt
            await asyncio.sleep(_PAGE_BETWEEN_DELAY)

        await browser.close()

    logger.info("[scraper] collected %d unique ad links total", len(links))
    return links


async def extract_ads(links: list[str]) -> list[dict]:
    """
    Open each ad URL, wait for JS content, extract structured fields.
    Retries on HTTP 403 / timeout with exponential back-off + jitter.
    Returns list of {url, data} dicts (never raises).
    """
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", settings.PLAYWRIGHT_BROWSERS_PATH)
    logger.info("[scraper] extracting content from %d ads", len(links))

    results: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=settings.PLAYWRIGHT_HEADLESS)
        ctx     = await browser.new_context(**_ctx_opts())

        for i, url in enumerate(links):
            logger.info("[scraper] extracting ad %d/%d: %s", i + 1, len(links), url)
            data = await _extract_one(url, ctx, PWTimeout)
            results.append({"url": url, "data": data})

            jitter = random.uniform(0.3, 1.0)
            await asyncio.sleep(_AD_BETWEEN_DELAY + jitter)

        await browser.close()

    logger.info("[scraper] extraction complete: %d/%d successful",
                sum(1 for r in results if "error" not in r["data"]), len(results))
    return results


async def _extract_one(url: str, ctx, PWTimeout) -> dict:
    """
    Extract a single ad page with retry.  Returns a data dict; on permanent
    failure returns {"url": url, "error": "<reason>"}.
    """
    site = _site(url)

    for attempt in range(_MAX_RETRIES + 1):
        page = None
        try:
            page = await ctx.new_page()
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            status = resp.status if resp else 0
            logger.debug("[scraper] ad page HTTP %d (attempt %d): %s", status, attempt + 1, url)

            # ── 403 handling: back off and retry ───────────────────────────
            if status == 403:
                logger.warning("[scraper] 403 on %s (attempt %d/%d)",
                               url, attempt + 1, _MAX_RETRIES + 1)
                await page.close(); page = None
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_BASE * (2 ** attempt) + random.uniform(2, 8)
                    logger.info("[scraper] backing off %.1fs before retry", delay)
                    await asyncio.sleep(delay)
                    continue
                return {"url": url, "error": "HTTP 403 — rate limited (retries exhausted)"}

            # ── Wait for JS-rendered content ────────────────────────────────
            await asyncio.sleep(_AD_LOAD_DELAY)
            await _dismiss_consent(page)
            await _wait_for_content(page, site)

            # ── Extract structured fields ────────────────────────────────────
            data: dict = {"url": url}
            fields = _FIELDS.get(site, _FIELDS["olx"])

            for field, sels in fields.items():
                for sel in sels:
                    try:
                        el = page.locator(sel).first
                        if await el.is_visible(timeout=2_000):
                            text = (await el.inner_text()).strip()
                            if text:
                                data[field] = text
                                logger.debug("[scraper] field '%s' ← '%s'",
                                             field, text[:60])
                                break
                    except Exception:
                        continue
                if field not in data:
                    logger.debug("[scraper] field '%s' not found on %s", field, url)

            # ── Extract parameters ───────────────────────────────────────────
            params = await _extract_params(page, site)
            if params:
                data["parameters"] = "\n".join(params)
                logger.debug("[scraper] extracted %d param rows", len(params))
            else:
                logger.debug("[scraper] no parameters found on %s", url)

            # ── Fallback: raw body text ──────────────────────────────────────
            if len(data) <= 2:
                logger.debug("[scraper] using raw body fallback for %s", url)
                try:
                    data["raw_text"] = (await page.inner_text("main, body"))[:5_000]
                except Exception:
                    pass

            logger.info("[scraper] extracted %d fields from %s", len(data), url)
            return data

        except PWTimeout as exc:
            logger.warning("[scraper] timeout on %s (attempt %d): %s", url, attempt + 1, exc)
            if page:
                try: await page.close()
                except Exception: pass
                page = None
            if attempt < _MAX_RETRIES:
                delay = _RETRY_BASE * (attempt + 1) + random.uniform(1, 4)
                logger.info("[scraper] waiting %.1fs before retry", delay)
                await asyncio.sleep(delay)
            else:
                return {"url": url, "error": f"Timeout after {_MAX_RETRIES + 1} attempts"}

        except Exception as exc:
            logger.error("[scraper] unexpected error on %s: %s", url, exc)
            return {"url": url, "error": str(exc)}

        finally:
            if page:
                try: await page.close()
                except Exception: pass

    return {"url": url, "error": "All retries exhausted"}
