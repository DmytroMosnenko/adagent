from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User, Session, MagicLink, Subscription, Report, FunnelEvent


# ── Users ──────────────────────────────────────────────────────────────────────

async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    r = await db.execute(select(User).where(User.email == email))
    return r.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    r = await db.execute(select(User).where(User.id == user_id))
    return r.scalar_one_or_none()


async def get_or_create_user(db: AsyncSession, email: str) -> tuple[User, bool]:
    user = await get_user_by_email(db, email)
    if user:
        return user, False
    user = User(email=email)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user, True


async def set_stripe_customer(db: AsyncSession, user_id: int, customer_id: str) -> None:
    await db.execute(
        update(User).where(User.id == user_id).values(stripe_customer_id=customer_id)
    )
    await db.commit()


# ── Sessions ───────────────────────────────────────────────────────────────────

async def create_session(db: AsyncSession, token: str, user_id: int, ttl_days: int) -> Session:
    s = Session(
        token=token,
        user_id=user_id,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=ttl_days),
    )
    db.add(s)
    await db.commit()
    return s


async def get_session(db: AsyncSession, token: str) -> Optional[Session]:
    r = await db.execute(
        select(Session).where(Session.token == token, Session.expires_at > datetime.now(timezone.utc).replace(tzinfo=None))
    )
    return r.scalar_one_or_none()


async def delete_session(db: AsyncSession, token: str) -> None:
    s = await db.get(Session, token)
    if s:
        await db.delete(s)
        await db.commit()


# ── Magic links ────────────────────────────────────────────────────────────────

async def create_magic_link(db: AsyncSession, token: str, email: str, ttl_minutes: int) -> MagicLink:
    ml = MagicLink(
        token=token,
        email=email,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=ttl_minutes),
    )
    db.add(ml)
    await db.commit()
    return ml


async def consume_magic_link(db: AsyncSession, token: str) -> Optional[str]:
    """Return email if the token is valid and unused, then mark it used."""
    r = await db.execute(
        select(MagicLink).where(
            MagicLink.token == token,
            MagicLink.used == False,
            MagicLink.expires_at > datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    ml = r.scalar_one_or_none()
    if not ml:
        return None
    await db.execute(
        update(MagicLink).where(MagicLink.token == token).values(used=True)
    )
    await db.commit()
    return ml.email


# ── Subscriptions ──────────────────────────────────────────────────────────────

async def upsert_subscription(
    db: AsyncSession,
    user_id: int,
    stripe_customer_id: str,
    stripe_subscription_id: str,
    stripe_price_id: str,
    plan_period: str,
    status: str,
    current_period_end: datetime,
) -> Subscription:
    r = await db.execute(select(Subscription).where(Subscription.user_id == user_id))
    sub = r.scalar_one_or_none()
    if sub:
        sub.stripe_subscription_id = stripe_subscription_id
        sub.stripe_price_id = stripe_price_id
        sub.plan_period = plan_period
        sub.status = status
        sub.current_period_end = current_period_end
        sub.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        sub = Subscription(
            user_id=user_id,
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
            stripe_price_id=stripe_price_id,
            plan_period=plan_period,
            status=status,
            current_period_end=current_period_end,
        )
        db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return sub


async def update_subscription_status(
    db: AsyncSession, stripe_subscription_id: str, status: str,
    current_period_end: Optional[datetime] = None,
) -> None:
    vals: dict = {"status": status, "updated_at": datetime.now(timezone.utc).replace(tzinfo=None)}
    if current_period_end:
        vals["current_period_end"] = current_period_end
    await db.execute(
        update(Subscription)
        .where(Subscription.stripe_subscription_id == stripe_subscription_id)
        .values(**vals)
    )
    await db.commit()


async def get_subscription(db: AsyncSession, user_id: int) -> Optional[Subscription]:
    r = await db.execute(select(Subscription).where(Subscription.user_id == user_id))
    return r.scalar_one_or_none()


async def is_subscribed(db: AsyncSession, user_id: int) -> bool:
    sub = await get_subscription(db, user_id)
    if not sub:
        return False
    if sub.status not in ("active", "trialing"):
        return False
    return sub.current_period_end > datetime.now(timezone.utc).replace(tzinfo=None)


# ── Reports ────────────────────────────────────────────────────────────────────

async def create_report(db: AsyncSession, **kwargs) -> Report:
    r = Report(**kwargs)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def count_free_reports_since(
    db: AsyncSession, since: datetime, client_hash: Optional[str] = None,
) -> int:
    """
    Free-tier (is_limited) reports created since `since` — for one client when
    `client_hash` is given, otherwise across all clients. Used by the daily
    abuse / budget guard in /analyze. `since` is naive UTC like created_at.
    """
    q = select(func.count()).select_from(Report).where(
        Report.is_limited.is_(True), Report.created_at >= since,
    )
    if client_hash:
        q = q.where(Report.client_hash == client_hash)
    return int((await db.execute(q)).scalar() or 0)


async def get_report(db: AsyncSession, report_id: str) -> Optional[Report]:
    # Validate hex length before hitting the DB (TypeDecorator would raise ValueError)
    if not report_id or len(report_id) != 32:
        return None
    try:
        bytes.fromhex(report_id)
    except ValueError:
        return None
    r = await db.execute(select(Report).where(Report.id == report_id))
    return r.scalar_one_or_none()


async def update_report(db: AsyncSession, report_id: str, **kwargs) -> None:
    await db.execute(update(Report).where(Report.id == report_id).values(**kwargs))
    await db.commit()


async def set_report_notify_email(
    db: AsyncSession, report_id: str, user_id: int, enabled: bool,
) -> Optional[Report]:
    """
    Toggle the "email me when finished" flag for a report.

    Only allowed for the report's owner. If the report is anonymous
    (user_id is NULL — e.g. it was started before sign-in), the caller
    claims it, which also makes it show up in their /history.
    Returns the updated report, or None if not found / not owned by
    this user.
    """
    report = await get_report(db, report_id)
    if not report:
        return None
    if report.user_id is not None and report.user_id != user_id:
        return None

    values = {"notify_email": enabled}
    if report.user_id is None:
        values["user_id"] = user_id

    await db.execute(update(Report).where(Report.id == report_id).values(**values))
    await db.commit()
    return await get_report(db, report_id)


async def get_user_reports(db: AsyncSession, user_id: int, limit: int = 50) -> list[Report]:
    r = await db.execute(
        select(Report)
        .where(Report.user_id == user_id, Report.status == "done")
        .order_by(Report.created_at.desc())
        .limit(limit)
    )
    return list(r.scalars().all())


async def fail_stale_reports(db: AsyncSession) -> None:
    """On startup: mark any 'running' reports as failed (server was restarted)."""
    await db.execute(
        update(Report)
        .where(Report.status == "running")
        .values(status="failed", error_message="Server restarted during analysis.")
    )
    await db.commit()


# ── Funnel events ──────────────────────────────────────────────────────────────

async def add_funnel_event(db: AsyncSession, **fields) -> None:
    fields.setdefault("created_at", datetime.now(timezone.utc).replace(tzinfo=None))
    db.add(FunnelEvent(**fields))
    await db.commit()


async def get_funnel_landing(db: AsyncSession, sid: str) -> Optional[FunnelEvent]:
    """First landing row of a session — source of has_gclid/utm for server-side events."""
    r = await db.execute(
        select(FunnelEvent)
        .where(FunnelEvent.sid == sid, FunnelEvent.event == "landing")
        .order_by(FunnelEvent.id).limit(1)
    )
    return r.scalar_one_or_none()


async def get_funnel_event_by_detail(db: AsyncSession, event: str, detail: str) -> Optional[FunnelEvent]:
    r = await db.execute(
        select(FunnelEvent)
        .where(FunnelEvent.event == event, FunnelEvent.detail == detail)
        .order_by(FunnelEvent.id).limit(1)
    )
    return r.scalar_one_or_none()


async def get_funnel_events(db: AsyncSession, start: datetime, end: datetime) -> list[FunnelEvent]:
    """Events with start <= created_at < end (naive UTC)."""
    r = await db.execute(
        select(FunnelEvent)
        .where(FunnelEvent.created_at >= start, FunnelEvent.created_at < end)
        .order_by(FunnelEvent.id)
    )
    return list(r.scalars().all())


async def purge_funnel_events(db: AsyncSession, before: datetime) -> int:
    r = await db.execute(delete(FunnelEvent).where(FunnelEvent.created_at < before))
    await db.commit()
    return int(r.rowcount or 0)


async def get_user_ids_by_emails(db: AsyncSession, emails) -> list[int]:
    if not emails:
        return []
    r = await db.execute(select(User.id).where(func.lower(User.email).in_(list(emails))))
    return [row[0] for row in r.all()]


async def get_report_ids_for_users(db: AsyncSession, user_ids: list[int]) -> list[str]:
    """Hex report ids owned by these users (BinaryUUID already yields the 32-char hex form)."""
    if not user_ids:
        return []
    r = await db.execute(select(Report.id).where(Report.user_id.in_(user_ids)))
    return [row[0] for row in r.all()]


async def get_funnel_sids_by_report_ids(db: AsyncSession, report_ids: list[str]) -> list[str]:
    """Session ids that started one of these reports (analyze_started.detail = report id)."""
    sids: set[str] = set()
    for i in range(0, len(report_ids), 500):
        r = await db.execute(
            select(FunnelEvent.sid)
            .where(FunnelEvent.event == "analyze_started",
                   FunnelEvent.detail.in_(report_ids[i:i + 500]),
                   FunnelEvent.sid.is_not(None))
        )
        sids.update(row[0] for row in r.all())
    return sorted(sids)


async def delete_funnel_events(db: AsyncSession, sids: list[str], report_ids: list[str]) -> int:
    """Delete every event of these sessions, plus the report-bound events (started/done/failed,
    matched by report id) which may have no sid when the visitor had JS off."""
    deleted = 0
    for i in range(0, len(sids), 500):
        r = await db.execute(delete(FunnelEvent).where(FunnelEvent.sid.in_(sids[i:i + 500])))
        deleted += int(r.rowcount or 0)
    for i in range(0, len(report_ids), 500):
        r = await db.execute(delete(FunnelEvent).where(
            FunnelEvent.event.in_(("analyze_started", "analyze_done", "analyze_failed")),
            FunnelEvent.detail.in_(report_ids[i:i + 500])))
        deleted += int(r.rowcount or 0)
    await db.commit()
    return deleted
