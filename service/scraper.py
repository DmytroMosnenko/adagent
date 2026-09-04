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

# Selectors that identify ad cards that failed to load or are "Skip" ads
_AD_FAILED_SELECTORS = (
    # "Ad failed to load" labels
    '[data-cy="ad-failed-to-load"]',
    '.ad-failed',
    'div:has-text("Ad failed to load")',
    '[class*="failedToLoad"]',
    '[class*="failed-to-load"]',
)

_AD_SKIP_SELECTORS = (
    # "Skip" / sponsored skip labels
    '[data-cy="ad-skip"]',
    'button:has-text("Skip")',
    'div:has-text("Skip")',
    '[class*="skipAd"]',
    '[class*="skip-ad"]',
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


async def _next_page_by_number(page, current_page: int, base: str, current: str) -> Optional[str]:
    """
    Find the next numbered OLX pagination link.

    OLX's current markup does not reliably expose a pagination-specific
    data-cy/data-testid attribute. The reliable part is the href itself:
    pagination links contain a `page=N` query parameter.

    We inspect links with a page query parameter and choose the smallest
    page number greater than the current page. This also preserves all
    search/filter query parameters because we use OLX's own href.
    """
    candidates: list[tuple[int, str]] = []

    try:
        for el in await page.query_selector_all('a[href*="page="]'):
            try:
                href = await el.get_attribute("href") or ""
                if not href:
                    continue

                candidate = _absolute(href, base)
                if not candidate:
                    continue

                if candidate.rstrip("/") == current.rstrip("/"):
                    continue

                parsed = urlparse(candidate)
                params = parse_qs(parsed.query, keep_blank_values=True)
                page_values = params.get("page")
                if not page_values:
                    continue

                try:
                    page_num = int(page_values[0])
                except (TypeError, ValueError):
                    continue

                if page_num <= current_page:
                    continue

                candidates.append((page_num, candidate))
            except Exception:
                continue
    except Exception:
        pass

    if candidates:
        # Deduplicate URLs and choose the immediate next page.
        unique: dict[str, tuple[int, str]] = {
            url: (num, url) for num, url in candidates
        }
        candidates = list(unique.values())
        candidates.sort(key=lambda item: item[0])

        page_num, candidate = candidates[0]
        logger.debug(
            "[scraper] next page via numbered pagination: page %d -> %d: %s",
            current_page, page_num, candidate,
        )
        return candidate

    return None


def _ctx_opts() -> dict:
    """
    Browser context options.  Extra headers and settings reduce the likelihood
    of CloudFront / bot-detection blocks — particularly important on datacenter
    IPs (Hetzner, AWS, etc.) where OLX's CloudFront distribution is aggressive.
    """
    return {
        "user_agent": _UA,
        "viewport":   {"width": 1280, "height": 900},
        "locale":     "pl-PL",
        "extra_http_headers": {
            "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        },
        "ignore_https_errors": settings.SCRAPER_PROXY_IGNORE_HTTPS_ERRORS,
    }


def _proxy_cfg() -> dict | None:
    """
    Build Playwright proxy config from BrightData residential credentials.
    Credentials are passed as separate fields (not embedded in the URL) so that
    Playwright's Chromium handles the proxy-auth challenge correctly — embedding
    user:pass in the server URL string is unreliable with Chromium.

    Returns None when SCRAPER_PROXY_ADDRESS is not configured (direct connection).
    """
    if not settings.SCRAPER_PROXY_ADDRESS:
        return None
    cfg = {
        "server":   settings.SCRAPER_PROXY_ADDRESS,
        "username": settings.SCRAPER_PROXY_USERNAME,
        "password": settings.SCRAPER_PROXY_PASSWORD,
    }
    logger.info(
        "[scraper] proxy configured: %s (user: %s)",
        settings.SCRAPER_PROXY_ADDRESS,
        settings.SCRAPER_PROXY_USERNAME,
    )
    return cfg


# Resource types that add bandwidth/latency but are never read by the
# extraction logic (all fields are read via .inner_text(), not images).
# Deliberately does NOT include:
#   - "stylesheet": layout/visibility (is_visible() checks) depend on CSS
#   - "script":     Otomoto/Otodom are React SPAs, content never renders without JS
#   - "xhr"/"fetch": some sites lazy-load description/parameters via XHR
# Deliberately does NOT block ad-network domains, even though they're often
# large: doing so would make _check_ad_load_issues() report false positives
# (an ad we blocked ourselves looks identical to an ad that failed to load).
_BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})


async def _block_unneeded_requests(route) -> None:
    """
    Abort requests for resource types we never use (images/video/fonts).
    On classifieds sites, photos are typically the large majority of page
    weight — blocking them cuts proxy bandwidth substantially and removes
    the serial round-trip wait for each one, which matters most when every
    request is going through a slow residential exit node.
    """
    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


async def _install_request_blocking(ctx) -> None:
    """Apply _block_unneeded_requests to every page opened in this context."""
    await ctx.route("**/*", _block_unneeded_requests)


def _launch_opts() -> dict:
    """
    Browser launch options.  On headless server deployments, add args that
    suppress automation signals visible to CloudFront / bot detectors.
    Proxy (if configured) is injected at launch level so it applies to all
    contexts and pages — including the initial navigation that CloudFront sees.
    """
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--window-size=1280,900",
    ]
    opts: dict = {"headless": settings.PLAYWRIGHT_HEADLESS, "args": args}
    proxy = _proxy_cfg()
    if proxy:
        opts["proxy"] = proxy
    return opts


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


async def _check_ad_load_issues(page, url: str) -> None:
    """
    Detect 'Ad failed to load' and 'Skip' labels on an ad page and log
    a Warning for each found.  Never raises — purely informational.
    """
    for sel in _AD_FAILED_SELECTORS:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=800):
                logger.warning(
                    "[scraper] 'Ad failed to load' label detected on %s (selector: %s)",
                    url, sel,
                )
                return   # one warning per page is enough
        except Exception:
            continue

    for sel in _AD_SKIP_SELECTORS:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=800):
                logger.warning(
                    "[scraper] 'Skip' label detected on %s (selector: %s)",
                    url, sel,
                )
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


async def _get_full_page_text(page) -> str:
    """
    Return the full visible text of the ad page for use as AI input.
    Captures everything including structured param tables that may not
    be individually addressable by CSS selectors.
    """
    try:
        return (await page.inner_text("main, body"))[:12_000]
    except Exception:
        return ""


# ── Public API ─────────────────────────────────────────────────────────────────

async def collect_all_links(filter_url: str) -> list[str]:
    """
    Crawl all listing pages and return unique ad URLs (no limit applied).
    The caller is responsible for applying the freemium limit.

    Pagination strategy:
      1. Try standard next-page button selectors (works for OLX).
      2. If there is no "Dalej", follow the next numbered pagination link.
      3. Fall back to incrementing ?page=N in the URL (Otomoto/Otodom).
      4. Stop when a page yields zero new links (safe for both strategies).
    """
    from playwright.async_api import async_playwright

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", settings.PLAYWRIGHT_BROWSERS_PATH)
    logger.info("[scraper] starting link collection: %s", filter_url)

    # Collect links as a set to deduplicate promoted ads that appear on
    # multiple filter pages, then preserve insertion order via a list.
    seen: set[str] = set()
    links: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**_launch_opts())
        ctx     = await browser.new_context(**_ctx_opts())
        await _install_request_blocking(ctx)
        page    = await ctx.new_page()
        current = filter_url
        page_n  = 0

        while current:
            page_n += 1
            logger.info("[scraper] listing page %d: %s", page_n, current)

            try:
                resp=await page.goto(current, wait_until="networkidle",
                                       timeout=90_000)
                status = resp.status if resp else 0
                logger.debug("[scraper] listing page HTTP %d", status)
                if status == 403:
                    logger.warning(
                        "[scraper] 403 headers=%s",
                        dict(resp.headers) if resp else None,
                    )

                    if resp:
                        body = await resp.text()
                        logger.warning("[scraper] 403 body=%s", body[:5000])

                    break
            except Exception as exc:
                logger.error("[scraper] listing page load failed: %s", exc)
                break

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
                        if full and _is_ad(full) and full not in seen:
                            seen.add(full)
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

            # 1. Try button selectors (OLX uses buttons with hrefs)
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

            # 2. Numbered-pagination fallback — OLX sometimes has only
            #    "1", "2", "3", ... and no "Dalej" button.  Pick the next
            #    numbered link instead of assuming the forward button exists.
            if not nxt:
                nxt = await _next_page_by_number(
                    page, page_n, base, current
                )

            # 3. URL-based fallback — Otomoto and Otodom use ?page=N.
            #    Keep this after numbered-link detection so an explicit link
            #    rendered by the site always wins.
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
        browser = await pw.chromium.launch(**_launch_opts())
        ctx     = await browser.new_context(**_ctx_opts())
        await _install_request_blocking(ctx)

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
            resp = await page.goto(url, wait_until="networkidle", timeout=90_000)
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
            await _dismiss_consent(page)
            await _wait_for_content(page, site)

            # ── Check for ad-load / skip issues ─────────────────────────────
            await _check_ad_load_issues(page, url)

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

            # ── Full page text for AI (universal, language-agnostic) ─────────
            # Otomoto in particular has rich structured data in the page but
            # CSS selectors may miss fields (e.g. Mileage) due to DOM layout
            # differences.  Providing the full page text lets the AI extract
            # whatever is actually present, regardless of language or layout.
            data["page_text"] = await _get_full_page_text(page)

            # ── Fallback: raw body text (kept for backwards compatibility) ───
            if len(data) <= 2:
                logger.debug("[scraper] using raw body fallback for %s", url)
                data["raw_text"] = data.get("page_text", "")[:5_000]

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
