"""
Anonymous first-party funnel tracking for the landing page.

Privacy design (keep in sync with the privacy policy):
  * no cookies, no IP address stored, no full user URLs — only the hostname of a
    pasted link plus a coarse pattern label such as "motoryzacja:filter";
  * the visitor is a random per-tab id kept in sessionStorage (static/funnel.js);
  * the gclid *value* never leaves the browser — only a has_gclid boolean;
  * device class is derived from the User-Agent header and the header is dropped;
  * rows older than FUNNEL_RETENTION_DAYS are purged by the daily report job.

This module is deliberately free of FastAPI / SQLAlchemy imports at import time:
the pure logic (validation, classification, aggregation, rendering) is unit-testable
on its own, and the DB writers import crud/db lazily. Every writer NEVER raises —
a logging failure must never break a page or a request.
"""
from __future__ import annotations

import html
import json
import re
import time
from collections import Counter, defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, urlsplit

from .logger import get_logger

logger = get_logger(__name__)

# ── Events ─────────────────────────────────────────────────────────────────────

# Funnel order — also the row order of the report.
STEPS: tuple[str, ...] = (
    "landing",
    "demo_open",
    "demo_pick",
    "demo_cta_click",
    "sample_report_click",
    "url_field_focus",
    "url_paste",
    "invalid_url",
    "analyze_submit",
    "analyze_started",
    "analyze_done",
    "analyze_failed",
)
SERVER_ONLY_EVENTS = frozenset({"analyze_started", "analyze_done", "analyze_failed"})
CLIENT_EVENTS = frozenset(STEPS) - SERVER_ONLY_EVENTS

MAX_BODY = 2048            # bytes accepted by the ingest endpoint
ALLOWED_HOSTS = ("olx.pl", "otomoto.pl", "otodom.pl")

_SID_RE     = re.compile(r"^[a-f0-9]{16,32}$")
_DETAIL_RE  = re.compile(r"^[A-Za-z0-9_:.\-]{1,64}$")
_UTM_RE     = re.compile(r"[^A-Za-z0-9_.\-+ ]")
_HOST_RE    = re.compile(r"^[a-z0-9.\-]{1,128}$")
_LABEL_RE   = re.compile(r"^[a-z\-]{1,20}:(ad|filter|category|other)$")
_LANGS      = ("en", "pl")

_BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|headless|lighthouse|phantom|pagespeed|gtmetrix|"
    r"python-requests|python-httpx|httpx|curl|wget|go-http|java/|"
    r"adsbot|google-adwords|mediapartners|facebookexternalhit|preview|monitor|uptime",
    re.I,
)
_MOBILE_RE = re.compile(r"Mobi|Android|iPhone|iPad|iPod|Windows Phone|Opera Mini", re.I)


# ── Classification helpers ─────────────────────────────────────────────────────

def is_bot(user_agent: Optional[str]) -> bool:
    """Empty User-Agent counts as a bot: real browsers always send one."""
    return not user_agent or bool(_BOT_RE.search(user_agent))


def device_class(user_agent: Optional[str]) -> str:
    """'mobile' | 'desktop' | 'other' (no/empty UA)."""
    if not user_agent:
        return "other"
    return "mobile" if _MOBILE_RE.search(user_agent) else "desktop"


def _host_of(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _allowed_host(host: str) -> Optional[str]:
    for h in ALLOWED_HOSTS:
        if host == h or host.endswith("." + h):
            return h
    return None


_CATEGORY_SEGMENTS = frozenset({
    "motoryzacja", "nieruchomosci", "osobowe", "dostawcze", "motocykle", "wyniki",
    "sprzedaz", "wynajem", "mieszkanie", "dom", "oferta", "d",
})


def url_label(url: str) -> tuple[str, str]:
    """
    Reduce a user-pasted URL to (host, coarse label). Nothing else about the URL
    is kept. host is one of ALLOWED_HOSTS or "other"; label is "<segment>:<kind>"
    with kind in ad | filter | category | other, e.g. ("olx.pl", "motoryzacja:filter").
    The 'ad' kind is what tells us whether people paste a single listing (see
    the single-ad investigation in the report).
    """
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return "other", "other:other"
    host = _allowed_host(_host_of(url or ""))
    if not host:
        return "other", "other:other"

    segs = [s.lower() for s in parts.path.split("/") if s]
    query = parts.query.lower()
    seg = next((s for s in segs[:3] if s in _CATEGORY_SEGMENTS), "other")

    if host == "olx.pl":
        is_ad = segs[:2] == ["d", "oferta"]
    elif host == "otomoto.pl":
        is_ad = "oferta" in segs
    else:  # otodom.pl
        is_ad = "oferta" in segs[:2]

    if is_ad:
        kind = "ad"
    elif "search%5b" in query or "search[" in query or any(s.startswith("q-") for s in segs) \
            or (host == "otodom.pl" and ("wyniki" in segs or query)):
        kind = "filter"
    elif segs:
        kind = "category"
    else:
        kind = "other"
    return host, f"{seg}:{kind}"


def invalid_url_reason(url: str) -> str:
    """Reason code for the server-side invalid_url path (mirrors /analyze's check)."""
    url = (url or "").strip()
    if not url:
        return "empty"
    if not url.startswith("http"):
        return "no_scheme"
    return "bad_host"


# ── Client payload validation ──────────────────────────────────────────────────

def _clean(value: Any, pattern: re.Pattern, maxlen: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()[:maxlen]
    return value if value and pattern.match(value) else None


def _clean_utm(value: Any, maxlen: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = _UTM_RE.sub("", value.strip())[:maxlen]
    return value or None


def parse_client_event(raw: bytes) -> Optional[dict]:
    """
    Validate a beacon body. Returns the cleaned fields for the funnel_events row,
    or None for anything malformed / disallowed. Never raises.
    """
    try:
        if not raw or len(raw) > MAX_BODY:
            return None
        data = json.loads(raw.decode("utf-8", "strict"))
        if not isinstance(data, dict):
            return None
        event = data.get("event")
        if event not in CLIENT_EVENTS:
            return None
        sid = _clean(data.get("sid"), _SID_RE, 32)
        if not sid:
            return None

        host = data.get("host")
        host = host if host in (*ALLOWED_HOSTS, "other") else None
        label = _clean(data.get("label"), _LABEL_RE, 32)
        if host is None:
            label = None

        lang = data.get("lang")
        ref = data.get("ref")
        ref = ref.lower()[:128] if isinstance(ref, str) and _HOST_RE.match(ref.lower()[:128]) else None

        gclid = data.get("gclid")
        return {
            "event":        event,
            "sid":          sid,
            "has_gclid":    gclid if isinstance(gclid, bool) else None,
            "ui_lang":      lang if lang in _LANGS else None,
            "utm_source":   _clean_utm(data.get("utm_source"), 64),
            "utm_campaign": _clean_utm(data.get("utm_campaign"), 128),
            "ref_host":     ref,
            "url_host":     host,
            "url_label":    label,
            "detail":       _clean(data.get("detail"), _DETAIL_RE, 64),
        }
    except Exception:  # malformed JSON, bad unicode, anything
        return None


# ── In-memory rate limiter (per worker process) ────────────────────────────────

class RateLimiter:
    """Sliding window: at most `limit` hits per `window` seconds per key."""

    def __init__(self, limit: int, window: float, max_keys: int = 20000):
        self.limit, self.window, self.max_keys = limit, window, max_keys
        self._hits: dict[str, deque] = {}

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        if len(self._hits) > self.max_keys:
            self._prune(now)
        q = self._hits.setdefault(key, deque())
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True

    def _prune(self, now: float) -> None:
        for k in [k for k, q in self._hits.items() if not q or q[-1] <= now - self.window]:
            del self._hits[k]
        if len(self._hits) > self.max_keys:   # still too many: drop everything
            self._hits.clear()


# ── DB writers (never raise) ───────────────────────────────────────────────────

_last_warn = 0.0


def _warn_throttled(msg: str, exc: BaseException) -> None:
    """A broken table must not turn into a log flood — one warning per minute."""
    global _last_warn
    now = time.monotonic()
    if now - _last_warn > 60:
        _last_warn = now
        logger.warning("[funnel] %s: %s", msg, exc)


def _enabled() -> bool:
    from .config import settings
    return bool(settings.FUNNEL_ENABLED)


def _factory(factory):
    if factory is not None:
        return factory
    from .db import async_session
    return async_session


async def record(factory=None, *, device: str = "other", **fields) -> None:
    """Insert one already-validated event row. Swallows every exception."""
    try:
        if not _enabled():
            return
        from . import crud
        async with _factory(factory)() as db:
            await crud.add_funnel_event(db, device=device, **fields)
    except Exception as exc:
        _warn_throttled("record failed", exc)


async def record_server_event(
    factory, event: str, sid: Optional[str], user_agent: Optional[str], *,
    detail: Optional[str] = None, url: Optional[str] = None, ui_lang: Optional[str] = None,
) -> None:
    """
    Server-side event (invalid_url, analyze_started). The sid comes from a hidden
    form field filled by funnel.js; without JS there is no sid and the row is still
    written, just unattributed. has_gclid / utm are inherited from the session's
    landing row so the report can split ad vs organic.
    """
    try:
        if not _enabled() or is_bot(user_agent):
            return
        from . import crud
        sid = _clean(sid, _SID_RE, 32)
        host = label = None
        if url:
            host, label = url_label(url)
        extra: dict = {}
        async with _factory(factory)() as db:
            if sid:
                landing = await crud.get_funnel_landing(db, sid)
                if landing is not None:
                    extra = {"has_gclid": landing.has_gclid, "utm_source": landing.utm_source,
                             "utm_campaign": landing.utm_campaign}
            await crud.add_funnel_event(
                db, event=event, sid=sid, device=device_class(user_agent), ui_lang=ui_lang,
                url_host=host, url_label=label, detail=_clean(detail, _DETAIL_RE, 64), **extra,
            )
    except Exception as exc:
        _warn_throttled("record_server_event failed", exc)


async def record_report_outcome(factory, report_id: str, event: str) -> None:
    """
    analyze_done / analyze_failed, written from tasks.py. The session attribution is
    copied from this report's analyze_started row (matched by report id), so the
    reports table needs no extra column. No started row -> nothing is written.
    """
    try:
        if not _enabled():
            return
        from . import crud
        async with _factory(factory)() as db:
            started = await crud.get_funnel_event_by_detail(db, "analyze_started", report_id)
            if started is None:
                return
            await crud.add_funnel_event(
                db, event=event, sid=started.sid, has_gclid=started.has_gclid,
                device=started.device, ui_lang=started.ui_lang, utm_source=started.utm_source,
                utm_campaign=started.utm_campaign, url_host=started.url_host,
                url_label=started.url_label, detail=report_id,
            )
    except Exception as exc:
        _warn_throttled("record_report_outcome failed", exc)


# ── Reporting window ───────────────────────────────────────────────────────────

def day_window(day: Optional[str], days: int, tz_name: str,
               now: Optional[datetime] = None) -> tuple[datetime, datetime, str]:
    """
    (start, end, label) as naive-UTC datetimes, covering `days` full local days that
    END with `day` (YYYY-MM-DD in `tz_name`); default = yesterday, i.e. the last
    complete day. Raises ValueError on a bad date.
    """
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(tz_name)
    days = max(1, min(int(days), 90))
    if day:
        last = date.fromisoformat(day)
    else:
        local_now = (now or datetime.now(timezone.utc)).astimezone(tz)
        last = local_now.date() - timedelta(days=1)
    first = last - timedelta(days=days - 1)

    def to_utc(d: date) -> datetime:
        return datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)

    label = last.isoformat() if days == 1 else f"{first.isoformat()} → {last.isoformat()}"
    return to_utc(first), to_utc(last + timedelta(days=1)), f"{label} ({tz_name})"


# ── Aggregation ────────────────────────────────────────────────────────────────

def _source(has_gclid: Optional[bool]) -> str:
    return "ads" if has_gclid is True else "organic" if has_gclid is False else "unknown"


COLUMNS: tuple[tuple[str, Any], ...] = (
    ("All",         lambda s, d: True),
    ("Ads",         lambda s, d: s == "ads"),
    ("Organic",     lambda s, d: s == "organic"),
    ("Mobile",      lambda s, d: d == "mobile"),
    ("Desktop",     lambda s, d: d == "desktop"),
    ("Ads·mobile",  lambda s, d: s == "ads" and d == "mobile"),
)


def aggregate(rows: Iterable[Any]) -> dict:
    """
    rows: objects with .sid .event .has_gclid .device .detail .url_host .url_label
    (ORM rows or SimpleNamespace). Returns
      columns: {name: {event: {"sessions": n, "events": n}}}
      invalid_reasons / demo_picks / paste_patterns: Counter dicts
      unattributed: events with no sid (JS off / blocked) per event
    "sessions" = distinct sid that fired the event at least once in the window.
    """
    sess: dict[str, dict[str, set]] = {c: defaultdict(set) for c, _ in COLUMNS}
    evs:  dict[str, Counter]        = {c: Counter() for c, _ in COLUMNS}
    invalid, picks, paste = Counter(), Counter(), Counter()
    unattributed: Counter = Counter()
    n = 0

    for r in rows:
        n += 1
        src, dev = _source(r.has_gclid), r.device or "other"
        if not r.sid:
            unattributed[r.event] += 1
        for name, pred in COLUMNS:
            if pred(src, dev):
                evs[name][r.event] += 1
                if r.sid:
                    sess[name][r.event].add(r.sid)
        if r.event == "invalid_url" and r.detail:
            invalid[r.detail] += 1
        elif r.event == "demo_pick" and r.detail:
            picks[r.detail] += 1
        elif r.event == "url_paste" and r.url_host:
            paste[f"{r.url_host} {r.url_label or ''}".strip()] += 1

    columns = {
        name: {ev: {"sessions": len(sess[name][ev]), "events": evs[name][ev]} for ev in STEPS}
        for name, _ in COLUMNS
    }
    return {
        "total_rows": n, "columns": columns, "invalid_reasons": dict(invalid.most_common()),
        "demo_picks": dict(picks.most_common()), "paste_patterns": dict(paste.most_common()),
        "unattributed": dict(unattributed),
    }


# ── Rendering ──────────────────────────────────────────────────────────────────

_STEP_HELP = {
    "landing":             "page loaded (JS ran, not a bot)",
    "demo_open":           "demo block scrolled into view",
    "demo_pick":           "clicked an example",
    "demo_cta_click":      "clicked CTA after the demo",
    "sample_report_click": "clicked sample report link/screenshot",
    "url_field_focus":     "focused the URL field",
    "url_paste":           "pasted into the URL field",
    "invalid_url":         "rejected link (client or server)",
    "analyze_submit":      "pressed Analyze",
    "analyze_started":     "report created on server",
    "analyze_done":        "report finished",
    "analyze_failed":      "report failed",
}


def _cell(c: dict, landing: int) -> str:
    s = c["sessions"]
    if not landing:
        return str(s)
    return f"{s} ({round(100 * s / landing)}%)" if s else "0"


def render_text(report: dict, label: str) -> str:
    cols = list(report["columns"])
    lines = [f"AdAgent funnel — {label}", "",
             "Cells: distinct sessions (% of that column's landing sessions).", ""]
    w0 = max(len(s) for s in STEPS) + 2
    lines.append("step".ljust(w0) + "".join(c.rjust(14) for c in cols))
    for step in STEPS:
        row = step.ljust(w0)
        for c in cols:
            landing = report["columns"][c]["landing"]["sessions"]
            cell = report["columns"][c][step]
            row += (str(cell["sessions"]) if step == "landing" else _cell(cell, landing)).rjust(14)
        lines.append(row)
    lines += ["", "Raw event counts (all columns): " + ", ".join(
        f"{s}={report['columns']['All'][s]['events']}" for s in STEPS)]
    for title, key in (("invalid_url reasons", "invalid_reasons"), ("demo picks", "demo_picks"),
                       ("pasted link patterns (host label)", "paste_patterns"),
                       ("events with no session id (JS off / blocked)", "unattributed")):
        if report[key]:
            lines.append(f"{title}: " + ", ".join(f"{k}={v}" for k, v in report[key].items()))
    lines += ["", "Ads = landing URL had a gclid/wbraid/gbraid. Organic = it did not (direct, search, "
                  "or Ads with auto-tagging off). Mobile/Desktop come from the User-Agent."]
    return "\n".join(lines)


def render_html(report: dict, label: str) -> str:
    cols = list(report["columns"])
    th = "padding:6px 10px;text-align:right;border-bottom:2px solid #1e3a5f;white-space:nowrap"
    td = "padding:6px 10px;text-align:right;border-bottom:1px solid #e2e8f0;white-space:nowrap"
    out = [f'<html><body style="font-family:sans-serif;color:#1e293b;max-width:760px">'
           f'<h2 style="color:#1e3a5f">AdAgent funnel — {html.escape(label)}</h2>'
           '<p style="color:#64748b;font-size:13px">Distinct sessions per step '
           "(% of that column's landing sessions).</p>"
           '<table style="border-collapse:collapse;font-size:14px"><tr>'
           f'<th style="{th};text-align:left">step</th>'
           + "".join(f'<th style="{th}">{html.escape(c)}</th>' for c in cols) + "</tr>"]
    for step in STEPS:
        out.append(f'<tr><td style="{td};text-align:left" title="{html.escape(_STEP_HELP[step])}">'
                   f"<b>{step}</b></td>")
        for c in cols:
            landing = report["columns"][c]["landing"]["sessions"]
            cell = report["columns"][c][step]
            txt = str(cell["sessions"]) if step == "landing" else _cell(cell, landing)
            out.append(f'<td style="{td}">{html.escape(txt)}</td>')
        out.append("</tr>")
    out.append("</table>")
    for title, key in (("invalid_url reasons", "invalid_reasons"), ("Demo picks", "demo_picks"),
                       ("Pasted link patterns", "paste_patterns"),
                       ("Events with no session id (JS off / blocked)", "unattributed")):
        if report[key]:
            items = ", ".join(f"{html.escape(k)} = {v}" for k, v in report[key].items())
            out.append(f'<p style="font-size:13px"><b>{html.escape(title)}:</b> {items}</p>')
    out.append('<p style="color:#94a3b8;font-size:12px">Ads = landing URL had a gclid/wbraid/gbraid; '
               "Organic = it did not. Mobile/Desktop from the User-Agent.</p></body></html>")
    return "".join(out)
