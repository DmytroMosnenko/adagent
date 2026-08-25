"""Tests for service/ai_client.py — parse_json_safe and _format_ad."""
import pytest
from service.ai_client import parse_json_safe, _format_ad


FALLBACK = {"rating": 5, "verdict": "unknown"}


class TestParseJsonSafe:

    def test_parses_clean_json(self):
        text = '{"rating": 8, "verdict": "worth_viewing"}'
        result = parse_json_safe(text, FALLBACK)
        assert result["rating"] == 8
        assert result["verdict"] == "worth_viewing"

    def test_strips_markdown_fences(self):
        text = '```json\n{"rating": 7, "verdict": "maybe"}\n```'
        result = parse_json_safe(text, FALLBACK)
        assert result["rating"] == 7

    def test_strips_json_fence_without_lang(self):
        text = '```\n{"rating": 6}\n```'
        result = parse_json_safe(text, FALLBACK)
        assert result["rating"] == 6

    def test_finds_json_embedded_in_prose(self):
        text = 'Here is the analysis: {"rating": 9, "verdict": "worth_viewing"} Hope that helps!'
        result = parse_json_safe(text, FALLBACK)
        assert result["rating"] == 9

    def test_returns_fallback_for_empty_string(self):
        result = parse_json_safe("", FALLBACK)
        assert result == FALLBACK

    def test_returns_fallback_for_none_like_empty(self):
        result = parse_json_safe("no json here at all", FALLBACK)
        assert result == FALLBACK

    def test_returns_fallback_for_malformed_json(self):
        result = parse_json_safe('{"rating": 8, "verdict": }', FALLBACK)
        assert result == FALLBACK

    def test_preserves_nested_objects(self):
        text = '{"rating": 8, "specs": {"make": "Mini", "year": 2019, "mileage_km": 50000}}'
        result = parse_json_safe(text, FALLBACK)
        assert result["specs"]["make"] == "Mini"
        assert result["specs"]["mileage_km"] == 50000

    def test_preserves_lists(self):
        text = '{"rating": 7, "red_flags": ["No service history", "High mileage"]}'
        result = parse_json_safe(text, FALLBACK)
        assert len(result["red_flags"]) == 2


class TestFormatAd:

    def test_basic_format(self):
        data = {
            "url": "https://www.olx.pl/d/oferta/test-ID123.html",
            "title": "Mini Cooper 2018",
            "price": "45 000 zł",
            "location": "Warszawa",
        }
        result = _format_ad(data)
        assert "URL: https://www.olx.pl" in result
        assert "Title:\nMini Cooper 2018" in result
        assert "Price:\n45 000 zł" in result
        assert "Location:\nWarszawa" in result

    def test_omits_missing_fields(self):
        data = {"url": "https://olx.pl/test", "title": "VW Golf"}
        result = _format_ad(data)
        assert "Description" not in result
        assert "Parameters" not in result

    def test_includes_parameters(self):
        data = {"url": "https://olx.pl/x", "parameters": "Rok: 2018\nPrzebieg: 80 000 km"}
        result = _format_ad(data)
        assert "Parameters" in result
        assert "Rok: 2018" in result

    def test_includes_error(self):
        data = {"url": "https://olx.pl/x", "error": "Page failed to load"}
        result = _format_ad(data)
        assert "Error" in result
