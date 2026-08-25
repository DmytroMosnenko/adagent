"""Tests for service/report_builder.py — spec building, ad prep, HTML rendering."""
import pytest
from service.report_builder import (
    _parse_raw_parameters,
    _build_spec_items, _prepare_ad, _fallback_analysis, build_html_report,
)


class TestBuildSpecItems:

    def test_vehicle_specs_full(self):
        specs = {
            "make": "Mini", "model": "Paceman R61", "year": 2014,
            "mileage_km": 112000, "engine": "1.6T 184hp",
            "fuel": "petrol", "gearbox": "automatic",
            "body_type": "suv", "color": "Midnight Black", "doors": 5,
        }
        items = _build_spec_items(specs)
        labels = [i["label"] for i in items]
        assert "Make" in labels
        assert "Model" in labels
        assert "Year" in labels
        assert "Mileage" in labels
        assert "Engine" in labels
        assert "Fuel" in labels
        assert "Gearbox" in labels

    def test_mileage_formatted_with_commas(self):
        items = _build_spec_items({"mileage_km": 112000})
        m = next(i for i in items if i["label"] == "Mileage")
        assert "112,000" in m["value"]

    def test_real_estate_specs(self):
        specs = {
            "property_type": "apartment", "area_m2": 52.5, "rooms": 3,
            "floor": "4/10", "year_built": 2005, "condition": "very_good",
            "heating": "district", "parking": "underground",
        }
        items = _build_spec_items(specs)
        labels = [i["label"] for i in items]
        assert "Type" in labels
        assert "Area" in labels
        assert "Rooms" in labels
        assert "Floor" in labels

    def test_area_formatted_with_m2(self):
        items = _build_spec_items({"area_m2": 52.5})
        a = next(i for i in items if i["label"] == "Area")
        assert "52.5 m²" in a["value"]

    def test_empty_specs_returns_empty_list(self):
        assert _build_spec_items({}) == []

    def test_none_values_excluded(self):
        items = _build_spec_items({"make": "Mini", "model": None, "year": None})
        labels = [i["label"] for i in items]
        assert "Make" in labels
        assert "Model" not in labels
        assert "Year" not in labels

    def test_body_type_human_readable(self):
        items = _build_spec_items({"body_type": "suv"})
        b = next(i for i in items if i["label"] == "Body")
        assert b["value"] == "Suv"

    def test_condition_human_readable(self):
        items = _build_spec_items({"condition": "to_renovate"})
        c = next(i for i in items if i["label"] == "Condition")
        assert "to renovate" in c["value"].lower()


class TestPrepareAd:

    def _make_result(self, analysis: str, raw_data: dict | None = None) -> dict:
        return {
            "url": "https://www.otomoto.pl/oferta/mini-paceman-ID123.html",
            "data": raw_data or {"url": "https://www.otomoto.pl/oferta/mini-paceman-ID123.html",
                                 "title": "Mini Paceman 2014", "price": "45 000 zł",
                                 "location": "Warszawa"},
            "analysis": analysis,
        }

    def test_parses_valid_analysis_json(self):
        import json
        analysis = json.dumps({
            "rating": 8, "verdict": "worth_viewing",
            "verdict_note": "Good condition, full history.",
            "summary": "2014 Mini Paceman with 112k km.",
            "specs": {"make": "Mini", "model": "Paceman", "year": 2014, "mileage_km": 112000},
            "asking_price": "45 000 zł", "price_assessment": "fair",
            "red_flags": [], "positives": ["Full service history"],
        })
        ad = _prepare_ad(self._make_result(analysis))
        assert ad["rating"] == 8
        assert ad["verdict"] == "worth_viewing"
        assert ad["verdict_label"] == "Worth Viewing"
        assert ad["title"] == "Mini Paceman 2014"  # make + model + year
        assert ad["asking_price"] == "45 000 zł"
        assert ad["price_assessment"] == "fair"
        assert ad["price_assessment_label"] == "Fair price"
        assert len(ad["positives"]) == 1
        assert ad["site_label"] == "Otomoto"

    def test_falls_back_gracefully_on_bad_json(self):
        ad = _prepare_ad(self._make_result("not json at all"))
        assert ad["rating"] == 5           # fallback default
        assert ad["verdict"] == "unknown"
        assert "not json" in ad["summary"] # raw text in summary

    def test_rating_clamped_1_to_10(self):
        import json
        ad = _prepare_ad(self._make_result(json.dumps({"rating": 99})))
        assert ad["rating"] == 10

        ad2 = _prepare_ad(self._make_result(json.dumps({"rating": -5})))
        assert ad2["rating"] == 1

    def test_title_from_scraped_data_when_no_specs(self):
        import json
        ad = _prepare_ad(self._make_result(
            json.dumps({"rating": 6, "verdict": "maybe", "specs": {}}),
            raw_data={"url": "https://olx.pl/x", "title": "VW Golf 2016 TDI"},
        ))
        assert ad["title"] == "VW Golf 2016 TDI"

    def test_real_estate_title_from_specs(self):
        import json
        analysis = json.dumps({
            "rating": 7, "verdict": "maybe", "verdict_note": "ok",
            "summary": "nice flat", "specs": {"property_type": "apartment", "area_m2": 55},
            "asking_price": "350 000 zł", "price_assessment": "fair",
            "red_flags": [], "positives": [],
        })
        ad = _prepare_ad({
            "url": "https://www.otodom.pl/oferta/flat-ID999.html",
            "data": {"url": "https://www.otodom.pl/oferta/flat-ID999.html"},
            "analysis": analysis,
        })
        assert "Apartment" in ad["title"]
        assert "55" in ad["title"]
        assert ad["site_label"] == "Otodom"

    def test_olx_site_label(self):
        import json
        ad = _prepare_ad({
            "url": "https://www.olx.pl/d/oferta/test-ID1.html",
            "data": {}, "analysis": json.dumps({"rating": 5}),
        })
        assert ad["site_label"] == "OLX"

    def test_spec_items_populated(self):
        import json
        analysis = json.dumps({
            "rating": 8, "verdict": "worth_viewing",
            "specs": {"make": "BMW", "model": "E90", "year": 2010, "mileage_km": 80000},
            "red_flags": [], "positives": [],
        })
        ad = _prepare_ad(self._make_result(analysis))
        labels = [s["label"] for s in ad["spec_items"]]
        assert "Make" in labels
        assert "Mileage" in labels

    def test_raw_data_passed_through(self):
        import json
        ad = _prepare_ad(self._make_result(
            json.dumps({"rating": 5}),
            raw_data={"url": "https://olx.pl/x", "description": "Nice car.",
                      "location": "Kraków", "parameters": "Rok: 2015"},
        ))
        assert ad["raw"]["description"] == "Nice car."
        assert ad["raw"]["location"] == "Kraków"
        assert ad["raw"]["parameters"] == "Rok: 2015"


class TestBuildHtmlReport:

    def _make_results(self):
        import json
        return [
            {
                "url": "https://www.otomoto.pl/oferta/mini-paceman-ID1.html",
                "data": {"url": "https://www.otomoto.pl/oferta/mini-paceman-ID1.html",
                         "title": "Mini Paceman 2014", "price": "45 000 zł",
                         "location": "Warszawa", "description": "Good condition."},
                "analysis": json.dumps({
                    "rating": 8, "verdict": "worth_viewing",
                    "verdict_note": "Solid buy.", "summary": "Good car.",
                    "specs": {"make": "Mini", "model": "Paceman", "year": 2014, "mileage_km": 112000},
                    "asking_price": "45 000 zł", "price_assessment": "fair",
                    "red_flags": ["High mileage"], "positives": ["Full history"],
                }),
            },
            {
                "url": "https://www.olx.pl/d/oferta/vw-golf-ID2.html",
                "data": {"url": "https://www.olx.pl/d/oferta/vw-golf-ID2.html",
                         "title": "VW Golf 2015"},
                "analysis": json.dumps({
                    "rating": 3, "verdict": "skip",
                    "verdict_note": "US import.", "summary": "Risky.",
                    "specs": {"make": "VW", "model": "Golf", "year": 2015},
                    "asking_price": "32 000 zł", "price_assessment": "high",
                    "red_flags": ["US import", "No history"], "positives": [],
                }),
            },
        ]

    def _make_summary(self):
        import json
        return json.dumps({
            "market_summary": "Mixed batch of listings.",
            "price_range": "32 000 – 45 000 zł",
            "average_price": "38 500 zł",
            "recommendation": "Go for the Mini.",
        })

    def test_renders_without_error(self):
        html = build_html_report(
            results=self._make_results(), summary_text=self._make_summary(),
            filter_url="https://www.olx.pl/motoryzacja/q-mini/",
            report_id="abc123", ads_found=10, is_limited=True,
            template_dir="templates", template_file="report.html.j2",
        )
        assert isinstance(html, str)
        assert len(html) > 5000

    def test_contains_leaderboard(self):
        html = build_html_report(
            results=self._make_results(), summary_text=self._make_summary(),
            filter_url="https://www.olx.pl/test/",
            report_id="abc", ads_found=10, is_limited=True,
        )
        assert 'id="leaderboard"' in html

    def test_ads_sorted_by_rating_descending(self):
        html = build_html_report(
            results=self._make_results(), summary_text=self._make_summary(),
            filter_url="https://www.olx.pl/test/",
            report_id="abc", ads_found=10, is_limited=False,
        )
        # Mini (rating 8) should appear before VW Golf (rating 3)
        assert html.index("Mini Paceman") < html.index("VW Golf")

    def test_freemium_banner_when_limited(self):
        html = build_html_report(
            results=self._make_results(), summary_text=self._make_summary(),
            filter_url="https://www.olx.pl/test/",
            report_id="abc", ads_found=10, is_limited=True,
        )
        assert "freemium-banner" in html
        assert "8 more ads" in html   # 10 found - 2 shown = 8

    def test_no_freemium_banner_when_not_limited(self):
        html = build_html_report(
            results=self._make_results(), summary_text=self._make_summary(),
            filter_url="https://www.olx.pl/test/",
            report_id="abc", ads_found=2, is_limited=False,
        )
        assert 'class="freemium-banner"' not in html

    def test_deduplicates_same_url(self):
        results = self._make_results()
        # duplicate of first result with query params
        results.append({**results[0], "url": results[0]["url"] + "?promoted=true"})
        html = build_html_report(
            results=results, summary_text=self._make_summary(),
            filter_url="https://www.olx.pl/test/",
            report_id="abc", ads_found=3, is_limited=False,
        )
        # Should only render 2 unique ads, not 3
        assert html.count('class="ad-card') == 2

    def test_market_summary_rendered(self):
        html = build_html_report(
            results=self._make_results(), summary_text=self._make_summary(),
            filter_url="https://www.olx.pl/test/",
            report_id="abc", ads_found=2, is_limited=False,
        )
        assert "Mixed batch of listings." in html
        assert "32 000" in html   # price range

    def test_raw_spoiler_present(self):
        html = build_html_report(
            results=self._make_results(), summary_text=self._make_summary(),
            filter_url="https://www.olx.pl/test/",
            report_id="abc", ads_found=2, is_limited=False,
        )
        assert 'class="raw"' in html
        assert "Raw scraped data" in html

    def test_adagent_bar_present(self):
        html = build_html_report(
            results=self._make_results(), summary_text=self._make_summary(),
            filter_url="https://www.olx.pl/test/",
            report_id="abc", ads_found=2, is_limited=False,
        )
        assert "aa-bar" in html
        assert "AdAgent" in html

    def test_float_button_present(self):
        html = build_html_report(
            results=self._make_results(), summary_text=self._make_summary(),
            filter_url="https://www.olx.pl/test/",
            report_id="abc", ads_found=2, is_limited=False,
        )
        assert 'id="float-btn"' in html

    def test_handles_fallback_summary_gracefully(self):
        html = build_html_report(
            results=self._make_results(), summary_text="not json",
            filter_url="https://www.olx.pl/test/",
            report_id="abc", ads_found=2, is_limited=False,
        )
        assert isinstance(html, str)  # must not crash

    def test_site_links_correct(self):
        html = build_html_report(
            results=self._make_results(), summary_text=self._make_summary(),
            filter_url="https://www.olx.pl/test/",
            report_id="abc", ads_found=2, is_limited=False,
        )
        assert "Otomoto ↗" in html
        assert "OLX ↗" in html


# ── Tests for fixes ──────────────────────────────────────────────────────────

class TestParseRawParameters:

    def test_parses_standard_olx_format(self):
        params = "Marka pojazdu: Volkswagen\nPrzebieg: 120 000 km\nRok produkcji: 2018"
        items = _parse_raw_parameters(params)
        assert len(items) == 3
        assert items[0] == {"label": "Marka pojazdu", "value": "Volkswagen"}
        assert items[1] == {"label": "Przebieg",       "value": "120 000 km"}

    def test_ignores_lines_without_colon(self):
        params = "Some header\nMarka: BMW\nno colon here\nRok: 2020"
        items = _parse_raw_parameters(params)
        assert len(items) == 2

    def test_handles_value_with_colon(self):
        params = "URL: https://example.com/path"
        items = _parse_raw_parameters(params)
        assert items[0]["value"] == "https://example.com/path"

    def test_empty_string_returns_empty_list(self):
        assert _parse_raw_parameters("") == []
        assert _parse_raw_parameters(None) == []

    def test_strips_whitespace(self):
        params = "  Marka :  BMW  "
        items = _parse_raw_parameters(params)
        assert items[0] == {"label": "Marka", "value": "BMW"}


class TestPrepareAdTitleSync:

    def test_uses_scraped_title_not_ai_specs(self):
        """Real estate: title must be the original ad headline, not 'Apartment 32.0 m²'."""
        import json
        result = {
            "url": "https://www.otodom.pl/oferta/mieszkanie-ID1.html",
            "data": {
                "url": "https://www.otodom.pl/oferta/mieszkanie-ID1.html",
                "title": "Małe mieszkanie gotowe do wprowadzenia",
                "price": "350 000 zł",
            },
            "analysis": json.dumps({
                "rating": 7, "verdict": "maybe", "verdict_note": "ok",
                "summary": "small flat", "specs": {"property_type": "apartment", "area_m2": 32.0},
                "asking_price": "350 000 zł", "price_assessment": "fair",
                "red_flags": [], "positives": [],
            }),
        }
        ad = _prepare_ad(result)
        assert ad["title"] == "Małe mieszkanie gotowe do wprowadzenia"
        # Must NOT look like the AI-built fallback
        assert "Apartment" not in ad["title"]
        assert "32.0" not in ad["title"]

    def test_falls_back_to_ai_title_when_scraped_is_empty(self):
        import json
        result = {
            "url": "https://www.olx.pl/d/oferta/mini-ID2.html",
            "data": {"url": "...", "title": ""},
            "analysis": json.dumps({
                "rating": 8, "verdict": "worth_viewing", "verdict_note": "good",
                "summary": "nice car", "specs": {"make": "Mini", "model": "Cooper", "year": 2020},
                "asking_price": "60 000 zł", "price_assessment": "fair",
                "red_flags": [], "positives": [],
            }),
        }
        ad = _prepare_ad(result)
        assert "Mini" in ad["title"]
        assert "Cooper" in ad["title"]

    def test_raw_params_supplement_sparse_ai_specs(self):
        """When AI misses mileage, raw parameters must fill the gap."""
        import json
        result = {
            "url": "https://www.otomoto.pl/oferta/vw-ID3.html",
            "data": {
                "url": "https://www.otomoto.pl/oferta/vw-ID3.html",
                "title": "Volkswagen Golf 2019",
                "parameters": "Przebieg: 85 000 km\nRodzaj paliwa: Benzyna",
            },
            "analysis": json.dumps({
                "rating": 7, "verdict": "maybe", "verdict_note": "ok",
                "summary": "decent", "specs": {"make": "Volkswagen", "model": "Golf"},
                "asking_price": "55 000 zł", "price_assessment": "fair",
                "red_flags": [], "positives": [],
            }),
        }
        ad = _prepare_ad(result)
        labels = [s["label"] for s in ad["spec_items"]]
        # AI gave make/model; raw params adds Przebieg + Rodzaj paliwa
        assert "Przebieg" in labels or "Mileage" in labels
        assert "Rodzaj paliwa" in labels or "Fuel" in labels

    def test_raw_params_used_when_ai_spec_items_empty(self):
        import json
        result = {
            "url": "https://www.olx.pl/d/oferta/test-ID4.html",
            "data": {
                "url": "https://www.olx.pl/d/oferta/test-ID4.html",
                "title": "Some ad",
                "parameters": "Rok: 2019\nKolor: Czarny",
            },
            "analysis": json.dumps({
                "rating": 5, "verdict": "maybe", "verdict_note": "",
                "summary": "", "specs": {},          # AI extracted nothing
                "asking_price": None, "price_assessment": "unknown",
                "red_flags": [], "positives": [],
            }),
        }
        ad = _prepare_ad(result)
        labels = [s["label"] for s in ad["spec_items"]]
        assert "Rok" in labels
        assert "Kolor" in labels


class TestUUIDv7:

    def test_generate_report_id_returns_16_bytes(self):
        from service.models import generate_report_id
        id1 = generate_report_id()
        assert isinstance(id1, bytes)
        assert len(id1) == 16

    def test_uuid_v7_version_bits(self):
        from service.models import generate_report_id
        id1 = generate_report_id()
        assert ((id1[6] >> 4) & 0xF) == 7

    def test_uuid_v7_variant_bits(self):
        from service.models import generate_report_id
        id1 = generate_report_id()
        assert ((id1[8] >> 6) & 0x3) == 2

    def test_ids_are_time_ordered(self):
        import time
        from service.models import generate_report_id
        id1 = generate_report_id()
        time.sleep(0.002)
        id2 = generate_report_id()
        assert id2 > id1

    def test_ids_are_unique(self):
        from service.models import generate_report_id
        ids = {generate_report_id() for _ in range(50)}
        assert len(ids) == 50

    def test_binary_uuid_typedecorator_roundtrip(self):
        from service.models import generate_report_id, BinaryUUID
        td  = BinaryUUID()
        raw = generate_report_id()
        hex_str = raw.hex()

        assert td.process_bind_param(hex_str, None) == raw
        assert td.process_bind_param(raw, None)     == raw
        assert td.process_result_value(raw, None)   == hex_str
        assert td.process_result_value(None, None) is None

    def test_get_report_rejects_invalid_hex(self):
        from service.crud import get_report
        import asyncio
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from sqlalchemy.pool import StaticPool
        from service.models import Base

        async def _run():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                         connect_args={"check_same_thread": False},
                                         poolclass=StaticPool)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                assert await get_report(db, "not-a-valid-hex") is None
                assert await get_report(db, "")  is None
                assert await get_report(db, "abc") is None  # wrong length
            await engine.dispose()

        asyncio.run(_run())
