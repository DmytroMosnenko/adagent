"""
AdAgent — FastAPI server
Routes: web UI + Stripe webhooks + JSON status API
"""
from __future__ import annotations

import json
import uuid
from service.models import generate_report_id
from pathlib import Path
from typing import Optional

import stripe
from contextlib import asynccontextmanager
from fastapi import (
    BackgroundTasks, Cookie, Depends, FastAPI, Form,
    HTTPException, Request, Response,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from service.config import settings
from service.db import async_session, get_db
from service.logger import get_logger
from service import crud, auth, email_client, stripe_client
from service.tasks import run_analysis

logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.REPORT_STORAGE_PATH).mkdir(parents=True, exist_ok=True)
    async with async_session() as db:
        await crud.fail_stale_reports(db)
    logger.info("AdAgent %s started", settings.APP_VERSION)
    yield

app = FastAPI(title="AdAgent", version=settings.APP_VERSION,
              docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ── Preset definitions (for the UI) ───────────────────────────────────────────
PRESETS = {
    "vehicles": {
        "label":       "Vehicles 🚗",
        "description": "Cars, motorcycles, trucks on OLX / Otomoto",
    },
    "realestate": {
        "label":       "Real Estate 🏠",
        "description": "Apartments, houses, plots on OLX / Otodom",
    },
}


# ── Startup ────────────────────────────────────────────────────────────────────


# ── Helpers ────────────────────────────────────────────────────────────────────

def _tpl(name: str, request: Request, extra: dict | None = None):
    ctx = {"request": request, "presets": PRESETS, "settings": settings}
    ctx.update(extra or {})
    return templates.TemplateResponse(request=request, name=name, context=ctx)


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

@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    adagent_session: Optional[str] = Cookie(default=None),
    error: Optional[str] = None,
):
    user, subscribed = await _resolve_user_and_sub(adagent_session, db)
    return _tpl("index.html", request, {
        "user": user, "subscribed": subscribed, "error": error,
    })


# ── Analyze form submit ────────────────────────────────────────────────────────

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
):
    # Validate URL
    allowed_hosts = ("olx.pl", "otomoto.pl", "otodom.pl")
    filter_url = filter_url.strip()
    if not any(h in filter_url for h in allowed_hosts) or not filter_url.startswith("http"):
        return RedirectResponse("/?error=Invalid+URL.+Must+be+from+olx.pl,+otomoto.pl+or+otodom.pl", 303)

    # Validate preset
    if prompt_preset and prompt_preset not in PRESETS:
        prompt_preset = None

    # Custom prompt requires both fields
    if not prompt_preset:
        if not custom_ad_prompt or not custom_summary_prompt:
            return RedirectResponse("/?error=Please+provide+both+custom+prompts+or+select+a+preset.", 303)

    # Resolve user & subscription
    user, subscribed = await _resolve_user_and_sub(adagent_session, db)

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
        is_limited=not subscribed,
        status="pending",
    )

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
        return _tpl("error.html", request, {
            "message": report.error_message or "Analysis failed.",
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
    }


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
    await email_client.send_magic_link(email, token)
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
    db: AsyncSession = Depends(get_db),
    adagent_session: Optional[str] = Cookie(default=None),
    plan: str = Form(default="monthly"),
):
    user, _ = await _resolve_user_and_sub(adagent_session, db)
    customer_email = user.email if user else None
    try:
        url = await stripe_client.create_checkout_session(plan, customer_email)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return RedirectResponse(url, status_code=303)


@app.get("/subscribe/success", response_class=HTMLResponse)
async def subscribe_success(
    request: Request,
    db: AsyncSession = Depends(get_db),
    adagent_session: Optional[str] = Cookie(default=None),
):
    user, subscribed = await _resolve_user_and_sub(adagent_session, db)
    return _tpl("subscribe_success.html", request, {
        "user": user, "subscribed": subscribed,
    })


# ── Stripe webhook ─────────────────────────────────────────────────────────────

@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe_client.parse_webhook(payload, sig_header)
    except stripe.SignatureVerificationError:
        logger.warning("[webhook] invalid signature")
        raise HTTPException(400, "Invalid signature")

    etype = event["type"]
    logger.info("[webhook] %s", etype)

    if etype == "checkout.session.completed":
        session = event["data"]["object"]
        await _handle_checkout_completed(db, session)

    elif etype in ("customer.subscription.updated", "customer.subscription.deleted"):
        sub = event["data"]["object"]
        status = "canceled" if etype.endswith("deleted") else sub["status"]
        period_end = stripe_client.period_end_to_datetime(sub["current_period_end"])
        await crud.update_subscription_status(db, sub["id"], status, period_end)

    elif etype == "invoice.payment_failed":
        inv = event["data"]["object"]
        sub_id = inv.get("subscription")
        if sub_id:
            await crud.update_subscription_status(db, sub_id, "past_due")

    return {"ok": True}


async def _handle_checkout_completed(db: AsyncSession, session: dict) -> None:
    customer_email = session.get("customer_details", {}).get("email") or session.get("customer_email")
    customer_id    = session.get("customer")
    subscription_id = session.get("subscription")

    if not customer_email or not subscription_id:
        logger.warning("[webhook] checkout.completed missing email or subscription_id")
        return

    # Find or create user
    user, created = await crud.get_or_create_user(db, customer_email.lower())
    if customer_id and not user.stripe_customer_id:
        await crud.set_stripe_customer(db, user.id, customer_id)

    # Retrieve subscription details from Stripe
    import asyncio
    import stripe as _stripe
    sub = await asyncio.to_thread(_stripe.Subscription.retrieve, subscription_id)
    price_id   = sub["items"]["data"][0]["price"]["id"]
    plan       = stripe_client.detect_plan_period(price_id)
    period_end = stripe_client.period_end_to_datetime(sub["current_period_end"])

    await crud.upsert_subscription(
        db,
        user_id=user.id,
        stripe_customer_id=customer_id or "",
        stripe_subscription_id=subscription_id,
        stripe_price_id=price_id,
        plan_period=plan,
        status=sub["status"],
        current_period_end=period_end,
    )

    # Send access magic link so they can log in immediately
    token = auth.generate_token()
    await crud.create_magic_link(db, token, customer_email.lower(), 60 * 24)  # 24h
    await email_client.send_subscription_welcome(customer_email, token)
    logger.info("[webhook] subscription created for %s plan=%s", customer_email, plan)


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.APP_VERSION}
