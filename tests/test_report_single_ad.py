"""
Single-ad report rendering (build_html_report(single_ad=True)): clean one-ad layout, no
invented market comparison, no seller personal data, and filter reports left as they were.
Pure Python (Jinja2 + the real template): no DB, no browser.
"""
import json
import re

import pytest

from service.report_builder import build_html_report, _prepare_ad

AD_URL = "https://www.olx.pl/d/oferta/mini-cooper-s-2018-CID5-ID123abc.html"
PASTED = AD_URL + "?reason=extended_search_extended_distance&utm_source=share"
FILTER_URL = "https://www.olx.pl/motoryzacja/samochody/q-mini/"

SECRET_DESC = "ZXQ-SECRET-DESCRIPTION: dzwoń 600 123 456, jan.kowalski@example.com, Jan Kowalski"


def _analysis(**over):
    a = {
        "rating": 7.4, "verdict": "maybe",
        "verdict_note": "Solid car, but the clutch is due.",
        "summary": "A well-kept Mini with a full service history.",
        "specs": {"make": "Mini", "model": "Cooper S", "year": 2018, "mileage_km": 112000,
                  "fuel": "petrol", "gearbox": "manual"},
        "asking_price": "45 000 zł", "price_assessment": "fair",
        "red_flags": ["Clutch likely due", "No ASO stamps"],
        "positives": ["First owner", "Full service history"],
        "deep_dive": {
            "general_specs": "1.6 turbo, about 184 hp.",
            "mentioned_fixes": ["Timing chain replaced"],
            "known_weak_points": "Timing chain tensioner and carbon build-up are known on this engine.",
            "real_world_consumption": "About 8 l/100 km.",
        },
        "seller_questions": ["Was the timing chain done at an authorised garage?",
                             "Can I see the service invoices?"],
    }
    a.update(over)
    return json.dumps(a)


def _result(analysis=None, data=None):
    data = data if data is not None else {
        "url": AD_URL, "title": "Mini Cooper S 2018", "price": "45 000 zł", "location": "Warszawa",
        "parameters": "Rok produkcji: 2018\nPrzebieg: 112 000 km", "description": SECRET_DESC,
        "page_text": SECRET_DESC, "image_url": "https://ireland.apollo.olxcdn.com/v1/files/a/image;s=1000x700",
    }
    return {"url": AD_URL, "data": data, "analysis": analysis if analysis is not None else _analysis()}


def _render(results, single_ad=True, summary_text="", filter_url=PASTED, **kw):
    return build_html_report(
        results=results, summary_text=summary_text, filter_url=filter_url, report_id="r1",
        ads_found=len(results), is_limited=True, ui_lang=kw.pop("ui_lang", "en"),
        single_ad=single_ad, **kw)


def _body(html: str) -> str:
    """The markup the reader sees (the stylesheet and script mention these words too)."""
    body = html.split("<body>")[1]
    return re.sub(r"<(script|style)\b.*?</\1>", "", body, flags=re.S)


def _no_empty_sections(html: str):
    """No headings / containers left blank."""
    assert not re.search(r'<div class="ad-specs">\s*</div>', html)
    assert not re.search(r'<div class="ad-tags">\s*</div>', html)
    assert not re.search(r'<ul class="fixes-list">\s*</ul>', html)
    assert "None" not in html and "undefined" not in html


class TestFullSingleAdReport:

    def test_layout_has_one_card_and_no_leaderboard_market_or_raw_blocks(self):
        html = _render([_result()])
        assert html.count('class="ad-card') == 1
        body = _body(html)
        assert 'id="leaderboard"' not in body and "lb-row" not in body
        assert "Market Overview" not in body and 'id="summary"' not in body
        assert "Raw scraped data" not in _body(html)
        assert "rank-circle" not in _body(html) and "rank-badge" not in _body(html)
        _no_empty_sections(html)

    def test_shows_title_price_score_verdict_flags_positives_weak_points(self):
        html = _render([_result()])
        assert "Mini Cooper S 2018" in html
        assert "45 000 zł" in html
        assert "7.4/10" in html
        assert "Solid car, but the clutch is due." in html and "Maybe" in html
        assert "Clutch likely due" in html and "First owner" in html
        assert "Known weak points for this model" in html
        assert "Timing chain tensioner" in html

    def test_primary_button_is_view_ad_pointing_at_the_clean_ad_url(self):
        html = _render([_result()])
        header = html.split("</header>")[0]
        assert "View ad ↗" in header and "filter ↗" not in header
        assert f'href="{AD_URL}"' in header
        assert "utm_source" not in html and "extended_search" not in html      # pasted tracking params never printed

    def test_price_label_comes_only_from_the_per_ad_analysis(self):
        assert "Fair price" in _render([_result()])
        no_label = _render([_result(_analysis(price_assessment="unknown"))])
        assert "Fair price" not in no_label and "High price" not in no_label and "Low price" not in no_label
        absent = json.loads(_analysis()); absent.pop("price_assessment")
        assert "Fair price" not in _render([_result(json.dumps(absent))])

    def test_seller_questions_checklist_when_the_analysis_returns_one(self):
        html = _render([_result()])
        assert "What to ask the seller" in html
        assert "Can I see the service invoices?" in html

    def test_no_seller_questions_block_when_the_analysis_has_none(self):
        a = json.loads(_analysis()); a.pop("seller_questions")
        html = _render([_result(json.dumps(a))])
        assert "What to ask the seller" not in html
        _no_empty_sections(html)

    def test_seller_questions_accepted_inside_deep_dive_too(self):
        a = json.loads(_analysis()); a["deep_dive"]["seller_questions"] = a.pop("seller_questions")
        assert "Can I see the service invoices?" in _render([_result(json.dumps(a))])

    def test_summary_text_is_ignored_nothing_is_compared(self):
        summary = json.dumps({"market_summary": "MARKET-TEXT", "price_range": "1-2", "average_price": "AVG-PRICE",
                              "recommendation": "REC-TEXT", "recommended_ads": []})
        html = _render([_result()], summary_text=summary)
        assert "MARKET-TEXT" not in html and "AVG-PRICE" not in html and "REC-TEXT" not in html
        assert 'id="float-btn-summary"' not in html

    def test_polish_ui(self):
        html = _render([_result()], ui_lang="pl")
        assert "Zobacz ogłoszenie ↗" in html and "O co zapytać sprzedawcę" in html


class TestSingleAdPrivacy:

    def test_raw_scraped_text_and_contact_details_never_reach_the_html(self):
        html = _render([_result()])
        assert "ZXQ-SECRET-DESCRIPTION" not in html          # description / page_text stay out
        assert "600 123 456" not in html and "jan.kowalski@example.com" not in html
        assert "Jan Kowalski" not in html

    def test_contact_details_echoed_by_the_ai_or_in_the_title_are_masked(self):
        a = _analysis(summary="Call Jan on 600 123 456 or mail jan.kowalski@example.com.",
                      red_flags=["Phone +48 601 222 333 in ad"], positives=["tel 22 123 45 67"],
                      verdict_note="Reach me: anna@example.pl")
        data = {"url": AD_URL, "title": "Mini Cooper tel. 600-123-456", "price": "45 000 zł"}
        html = _render([_result(a, data)])
        for leaked in ("600 123 456", "600-123-456", "jan.kowalski@example.com", "+48 601 222 333",
                       "601 222 333", "22 123 45 67", "anna@example.pl"):
            assert leaked not in html, leaked
        assert "•••" in html

    def test_masking_leaves_prices_mileage_and_years_alone(self):
        a = _analysis(asking_price="1 250 000 zł", summary="112 000 km, rok 2018, 184 KM, ID 6H123.")
        html = _render([_result(a)])
        assert "1 250 000 zł" in html and "112 000 km" in html and "2018" in html and "184" in html


class TestMissingAndBrokenData:

    def test_minimal_analysis_and_title_only_scrape_render_cleanly(self):
        res = _result(json.dumps({"rating": 6, "verdict": "maybe"}),
                      {"url": AD_URL, "title": "Mini Cooper S 2018"})
        html = _render([res])
        assert "Mini Cooper S 2018" in html and "6.0/10" in html or "6/10" in html
        assert "View ad ↗" in html
        _no_empty_sections(html)

    def test_no_title_falls_back_to_ai_specs_then_url(self):
        res = _result(None, {"url": AD_URL})
        html = _render([res])
        assert "Mini Cooper S 2018" in html or "Mini Cooper S" in html
        res2 = _result(json.dumps({"rating": 5}), {"url": AD_URL})
        assert AD_URL in _render([res2])

    def test_unparseable_ai_output_does_not_crash_the_renderer(self):
        html = _render([_result("not json at all", {"url": AD_URL, "title": "T"})])
        assert "View ad ↗" in html

    def test_failed_scrape_result_renders_without_error_text_or_raw_block(self):
        """tasks.py never builds a report for a failed single-ad scrape, but if one reaches the
        renderer it must not blow up, print the scraper error, or show a raw block."""
        res = {"url": AD_URL, "data": {"url": AD_URL, "error": "HTTP 403 — rate limited (retries exhausted)",
                                       "error_code": "blocked"}, "analysis": "AI ERROR: boom"}
        html = _render([res])
        assert "rate limited" not in html and "Raw scraped data" not in _body(html)
        assert "View ad ↗" in html

    def test_empty_results_list_renders_a_shell(self):
        html = _render([])
        assert "View ad ↗" in html and 'class="ad-card' not in html


class TestFilterReportsAreUnchanged:

    RESULTS = [_result(), {**_result(), "url": "https://www.olx.pl/d/oferta/vw-golf-CID5-ID999.html"}]
    SUMMARY = json.dumps({"market_summary": "Decent market.", "price_range": "40-50k", "average_price": "45k",
                          "recommendation": "Buy the Mini.", "recommended_ads": []})

    def _filter_html(self):
        return build_html_report(results=self.RESULTS, summary_text=self.SUMMARY, filter_url=FILTER_URL,
                                 report_id="r2", ads_found=2, is_limited=False)

    def test_leaderboard_market_overview_and_raw_block_are_still_there(self):
        html = self._filter_html()
        assert 'id="leaderboard"' in html and html.count("lb-row") >= 2
        assert "Market Overview" in html and "Decent market." in html
        assert _body(html).count("Raw scraped data") == 2
        assert "View OLX filter ↗" in html and f'href="{FILTER_URL}"' in html
        assert "View ad ↗" not in html

    def test_seller_questions_are_not_rendered_in_filter_reports(self):
        assert "What to ask the seller" not in self._filter_html()

    def test_contact_masking_is_not_applied_to_filter_reports(self):
        # the raw block of a filter report is shown as before (make_demo_report.py strips it for the public sample)
        assert "600 123 456" in self._filter_html()

    def test_prepare_ad_default_is_the_old_behaviour(self):
        ad = _prepare_ad(_result())
        assert ad["seller_questions"] == [] and ad["raw"]["description"] == SECRET_DESC
