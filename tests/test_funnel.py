"""service/funnel.py — pure logic: classification, validation, rate limit, window, aggregation, rendering.
No DB / FastAPI needed (see test_funnel_routes.py for those)."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from service import funnel

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


# ── device / bot ───────────────────────────────────────────────────────────────

def test_device_class():
    assert funnel.device_class(MOBILE_UA) == "mobile"
    assert funnel.device_class("Mozilla/5.0 (Linux; Android 14; Pixel 8) Chrome/126 Mobile Safari/537.36") == "mobile"
    assert funnel.device_class(DESKTOP_UA) == "desktop"
    assert funnel.device_class("") == "other"
    assert funnel.device_class(None) == "other"


@pytest.mark.parametrize("ua", [None, "", "Googlebot/2.1", "AdsBot-Google-Mobile", "python-httpx/0.27",
                                "curl/8.0", "HeadlessChrome/126", "Mozilla/5.0 Chrome-Lighthouse"])
def test_bots_are_detected(ua):
    assert funnel.is_bot(ua)


def test_real_browsers_are_not_bots():
    assert not funnel.is_bot(MOBILE_UA)
    assert not funnel.is_bot(DESKTOP_UA)


# ── url_label: only host + coarse label survive ────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://www.olx.pl/motoryzacja/samochody/q-mini/?search%5Bfilter_float_price:to%5D=60000",
     ("olx.pl", "motoryzacja:filter")),
    ("https://www.olx.pl/motoryzacja/samochody/", ("olx.pl", "motoryzacja:category")),
    ("https://www.olx.pl/d/oferta/renault-megane-CID5-IDabc.html", ("olx.pl", "d:ad")),
    ("https://www.olx.pl/", ("olx.pl", "other:other")),
    ("https://www.otomoto.pl/osobowe/mini/?search%5Bfilter_float_year:from%5D=2018", ("otomoto.pl", "osobowe:filter")),
    ("https://www.otomoto.pl/osobowe/oferta/mini-cooper-ID6Abc.html", ("otomoto.pl", "osobowe:ad")),
    ("https://www.otodom.pl/pl/wyniki/sprzedaz/mieszkanie/malopolskie/krakow?priceMax=650000",
     ("otodom.pl", "wyniki:filter")),
    ("https://www.otodom.pl/pl/oferta/2-pokoje-ID4abc", ("otodom.pl", "oferta:ad")),
    ("https://m.olx.pl/motoryzacja/", ("olx.pl", "motoryzacja:category")),
    ("https://www.allegro.pl/oferty/", ("other", "other:other")),
    ("https://evil.example/?x=olx.pl", ("other", "other:other")),
    ("not a url", ("other", "other:other")),
    ("", ("other", "other:other")),
])
def test_url_label(url, expected):
    assert funnel.url_label(url) == expected


def test_url_label_never_leaks_query_or_slug():
    host, label = funnel.url_label("https://www.olx.pl/motoryzacja/samochody/q-secret-model/?search%5Bphone%5D=555")
    assert "secret" not in label and "555" not in label and "q-" not in label


@pytest.mark.parametrize("url,reason", [
    ("", "empty"), ("   ", "empty"), ("olx.pl/abc", "no_scheme"), ("www.olx.pl", "no_scheme"),
    ("https://www.allegro.pl/", "bad_host"),
])
def test_invalid_url_reason(url, reason):
    assert funnel.invalid_url_reason(url) == reason


# ── parse_client_event ─────────────────────────────────────────────────────────

SID = "a" * 32


def _body(**kw):
    d = {"event": "landing", "sid": SID, "gclid": True, "lang": "pl"}
    d.update(kw)
    return json.dumps(d).encode()


def test_parse_valid_event():
    e = funnel.parse_client_event(_body(utm_source="google", utm_campaign="pl_search", ref="www.google.com"))
    assert e["event"] == "landing" and e["sid"] == SID and e["has_gclid"] is True and e["ui_lang"] == "pl"
    assert e["utm_source"] == "google" and e["utm_campaign"] == "pl_search" and e["ref_host"] == "www.google.com"


def test_parse_demo_pick_detail_and_url_label():
    e = funnel.parse_client_event(_body(event="demo_pick", detail="car1"))
    assert e["detail"] == "car1"
    e = funnel.parse_client_event(_body(event="url_paste", host="olx.pl", label="motoryzacja:filter"))
    assert (e["url_host"], e["url_label"]) == ("olx.pl", "motoryzacja:filter")


@pytest.mark.parametrize("raw", [
    b"", b"not json", b"[]", b'"x"', b"\xff\xfe",
    _body(event="nope"),
    _body(event="analyze_started"),          # server-only events cannot be forged by the browser
    _body(event="analyze_done"),
    _body(sid="short"), _body(sid="Z" * 32), _body(sid=None),
    b"x" * (funnel.MAX_BODY + 1),
])
def test_parse_rejects(raw):
    assert funnel.parse_client_event(raw) is None


def test_parse_sanitizes_free_text():
    e = funnel.parse_client_event(_body(utm_source="<script>alert(1)</script>", detail="bad detail!", ref="http://x/y",
                                        lang="de", gclid="yes"))
    assert "<" not in (e["utm_source"] or "") and ">" not in (e["utm_source"] or "")
    assert e["detail"] is None and e["ref_host"] is None and e["ui_lang"] is None and e["has_gclid"] is None


def test_parse_drops_label_for_unknown_host_and_bad_label():
    e = funnel.parse_client_event(_body(event="url_paste", host="evil.com", label="motoryzacja:filter"))
    assert e["url_host"] is None and e["url_label"] is None
    e = funnel.parse_client_event(_body(event="url_paste", host="olx.pl", label="http://full.url/here"))
    assert e["url_label"] is None


# ── rate limiter ───────────────────────────────────────────────────────────────

def test_rate_limiter_window():
    rl = funnel.RateLimiter(limit=3, window=10)
    assert [rl.allow("k", now=t) for t in (0, 1, 2)] == [True, True, True]
    assert rl.allow("k", now=3) is False
    assert rl.allow("other", now=3) is True
    assert rl.allow("k", now=10.5) is True       # oldest hit expired


def test_rate_limiter_memory_is_bounded():
    rl = funnel.RateLimiter(limit=1, window=1, max_keys=50)
    for i in range(500):
        rl.allow(f"k{i}", now=i * 2)
    assert len(rl._hits) <= 51


# ── day_window ─────────────────────────────────────────────────────────────────

def test_day_window_defaults_to_yesterday_local():
    now = datetime(2026, 10, 28, 5, 0, tzinfo=timezone.utc)        # 06:00 in Warsaw (CET)
    start, end, label = funnel.day_window(None, 1, "Europe/Warsaw", now=now)
    assert start == datetime(2026, 10, 26, 23, 0) and end == datetime(2026, 10, 27, 23, 0)
    assert label.startswith("2026-10-27")


def test_day_window_handles_dst_change_day():
    # 2026-10-25: clocks go back, the local day is 25 hours long
    start, end, _ = funnel.day_window("2026-10-25", 1, "Europe/Warsaw")
    assert (end - start).total_seconds() == 25 * 3600


def test_day_window_multi_day_and_bad_date():
    start, end, label = funnel.day_window("2026-10-27", 3, "Europe/Warsaw")
    assert "2026-10-25" in label and "2026-10-27" in label and end > start
    with pytest.raises(ValueError):
        funnel.day_window("27-10-2026", 1, "Europe/Warsaw")


# ── aggregate / render ─────────────────────────────────────────────────────────

def _row(sid, event, gclid=None, device="mobile", detail=None, host=None, label=None):
    return SimpleNamespace(sid=sid, event=event, has_gclid=gclid, device=device, detail=detail,
                           url_host=host, url_label=label)


def _rows():
    return [
        _row("s1", "landing", True), _row("s1", "demo_open", True), _row("s1", "demo_pick", True, detail="car1"),
        _row("s1", "demo_pick", True, detail="car2"), _row("s1", "url_paste", True, host="olx.pl", label="d:ad"),
        _row("s1", "analyze_submit", True),
        _row("s2", "landing", True), _row("s2", "invalid_url", True, detail="client_not_url"),
        _row("s3", "landing", False, device="desktop"), _row("s3", "url_field_focus", False, device="desktop"),
        _row(None, "analyze_started", None, device="mobile"),
    ]


def test_aggregate_counts_distinct_sessions_per_segment():
    a = funnel.aggregate(_rows())
    c = a["columns"]
    assert c["All"]["landing"]["sessions"] == 3
    assert c["Ads"]["landing"]["sessions"] == 2 and c["Organic"]["landing"]["sessions"] == 1
    assert c["Mobile"]["landing"]["sessions"] == 2 and c["Desktop"]["landing"]["sessions"] == 1
    assert c["Ads·mobile"]["demo_pick"] == {"sessions": 1, "events": 2}      # 1 session, 2 picks
    assert c["Ads"]["invalid_url"]["sessions"] == 1
    assert c["Organic"]["demo_open"]["sessions"] == 0
    assert a["demo_picks"] == {"car1": 1, "car2": 1}
    assert a["invalid_reasons"] == {"client_not_url": 1}
    assert a["paste_patterns"] == {"olx.pl d:ad": 1}
    assert a["unattributed"] == {"analyze_started": 1}
    assert a["total_rows"] == 11


def test_aggregate_empty():
    a = funnel.aggregate([])
    assert a["total_rows"] == 0 and a["columns"]["All"]["landing"] == {"sessions": 0, "events": 0}
    assert "AdAgent funnel" in funnel.render_text(a, "x")       # renders without division by zero


def test_render_text_and_html():
    a = funnel.aggregate(_rows())
    text = funnel.render_text(a, "2026-10-27 (Europe/Warsaw)")
    assert "2026-10-27" in text and "landing" in text and "client_not_url=1" in text and "Ads·mobile" in text
    assert "1 (33%)" in text            # 1 of 3 landing sessions opened the demo
    html = funnel.render_html(a, "<b>x</b>")
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<table" in html and "analyze_submit" in html
