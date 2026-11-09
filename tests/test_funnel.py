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


def test_how_open_is_an_accepted_client_event_and_shows_in_the_report():
    assert "how_open" in funnel.CLIENT_EVENTS and funnel.STEPS.index("how_open") == funnel.STEPS.index("landing") + 1
    e = funnel.parse_client_event(_body(event="how_open"))
    assert e and e["event"] == "how_open"
    a = funnel.aggregate([_row("s1", "landing", True), _row("s1", "how_open", True), _row("s1", "how_open", True),
                          _row("s2", "landing", True)])
    assert a["columns"]["All"]["how_open"] == {"sessions": 1, "events": 2}          # 1 visitor, opened twice
    text = funnel.render_text(a, "x")
    assert "how_open" in text and "1 (50%)" in text                                  # 1 of 2 landing sessions


# ── ads attribution (Google / Meta / Organic) ──────────────────────────────────

@pytest.mark.parametrize("gclid,utm,fbclid,expected", [
    (None, None, None, "organic"),
    (False, None, False, "organic"),
    (False, "", False, "organic"),
    (True, None, None, "google"),                  # gclid alone
    (True, "google", None, "google"),
    (None, "google", None, "google"),              # utm_source alone is enough to be Ads
    (None, "Google", None, "google"),              # case-insensitive
    (None, "fb", None, "meta"),
    (None, "ig", None, "meta"),
    (None, None, True, "meta"),                    # fbclid alone
    (True, "ig", None, "meta"),                    # utm_source wins over the click id
    (None, "newsletter", None, "ads_other"),       # Ads (utm present) but neither network
    (None, "newsletter", True, "meta"),            # unknown utm -> click id decides
])
def test_classify_source(gclid, utm, fbclid, expected):
    assert funnel.classify_source(gclid, utm, fbclid) == expected


def test_parse_has_fbclid():
    assert funnel.parse_client_event(_body(fbclid=True))["has_fbclid"] is True
    assert funnel.parse_client_event(_body(fbclid=False))["has_fbclid"] is False
    assert funnel.parse_client_event(_body())["has_fbclid"] is None
    assert funnel.parse_client_event(_body(fbclid="abc123"))["has_fbclid"] is None   # the value never gets stored


def _srow(sid, event, *, gclid=None, utm=None, fbclid=None, device="mobile", detail=None, at=None):
    return SimpleNamespace(sid=sid, event=event, has_gclid=gclid, has_fbclid=fbclid, utm_source=utm,
                           device=device, detail=detail, url_host=None, url_label=None, created_at=at)


def test_aggregate_splits_ads_into_google_meta_organic():
    rows = [
        _srow("g1", "landing", gclid=True, utm="google"),
        _srow("g2", "landing", utm="google"),
        _srow("m1", "landing", utm="fb", fbclid=True),
        _srow("m2", "landing", utm="ig"),
        _srow("m3", "landing", fbclid=True),
        _srow("o1", "landing", gclid=False, fbclid=False),
        _srow("o2", "landing"),
    ]
    c = funnel.aggregate(rows)["columns"]
    assert c["All"]["landing"]["sessions"] == 7
    assert c["Ads"]["landing"]["sessions"] == 5
    assert c["Ads·Google"]["landing"]["sessions"] == 2
    assert c["Ads·Meta"]["landing"]["sessions"] == 3
    assert c["Organic"]["landing"]["sessions"] == 2


def test_aggregate_attributes_per_session_not_per_row():
    # a later row without the flags (e.g. a server event with no inherited source) stays in the Meta session
    rows = [_srow("m1", "landing", utm="ig"), _srow("m1", "analyze_started")]
    c = funnel.aggregate(rows)["columns"]
    assert c["Ads·Meta"]["analyze_started"]["sessions"] == 1
    assert c["Organic"]["analyze_started"]["sessions"] == 0


# ── bot-like sessions: first url_field_focus < 2 s after landing ───────────────

T0 = datetime(2026, 11, 9, 10, 0, 0)


def _at(seconds):
    from datetime import timedelta
    return T0 + timedelta(seconds=seconds)


def test_fast_focus_session_is_excluded_from_funnel_counts():
    rows = [
        _srow("bot", "landing", utm="google", at=_at(0)),
        _srow("bot", "url_field_focus", utm="google", at=_at(1)),                 # 1 s -> bot-like
        _srow("bot", "analyze_submit", utm="google", at=_at(2)),
        _srow("human", "landing", utm="google", at=_at(0)),
        _srow("human", "url_field_focus", utm="google", at=_at(5)),
    ]
    a = funnel.aggregate(rows)
    assert a["bot_sessions"] == 1 and a["bot_rows"] == 3
    c = a["columns"]
    assert c["All"]["landing"]["sessions"] == 1
    assert c["All"]["url_field_focus"]["sessions"] == 1
    assert c["All"]["analyze_submit"]["sessions"] == 0
    assert c["Ads·Google"]["landing"]["sessions"] == 1


def test_focus_threshold_is_exactly_two_seconds():
    rows = [_srow("a", "landing", at=_at(0)), _srow("a", "url_field_focus", at=_at(2)),      # 2.0 s: kept
            _srow("b", "landing", at=_at(0)), _srow("b", "url_field_focus", at=_at(1.99))]   # < 2 s: bot
    a = funnel.aggregate(rows)
    assert a["bot_sessions"] == 1 and a["columns"]["All"]["landing"]["sessions"] == 1


def test_browser_measured_delay_wins_over_timestamps():
    # DB timestamps have 1 s resolution: they say 3 s, the browser measured 1.2 s -> bot-like
    rows = [_srow("a", "landing", at=_at(0)), _srow("a", "url_field_focus", detail="dt1200", at=_at(3)),
            _srow("b", "landing", at=_at(0)), _srow("b", "url_field_focus", detail="dt2500", at=_at(1))]
    a = funnel.aggregate(rows)
    assert a["bot_sessions"] == 1
    assert a["columns"]["All"]["landing"]["sessions"] == 1


def test_only_the_first_focus_counts_and_missing_data_is_never_a_bot():
    rows = [_srow("a", "landing", at=_at(0)), _srow("a", "url_field_focus", at=_at(10)),
            _srow("a", "url_field_focus", at=_at(10.5)),
            _srow("nolanding", "url_field_focus", at=_at(0)),                          # landing outside the window
            _srow("nots", "landing"), _srow("nots", "url_field_focus")]                # no timestamps at all
    a = funnel.aggregate(rows)
    assert a["bot_sessions"] == 0 and a["columns"]["All"]["url_field_focus"]["sessions"] == 3


def test_bot_exclusion_is_reported_and_dropped_from_detail_counters():
    rows = [_srow("bot", "landing", at=_at(0)), _srow("bot", "url_field_focus", at=_at(0.5)),
            SimpleNamespace(**{**_srow("bot", "demo_pick", detail="car1").__dict__}),
            _srow("ok", "landing", at=_at(0))]
    a = funnel.aggregate(rows)
    assert a["demo_picks"] == {}
    assert "Excluded as bot-like" in funnel.render_text(a, "x")
    assert "Excluded as bot-like" in funnel.render_html(a, "x")
    assert "Excluded as bot-like" not in funnel.render_text(funnel.aggregate([_srow("ok", "landing")]), "x")
