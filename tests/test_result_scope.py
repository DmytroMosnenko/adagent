"""Pure rules for results that do not belong to the user's filter."""
import pytest

from service.result_scope import (
    ListingMeta, extra_result_reason, is_last_page, listing_meta, parse_result_count,
)

OLX = "https://www.olx.pl/d/oferta/iphone-13-CID99-ID1.html"


class TestExtraResultReason:
    def test_olx_extended_delivery(self):
        url = OLX + "?reason=extended_search_extended_delivery&search_reason=search%7Corganic"
        assert extra_result_reason(url) == "extended_search_extended_delivery"

    def test_olx_extended_distance(self):
        assert extra_result_reason(OLX + "?reason=extended_search_extended_distance") \
            == "extended_search_extended_distance"

    def test_any_future_extended_kind(self):
        assert extra_result_reason(OLX + "?reason=extended_search_spell_checker")

    def test_organic_is_kept(self):
        assert extra_result_reason(OLX + "?search_reason=search%7Corganic") is None
        assert extra_result_reason(OLX) is None

    def test_unrelated_reason_value_is_kept(self):
        assert extra_result_reason(OLX + "?reason=something_else") is None

    def test_otodom_promoted(self):
        assert extra_result_reason("https://www.otodom.pl/hpr/pl/oferta/x-ID9") == "promoted"
        assert extra_result_reason("https://www.otodom.pl/pl/oferta/x-ID9") is None

    def test_empty(self):
        assert extra_result_reason("") is None


class TestParseResultCount:
    @pytest.mark.parametrize("text,expected", [
        ("Znaleźliśmy  65 ogłoszeń", (65, True)),
        ("Znaleźliśmy 1 ogłoszenie", (1, True)),
        ("Znaleźliśmy 1 234 ogłoszenia", (1234, True)),
        ("Znaleźliśmy ponad 1000 ogłoszeń", (1000, False)),
        ("Znaleźliśmy\u00a0ponad\u00a01000\u00a0ogłoszeń", (1000, False)),
    ])
    def test_counts(self, text, expected):
        assert parse_result_count(text) == expected

    def test_absent(self):
        assert parse_result_count("") is None
        assert parse_result_count(None) is None
        assert parse_result_count("Telefony Łódź") is None


class TestListingMeta:
    def test_state(self):
        assert listing_meta({"total": 65, "totalPages": 2, "countText": None}) \
            == ListingMeta(total=65, exact=True, total_pages=2)

    def test_counter_fallback_when_state_missing(self):
        assert listing_meta({"countText": "Znaleźliśmy 65 ogłoszeń"}) \
            == ListingMeta(total=65, exact=True, total_pages=None)

    def test_ponad_is_inexact(self):
        m = listing_meta({"total": 1000, "totalPages": 25, "countText": "Znaleźliśmy ponad 1000 ogłoszeń"})
        assert m.exact is False and m.total_pages == 25

    def test_cap_is_inexact_even_without_counter(self):
        assert listing_meta({"total": 1000, "totalPages": 25}).exact is False

    def test_nothing_known(self):
        assert listing_meta(None) is None
        assert listing_meta({}) is None
        assert listing_meta({"total": None, "totalPages": 0, "countText": "x"}) is None

    def test_rejects_garbage_types(self):
        assert listing_meta({"total": "65", "totalPages": True}) is None


class TestIsLastPage:
    def test_boundaries(self):
        m = ListingMeta(65, True, 2)
        assert not is_last_page(1, m)
        assert is_last_page(2, m)
        assert is_last_page(3, m)

    def test_unknown_pages_never_last(self):
        assert not is_last_page(5, ListingMeta(65, True, None))
        assert not is_last_page(5, None)
