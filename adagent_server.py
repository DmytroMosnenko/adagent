"""
AdAgent — FastAPI server
Routes: web UI + Stripe webhooks + JSON status API
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from service.models import generate_report_id
from pathlib import Path
from typing import Optional

import stripe
from contextlib import asynccontextmanager
from fastapi import (
    BackgroundTasks, Cookie, Depends, FastAPI, Form,
    HTTPException, Query, Request, Response,
)
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from service.config import settings
from service.db import async_session, get_db
from service.logger import get_logger, setup_root_logging

# Apply unified log formatting before any third-party loggers are configured
setup_root_logging()
from service import crud, auth, email_client, stripe_client, scraper, i18n, funnel, url_guard
from service.demo_data import DEMO_EXAMPLES
from service.tasks import run_analysis

logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.REPORT_STORAGE_PATH).mkdir(parents=True, exist_ok=True)
    async with async_session() as db:
        await crud.fail_stale_reports(db)
    # One Chromium instance + context pool per worker process, kept alive for
    # the process lifetime — see scraper.py's ContextPool / scrape_gate.
    await scraper.startup_browser_pool()
    logger.info("AdAgent %s started", settings.APP_VERSION)
    yield
    await scraper.shutdown_browser_pool()

app = FastAPI(title="AdAgent", version=settings.APP_VERSION,
              docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    # Browsers request this path automatically on every navigation regardless
    # of any <link rel="icon"> tags — without an explicit route it just 404s
    # on every page load. Serving the same PNG used elsewhere is fine here;
    # browsers sniff content type rather than enforcing the .ico format.
    return FileResponse("static/favicon.png", media_type="image/png")

# ── Preset definitions (for the UI) ───────────────────────────────────────────
from service.prompts_registry import PRESETS
from service.languages import LANGUAGES, language_options, detect_language, DEFAULT_LANGUAGE


# ── Startup ────────────────────────────────────────────────────────────────────


# ── Helpers ────────────────────────────────────────────────────────────────────

# Messages shown on the landing page after a rejected /analyze. Passed as short
# codes (?error=invalid_url) instead of free text: the text is translated here, and
# arbitrary ?error= text can no longer be reflected into the page.
FORM_ERRORS = {
    "invalid_url":  "Invalid URL. Must be from olx.pl, otomoto.pl or otodom.pl",
    "need_prompts": "Please provide both custom prompts or choose a preset.",
    "daily_limit":  "Daily limit of free analyses reached. Upgrade to Pro or try again tomorrow.",
    "busy":         "Free analyses are temporarily unavailable due to high demand. Please try again later or upgrade to Pro.",
}

WELCOME_LINK_TTL_MINUTES = 60 * 24


def _ui_lang(request: Request, forced: Optional[str] = None) -> str:
    return forced or i18n.pick_ui_language(
        request.query_params.get("lang"),
        request.cookies.get("ui_lang"),
        request.headers.get("accept-language"),
    )


def _switch_url(request: Request, ui_lang: str) -> str:
    """Link for the PL/EN toggle in the nav. Symmetric: both languages get a
    clean, stable path on the landing page (/pl, /en); every other page keeps
    its own path and flips ?lang=."""
    other = "en" if ui_lang == "pl" else "pl"
    if request.url.path in ("/", "/pl", "/en"):
        return f"/{other}"
    return f"{request.url.path}?lang={other}"


def _tpl(name: str, request: Request, extra: dict | None = None, lang: Optional[str] = None):
    ui_lang = _ui_lang(request, lang)
    ctx = {"request": request, "presets": PRESETS, "settings": settings,
           "languages": language_options(),
           "current_year": datetime.now(timezone.utc).year,
           "ui_lang": ui_lang, "t": i18n.translator(ui_lang),
           "price_label": settings.price_label(ui_lang),
           "switch_url": _switch_url(request, ui_lang)}
    ctx.update(extra or {})
    resp = templates.TemplateResponse(request=request, name=name, context=ctx)
    # Remember whatever language we actually resolved (explicit ?lang=, forced
    # /pl or /en, an existing cookie, or a first-visit Accept-Language guess) —
    # so a returning visitor keeps getting their language even if their browser
    # locale or lack of an ?lang= param would otherwise resolve differently.
    if request.cookies.get("ui_lang") != ui_lang:
        resp.set_cookie("ui_lang", ui_lang, max_age=365 * 86400,
                        httponly=True, secure=True, samesite="lax")
    resp.headers["Vary"] = "Accept-Language, Cookie"
    return resp


def _client_hash(request: Request) -> str:
    """Keyed hash of the client IP (nginx sets X-Real-IP). Never stores the raw IP."""
    ip = request.client.host if request.client else ""
    if ip in ("127.0.0.1", "::1"):
        ip = request.headers.get("x-real-ip", ip)
    return hmac.new(settings.SECRET_KEY.encode(), ip.encode(), hashlib.sha256).hexdigest()


async def _resolve_user_and_sub(
    session_token: Optional[str],
    db: AsyncSession,
) -> tuple[Optional[object], bool]:
    user = None
    subscribed = False
    if session_token:
        s = await crud.get_session(db, session_token)
        if s:
            user = await crud.get_user_by_id(db, s.user_id)
            if user:
                subscribed = await crud.is_subscribed(db, user.id)
    return user, subscribed


# ── Landing page ───────────────────────────────────────────────────────────────

async def _render_index(request: Request, db: AsyncSession, session_token: Optional[str],
                        error: Optional[str], lang: Optional[str]):
    user, subscribed = await _resolve_user_and_sub(session_token, db)
    ui_lang = _ui_lang(request, lang)
    # Own / test accounts: no tracker on the page, and the browser is flagged as opted out.
    funnel_exclude = bool(user and user.email.lower() in settings.funnel_exclude_emails)
    # Polish UI -> Polish report by default; otherwise keep the old Accept-Language behaviour
    default_language = "pl" if ui_lang == "pl" else detect_language(request.headers.get("accept-language"))
    error_text = i18n.translate(FORM_ERRORS[error], ui_lang) if error in FORM_ERRORS else None
    return _tpl("index.html", request, {
        "user": user, "subscribed": subscribed,
        # invalid_url is shown next to the URL field; other errors keep the top banner
        "error": None if error == "invalid_url" else error_text,
        "url_error": error_text if error == "invalid_url" else None,
        "default_language": default_language,
        "demo_examples": DEMO_EXAMPLES,
        "funnel_exclude": funnel_exclude,
    }, lang=lang)


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    adagent_session: Optional[str] = Cookie(default=None),
    error: Optional[str] = None,
):
    return await _render_index(request, db, adagent_session, error, None)


@app.get("/pl", response_class=HTMLResponse)
async def index_pl(
    request: Request,
    db: AsyncSession = Depends(get_db),
    adagent_session: Optional[str] = Cookie(default=None),
    error: Optional[str] = None,
):
    """Stable Polish landing page — the final URL for Google Ads / hreflang."""
    return await _render_index(request, db, adagent_session, error, "pl")


@app.get("/en", response_class=HTMLResponse)
async def index_en(
    request: Request,
    db: AsyncSession = Depends(get_db),
    adagent_session: Optional[str] = Cookie(default=None),
    error: Optional[str] = None,
):
    """
    Mirror of /pl for English — gives the language toggle a clean, symmetric
    URL on both sides (was previously "/?lang=en"). Canonicalizes to "/"
    (see index.html's <link rel="canonical">) so this never competes with "/"
    as a separate indexed URL — it's a link target, not a distinct page.
    """
    return await _render_index(request, db, adagent_session, error, "en")


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    return PlainTextResponse(
        "User-agent: *\n"
        "Disallow: /report/\n"
        "Disallow: /api/\n"
        "Disallow: /auth\n"
        "Disallow: /history\n"
        "Disallow: /analyze\n"
        "Disallow: /subscribe/\n"
        "\n"
        f"Sitemap: {settings.APP_BASE_URL}/sitemap.xml\n"
    )


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml():
    base = settings.APP_BASE_URL
    def alt(lang: str, path: str) -> str:
        return f'<xhtml:link rel="alternate" hreflang="{lang}" href="{base}{path}"/>'
    urls = [
        ("/",  [alt("en", "/"), alt("pl", "/pl"), alt("x-default", "/")]),
        ("/pl", [alt("en", "/"), alt("pl", "/pl"), alt("x-default", "/")]),
        ("/subscribe", []), ("/privacy", []), ("/terms", []),
    ]
    body = "".join(f"<url><loc>{base}{path}</loc>{''.join(alts)}</url>" for path, alts in urls)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
           'xmlns:xhtml="http://www.w3.org/1999/xhtml">' + body + "</urlset>")
    return Response(content=xml, media_type="application/xml")


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_policy(
    request: Request,
    db: AsyncSession = Depends(get_db),
    adagent_session: Optional[str] = Cookie(default=None),
):
    user, subscribed = await _resolve_user_and_sub(adagent_session, db)
    return _tpl("legal_privacy.html", request, {"user": user, "subscribed": subscribed})


@app.get("/terms", response_class=HTMLResponse)
async def terms_of_service(
    request: Request,
    db: AsyncSession = Depends(get_db),
    adagent_session: Optional[str] = Cookie(default=None),
):
    user, subscribed = await _resolve_user_and_sub(adagent_session, db)
    return _tpl("legal_terms.html", request, {"user": user, "subscribed": subscribed})


# ── Analyze form submit ────────────────────────────────────────────────────────

def _home(request: Request) -> str:
    """Landing page in the visitor's UI language (so error redirects stay Polish)."""
    return "/pl" if _ui_lang(request) == "pl" else "/"


@app.post("/analyze")
async def analyze(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    adagent_session: Optional[str] = Cookie(default=None),
    filter_url: str = Form(...),
    prompt_preset: Optional[str] = Form(default=None),
    custom_ad_prompt: Optional[str] = Form(default=None),
    custom_summary_prompt: Optional[str] = Form(default=None),
    report_language: Optional[str] = Form(default=None),
    sid: Optional[str] = Form(default=None),     # per-tab funnel id, filled by static/funnel.js
):
    # Validate URL (parsed, allowlisted host — see service/url_guard.py)
    filter_url = filter_url.strip()
    reason = url_guard.rejection_reason(filter_url)
    if reason is not None:
        # repr() + truncation: the URL is attacker-controlled (no newline/log injection).
        logger.info("[analyze] invalid URL rejected (%s): %s", reason, repr(filter_url[:300]))
        # Funnel: this redirect used to be silent. Written after the response is sent.
        background_tasks.add_task(
            funnel.record_server_event, None, "invalid_url", sid,
            request.headers.get("user-agent"), detail=funnel.invalid_url_reason(filter_url),
            url=filter_url, ui_lang=_ui_lang(request), session_token=adagent_session)
        # #filter_url: land on the form field, where the message is rendered.
        return RedirectResponse(f"{_home(request)}?error=invalid_url#filter_url", 303)

    # Validate preset
    if prompt_preset and prompt_preset not in PRESETS:
        prompt_preset = None

    # Validate language — fall back to default rather than rejecting the whole form
    if report_language not in LANGUAGES:
        report_language = DEFAULT_LANGUAGE

    # Custom prompt requires both fields
    if not prompt_preset:
        if not custom_ad_prompt or not custom_summary_prompt:
            return RedirectResponse(f"{_home(request)}?error=need_prompts", 303)

    # Resolve user & subscription
    user, subscribed = await _resolve_user_and_sub(adagent_session, db)

    # Free-tier guard (subscribers are never limited)
    client_hash = _client_hash(request)
    if not subscribed:
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
        per_client = settings.FREE_REPORTS_PER_CLIENT_PER_DAY
        if per_client > 0 and await crud.count_free_reports_since(db, since, client_hash) >= per_client:
            return RedirectResponse(f"{_home(request)}?error=daily_limit", 303)
        global_cap = settings.FREE_REPORTS_GLOBAL_PER_DAY
        if global_cap > 0 and await crud.count_free_reports_since(db, since) >= global_cap:
            return RedirectResponse(f"{_home(request)}?error=busy", 303)

    # Create report
    id_bytes  = generate_report_id()
    report    = await crud.create_report(
        db,
        id=id_bytes,
        user_id=user.id if user else None,
        filter_url=filter_url,
        prompt_preset=prompt_preset,
        custom_ad_prompt=custom_ad_prompt if not prompt_preset else None,
        custom_summary_prompt=custom_summary_prompt if not prompt_preset else None,
        report_language=report_language,
        client_hash=client_hash,
        is_limited=not subscribed,
        status="pending",
    )

    # Must be queued BEFORE run_analysis: tasks run in order, and the done/failed
    # outcome rows look this one up by report id to inherit the session attribution.
    background_tasks.add_task(
        funnel.record_server_event, None, "analyze_started", sid,
        request.headers.get("user-agent"), detail=report.id, url=filter_url,
        ui_lang=_ui_lang(request), session_token=adagent_session)
    background_tasks.add_task(run_analysis, report.id)
    return RedirectResponse(f"/report/{report.id}", status_code=303)


# ── Report view ────────────────────────────────────────────────────────────────

@app.get("/report/{report_id}", response_class=HTMLResponse)
async def report_view(
    request: Request,
    report_id: str,
    db: AsyncSession = Depends(get_db),
    adagent_session: Optional[str] = Cookie(default=None),
):
    report = await crud.get_report(db, report_id)
    if not report:
        raise HTTPException(404, "Report not found")

    user, subscribed = await _resolve_user_and_sub(adagent_session, db)

    if report.status in ("pending", "running"):
        return _tpl("status.html", request, {
            "report": report, "user": user, "subscribed": subscribed,
        })

    if report.status == "failed":
        # tasks.py prefixes the confirmed-empty-result message with this
        # exact phrase — used to soften the "failure" framing and skip the
        # pointless "retry identical search" CTA when it's a real 0-match
        # result rather than a scraping problem.
        is_no_match = bool(report.error_message) and report.error_message.startswith("No ads match this search")
        return _tpl("error.html", request, {
            "message": report.error_message or "Analysis failed.",
            "is_no_match": is_no_match,
            "report": report,
            "user": user,
        })

    # Done
    if report.prompt_preset and report.report_path:
        # Serve the pre-generated self-contained HTML report
        html = Path(report.report_path).read_text(encoding="utf-8")
        return HTMLResponse(html)

    if report.result_json:
        data = json.loads(report.result_json)
        return _tpl("report_custom.html", request, {
            "report": report,
            "ads": data.get("ads", []),
            "summary": data.get("summary", ""),
            "user": user,
            "subscribed": subscribed,
        })

    raise HTTPException(500, "Report data missing")


# ── JSON status (polled by status.html) ───────────────────────────────────────

@app.get("/api/report/{report_id}/status")
async def report_status(report_id: str, db: AsyncSession = Depends(get_db)):
    report = await crud.get_report(db, report_id)
    if not report:
        raise HTTPException(404)
    return {
        "status":       report.status,
        "ads_found":    report.ads_found,
        "ads_analyzed": report.ads_analyzed,
        "is_limited":   report.is_limited,
        "has_preset":   report.prompt_preset is not None,
        "notify_email": report.notify_email,
    }


@app.post("/api/report/{report_id}/notify")
async def report_notify(
    report_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    adagent_session: Optional[str] = Cookie(default=None),
):
    """
    Toggle "email me when finished" for a report from the progress page.
    Requires sign-in. If the report was started anonymously, checking the
    box claims it for this account (so it also appears in /history).
    """
    user, _ = await _resolve_user_and_sub(adagent_session, db)
    if not user:
        raise HTTPException(401, "Sign in to enable email notifications")

    body = await request.json()
    enabled = bool(body.get("enabled", False))

    report = await crud.set_report_notify_email(db, report_id, user.id, enabled)
    if not report:
        raise HTTPException(404, "Report not found or not owned by this account")

    return {"ok": True, "notify_email": report.notify_email}


# ── Auth ───────────────────────────────────────────────────────────────────────

@app.get("/auth", response_class=HTMLResponse)
async def auth_page(request: Request, sent: Optional[str] = None):
    return _tpl("auth_request.html", request, {"sent": sent})


@app.post("/auth")
async def auth_send(
    request: Request,
    db: AsyncSession = Depends(get_db),
    email: str = Form(...),
):
    email = email.strip().lower()
    # Always create user (idempotent)
    await crud.get_or_create_user(db, email)
    token = auth.generate_token()
    await crud.create_magic_link(db, token, email, settings.MAGIC_LINK_TTL_MINUTES)
    if not await email_client.send_magic_link(email, token, _ui_lang(request)):
        # Mail relay refused / unreachable (email_client already logged why).
        # Don't show the "sent" screen — re-render the form with an error.
        resp = _tpl("auth_request.html", request, {"send_failed": True})
        resp.status_code = 503
        return resp
    return RedirectResponse(f"/auth?sent={email}", status_code=303)


@app.get("/auth/verify/{token}")
async def auth_verify(
    token: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    email = await crud.consume_magic_link(db, token)
    if not email:
        raise HTTPException(400, "Invalid or expired sign-in link.")

    user, _ = await crud.get_or_create_user(db, email)
    session_token = auth.generate_token()
    await crud.create_session(db, session_token, user.id, settings.SESSION_TTL_DAYS)

    resp = RedirectResponse("/history", status_code=303)
    resp.set_cookie(
        "adagent_session", session_token,
        max_age=settings.SESSION_TTL_DAYS * 86400,
        httponly=True, secure=True, samesite="lax",
    )
    return resp


@app.post("/auth/logout")
async def auth_logout(
    response: Response,
    db: AsyncSession = Depends(get_db),
    adagent_session: Optional[str] = Cookie(default=None),
):
    if adagent_session:
        await crud.delete_session(db, adagent_session)
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie("adagent_session")
    return resp


# ── History ────────────────────────────────────────────────────────────────────

@app.get("/history", response_class=HTMLResponse)
async def history(
    request: Request,
    db: AsyncSession = Depends(get_db),
    adagent_session: Optional[str] = Cookie(default=None),
):
    user, subscribed = await _resolve_user_and_sub(adagent_session, db)
    if not user:
        return RedirectResponse("/auth", status_code=303)
    reports = await crud.get_user_reports(db, user.id)
    return _tpl("history.html", request, {
        "user": user, "subscribed": subscribed, "reports": reports,
    })


# ── Subscribe ──────────────────────────────────────────────────────────────────

@app.get("/subscribe", response_class=HTMLResponse)
async def subscribe_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    adagent_session: Optional[str] = Cookie(default=None),
):
    user, subscribed = await _resolve_user_and_sub(adagent_session, db)
    prices = settings.stripe_prices
    plans = [
        {"period": p, "price_id": pid, "label": p.capitalize()}
        for p, pid in prices.items() if pid
    ]
    return _tpl("subscribe.html", request, {
        "user": user, "subscribed": subscribed, "plans": plans,
    })


@app.post("/subscribe/checkout")
async def subscribe_checkout(
    request: Request,
    db: AsyncSession = Depends(get_db),
    adagent_session: Optional[str] = Cookie(default=None),
    plan: str = Form(default="monthly"),
):
    user, _ = await _resolve_user_and_sub(adagent_session, db)
    customer_email = user.email if user else None
    try:
        url = await stripe_client.create_checkout_session(plan, customer_email, lang=_ui_lang(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return RedirectResponse(url, status_code=303)


@app.get("/subscribe/success", response_class=HTMLResponse)
async def subscribe_success(
    request: Request,
    db: AsyncSession = Depends(get_db),
    adagent_session: Optional[str] = Cookie(default=None),
    session_id: Optional[str] = None,
):
    user, subscribed = await _resolve_user_and_sub(adagent_session, db)
    # Verified purchase data for the conversion tag (None -> tag skipped)
    purchase = None
    if settings.tracking_enabled:
        purchase = await stripe_client.get_checkout_summary(session_id)
    return _tpl("subscribe_success.html", request, {
        "user": user, "subscribed": subscribed, "purchase": purchase,
    })


# ── Stripe webhook ─────────────────────────────────────────────────────────────

@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe_client.parse_webhook(payload, sig_header)
    except stripe.SignatureVerificationError:
        logger.warning("[webhook] invalid signature")
        raise HTTPException(400, "Invalid signature")

    etype = event["type"]
    logger.info("[webhook] %s", etype)

    if etype == "checkout.session.completed":
        session = stripe_client.get_session_dict(event)
        await _handle_checkout_completed(db, session)

    elif etype in ("customer.subscription.updated", "customer.subscription.deleted"):
        sub = event["data"]["object"].to_dict()
        sub_id = sub.get("id")
        status = "canceled" if etype.endswith("deleted") else sub.get("status", "active")
        if not sub_id:
            logger.warning("[webhook] Missing subscription ID in event %s", etype)
            return {"ok": False}

        raw_period_end = stripe_client.extract_current_period_end(sub)
        period_end = stripe_client.period_end_to_datetime(raw_period_end) if raw_period_end else None
        await crud.update_subscription_status(
            db=db,
            stripe_subscription_id=sub_id,
            status=status,
            current_period_end=period_end
        )

    elif etype == "invoice.payment_failed":
        inv = event["data"]["object"].to_dict()
        sub_id = inv.get("subscription")
        if sub_id:
            await crud.update_subscription_status(
                db=db,
                stripe_subscription_id=sub_id,
                status="past_due",
                current_period_end=None
            )

    return {"ok": True}


async def _handle_checkout_completed(db: AsyncSession, session: dict) -> None:
    customer_email = session.get("customer_details", {}).get("email") or session.get("customer_email")
    customer_id = session.get("customer")
    subscription_id = session.get("subscription")

    if not customer_email or not subscription_id:
        logger.warning("[webhook] checkout.completed missing email or subscription_id")
        return

    # Find or create user
    user, created = await crud.get_or_create_user(db, customer_email.lower())
    if customer_id and not user.stripe_customer_id:
        await crud.set_stripe_customer(db, user.id, customer_id)

    stripe_obj = await stripe.Subscription.retrieve_async(subscription_id)

    sub = stripe_obj.to_dict()

    price_id = None
    sub_plan = sub.get("plan")
    sub_items = sub.get("items")

    if sub_plan and isinstance(sub_plan, dict):
        price_id = sub_plan.get("id")
    elif sub_items and isinstance(sub_items, dict):
        items_data = sub_items.get("data", [])
        if items_data and len(items_data) > 0:
            first_item = items_data[0]
            if isinstance(first_item, dict) and first_item.get("price"):
                price_id = first_item["price"].get("id")

    if not price_id:
        logger.error("[webhook] Could not find price_id in subscription %s", subscription_id)
        return

    plan = stripe_client.detect_plan_period(price_id)

    raw_period_end = stripe_client.extract_current_period_end(sub)
    period_end = stripe_client.period_end_to_datetime(raw_period_end) if raw_period_end else None

    if period_end is None:
        logger.error(
            "[webhook] Could not determine current_period_end for subscription %s — "
            "check Stripe API version / response shape", subscription_id
        )
        return

    await crud.upsert_subscription(
        db=db,
        user_id=user.id,
        stripe_customer_id=customer_id or "",
        stripe_subscription_id=subscription_id,
        stripe_price_id=price_id,
        plan_period=plan,
        status=sub.get("status", "active"),
        current_period_end=period_end,
    )

    # Send access magic link so they can log in immediately
    token = auth.generate_token()
    await crud.create_magic_link(db, token, customer_email.lower(), WELCOME_LINK_TTL_MINUTES)  # 24h
    lang = i18n.normalize(session.get("locale")) or "en"   # set by create_checkout_session
    await email_client.send_subscription_welcome(customer_email, token, lang, WELCOME_LINK_TTL_MINUTES)
    logger.info("[webhook] subscription created for %s plan=%s", customer_email, plan)


# ── Funnel tracking ────────────────────────────────────────────────────────────

# Per worker process, keyed by the keyed IP hash (never stored). nginx also limits
# this path (packaging/adagent-nginx.conf); this is the second, app-level guard.
_event_limiter = funnel.RateLimiter(limit=120, window=600)


@app.post("/api/e", include_in_schema=False)
async def funnel_ingest(request: Request):
    """
    Beacon target for static/funnel.js. Always answers 204 — to the browser a
    tracking failure must be invisible — and does the DB write AFTER the response
    (BackgroundTask), so it can neither slow down nor break a page.
    """
    try:
        if not settings.FUNNEL_ENABLED:
            return Response(status_code=204)
        ua = request.headers.get("user-agent")
        if funnel.is_bot(ua) or not _event_limiter.allow(_client_hash(request)):
            return Response(status_code=204)
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > funnel.MAX_BODY:
            return Response(status_code=204)
        body = bytearray()
        async for chunk in request.stream():           # cap the read for chunked bodies too
            body += chunk
            if len(body) > funnel.MAX_BODY:
                return Response(status_code=204)
        fields = funnel.parse_client_event(bytes(body))
        if not fields:
            return Response(status_code=204)
        return Response(status_code=204, background=BackgroundTask(
            funnel.record, None, device=funnel.device_class(ua),
            session_token=request.cookies.get("adagent_session"), **fields))
    except Exception as exc:
        logger.warning("[funnel] ingest failed: %s", exc)
        return Response(status_code=204)


# ── Internal (loopback + token) ────────────────────────────────────────────────

def _require_internal(request: Request) -> None:
    """
    /internal/* is reachable only from this machine: the nginx allowlist does not
    contain these paths (403 from outside), nginx always adds X-Real-IP so a request
    that came through it is refused here as well, and the caller must send the shared
    INTERNAL_TOKEN. With INTERNAL_TOKEN unset the endpoints do not exist (404).
    """
    token = settings.INTERNAL_TOKEN
    ip = request.client.host if request.client else ""
    if (not token or ip not in ("127.0.0.1", "::1")
            or request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for")):
        raise HTTPException(404)
    if not hmac.compare_digest(request.headers.get("x-internal-token", "").encode(), token.encode()):
        raise HTTPException(403)


async def _funnel_report(db: AsyncSession, day: Optional[str], days: int):
    try:
        start, end, label = funnel.day_window(day, days, settings.REPORT_TIMEZONE)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    rows = await crud.get_funnel_events(db, start, end)
    return funnel.aggregate(rows), label


async def _purge_excluded(db: AsyncSession, extra_sids: list[str] = ()) -> dict:
    """Remove events of excluded (test) accounts that were recorded before the visitor was
    recognised: every session that started a report owned by an excluded e-mail, plus any
    explicitly given sids. Anonymous browsing that never led to a report can't be linked."""
    user_ids = await crud.get_user_ids_by_emails(db, settings.funnel_exclude_emails)
    report_ids = await crud.get_report_ids_for_users(db, user_ids)
    sids = set(await crud.get_funnel_sids_by_report_ids(db, report_ids))
    sids.update(s for s in extra_sids if funnel.is_valid_sid(s))
    deleted = await crud.delete_funnel_events(db, sorted(sids), report_ids) if (sids or report_ids) else 0
    return {"sessions": len(sids), "reports": len(report_ids), "deleted": deleted}


@app.post("/internal/funnel-report/purge-excluded", include_in_schema=False)
async def funnel_purge_excluded(
    request: Request, db: AsyncSession = Depends(get_db), sid: list[str] = Query(default=[]),
):
    """Delete already-recorded events of FUNNEL_EXCLUDE_EMAILS accounts (and optional ?sid=… ids)."""
    _require_internal(request)
    return await _purge_excluded(db, sid)


@app.get("/internal/funnel-report", include_in_schema=False)
async def funnel_report_view(
    request: Request, db: AsyncSession = Depends(get_db),
    date: Optional[str] = None, days: int = 1, format: str = "text",
):
    """Read the funnel: ?date=YYYY-MM-DD (default yesterday) &days=N &format=text|html|json."""
    _require_internal(request)
    report, label = await _funnel_report(db, date, days)
    if format == "json":
        return {"window": label, **report}
    if format == "html":
        return HTMLResponse(funnel.render_html(report, label))
    return PlainTextResponse(funnel.render_text(report, label))


@app.post("/internal/funnel-report/email", include_in_schema=False)
async def funnel_report_email(
    request: Request, db: AsyncSession = Depends(get_db),
    date: Optional[str] = None, days: int = 1,
):
    """Called by the adagent-funnel-report systemd timer: build the report, mail it to
    ADMIN_EMAIL, then purge events older than FUNNEL_RETENTION_DAYS."""
    _require_internal(request)
    if not settings.ADMIN_EMAIL:
        raise HTTPException(503, "ADMIN_EMAIL is not set")
    await _purge_excluded(db)     # test accounts' sessions never show up in the daily numbers
    report, label = await _funnel_report(db, date, days)
    sent = await email_client.send_funnel_report(
        settings.ADMIN_EMAIL, f"AdAgent funnel — {label}",
        funnel.render_text(report, label), funnel.render_html(report, label))
    if not sent:
        raise HTTPException(502, "Report e-mail could not be sent")    # makes the systemd unit fail
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=settings.FUNNEL_RETENTION_DAYS)
    purged = await crud.purge_funnel_events(db, cutoff)
    return {"ok": True, "window": label, "events": report["total_rows"], "purged": purged}


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.APP_VERSION}
