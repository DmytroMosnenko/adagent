from datetime import datetime
from sqlalchemy import (
    BINARY, BigInteger,
    Column, String, Integer, Boolean, DateTime, Text, Enum,
    ForeignKey, Index, text
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.ext.asyncio import AsyncAttrs


import os
import time
from sqlalchemy.types import TypeDecorator


# ── UUID v7 binary storage ─────────────────────────────────────────────────────

def generate_report_id() -> bytes:
    """
    Generate a UUID v7 (time-ordered, 16 bytes).
    First 48 bits = millisecond timestamp → rows sort chronologically by PK.
    """
    ms       = int(time.time() * 1000)
    ts       = ms.to_bytes(6, "big")
    rnd      = os.urandom(10)
    ver_rand = ((0x7 << 12) | (int.from_bytes(rnd[:2], "big") & 0x0FFF)).to_bytes(2, "big")
    var_rand = bytes([0x80 | (rnd[2] & 0x3F)]) + rnd[3:]
    return ts + ver_rand + var_rand  # 16 bytes


class BinaryUUID(TypeDecorator):
    """
    Stores a 16-byte UUID as BINARY(16) in MariaDB.
    Application layer always sees a 32-char lowercase hex string.
    Accepts both hex strings and bytes as input.
    """
    impl     = BINARY(16)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, bytes):
            return value
        try:
            return bytes.fromhex(str(value).replace("-", ""))
        except ValueError:
            return value   # let DB raise

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, (bytes, bytearray)):
            return value.hex()
        return str(value)


class Base(AsyncAttrs, DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    email              = Column(String(255), unique=True, nullable=False, index=True)
    stripe_customer_id = Column(String(255), nullable=True, unique=True)
    created_at         = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"))


class Session(Base):
    __tablename__ = "sessions"

    token      = Column(String(64), primary_key=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    expires_at = Column(DateTime, nullable=False)

    __table_args__ = (Index("idx_session_user", "user_id"),)


class MagicLink(Base):
    __tablename__ = "magic_links"

    token      = Column(String(64), primary_key=True)
    email      = Column(String(255), nullable=False, index=True)
    created_at = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    expires_at = Column(DateTime, nullable=False)
    used       = Column(Boolean, nullable=False, default=False, server_default=text("0"))


class Subscription(Base):
    __tablename__ = "subscriptions"

    id                      = Column(Integer, primary_key=True, autoincrement=True)
    user_id                 = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                                     nullable=False, unique=True)
    stripe_customer_id      = Column(String(255), nullable=False, index=True)
    stripe_subscription_id  = Column(String(255), nullable=False, unique=True)
    stripe_price_id         = Column(String(255), nullable=False)
    plan_period             = Column(String(20), nullable=False, default="monthly")  # monthly/weekly/daily
    status                  = Column(String(50), nullable=False)  # active, past_due, canceled…
    current_period_end      = Column(DateTime, nullable=False)
    created_at              = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    updated_at              = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"),
                                     onupdate=datetime.utcnow)


class Report(Base):
    __tablename__ = "reports"

    id                    = Column(BinaryUUID(), primary_key=True)
    user_id               = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"),
                                   nullable=True, index=True)
    filter_url            = Column(String(2048), nullable=False)
    prompt_preset         = Column(String(64), nullable=True)       # "vehicles"|"realestate"|None
    custom_ad_prompt      = Column(Text, nullable=True)
    custom_summary_prompt = Column(Text, nullable=True)
    status                = Column(
        Enum("pending", "running", "done", "failed"),
        nullable=False, default="pending",
    )
    ads_found             = Column(Integer, nullable=True)           # total links scraped
    ads_analyzed          = Column(Integer, nullable=True)           # actually processed by AI
    is_limited            = Column(Boolean, nullable=False, default=True,
                                   server_default=text("1"))         # True = free tier (5 ads)
    notify_email          = Column(Boolean, nullable=False, default=False,
                                   server_default=text("0"))         # email user on done/failed (logged-in only)
    report_language       = Column(String(8), nullable=True)         # e.g. "pl" — None = model's default (ad's own language)
    client_hash           = Column(String(64), nullable=True, index=True)  # keyed hash of the client IP — free-tier abuse guard only
    report_path           = Column(String(512), nullable=True)       # path to .html (preset)
    result_json           = Column(MEDIUMTEXT, nullable=True)              # raw JSON (custom prompts)
    created_at            = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    finished_at           = Column(DateTime, nullable=True)
    error_message         = Column(Text, nullable=True)


class FunnelEvent(Base):
    """
    Anonymous landing-page funnel event (see service/funnel.py for the privacy
    design). No IP, no cookie, no full URL: `sid` is a random per-tab id,
    `url_host`/`url_label` are a hostname and a coarse pattern label only.
    created_at is naive UTC, like the rest of the app's comparisons.
    """
    __tablename__ = "funnel_events"

    id           = Column(BigInteger().with_variant(Integer, "sqlite"),
                          primary_key=True, autoincrement=True)
    created_at   = Column(DateTime, nullable=False, default=datetime.utcnow)
    sid          = Column(String(32), nullable=True)
    event        = Column(String(32), nullable=False)
    has_gclid    = Column(Boolean, nullable=True)
    device       = Column(String(8), nullable=False, default="other")   # mobile|desktop|other
    ui_lang      = Column(String(4), nullable=True)
    utm_source   = Column(String(64), nullable=True)
    utm_campaign = Column(String(128), nullable=True)
    ref_host     = Column(String(128), nullable=True)
    url_host     = Column(String(16), nullable=True)
    url_label    = Column(String(48), nullable=True)
    detail       = Column(String(64), nullable=True)   # example id / invalid reason / report id

    __table_args__ = (
        Index("ix_funnel_created_event", "created_at", "event"),
        Index("ix_funnel_sid", "sid"),
        Index("ix_funnel_event_detail", "event", "detail"),
    )
