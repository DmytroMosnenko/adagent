"""Tests for service/schemas.py — pure Python, no DB."""
import pytest
from pydantic import ValidationError
from service.schemas import AnalyzeRequest, CheckoutRequest


class TestAnalyzeRequest:

    def test_valid_olx_url(self):
        r = AnalyzeRequest(filter_url="https://www.olx.pl/motoryzacja/samochody/q-mini/",
                           prompt_preset="vehicles")
        assert r.filter_url.startswith("https://")
        assert r.prompt_preset == "vehicles"

    def test_valid_otomoto_url(self):
        r = AnalyzeRequest(filter_url="https://www.otomoto.pl/osobowe/mini")
        assert "otomoto.pl" in r.filter_url

    def test_valid_otodom_url(self):
        r = AnalyzeRequest(filter_url="https://www.otodom.pl/pl/wyniki/sprzedaz/mieszkanie/")
        assert "otodom.pl" in r.filter_url

    def test_strips_whitespace_from_url(self):
        r = AnalyzeRequest(filter_url="  https://www.olx.pl/auta/  ")
        assert r.filter_url == "https://www.olx.pl/auta/"

    def test_rejects_non_olx_url(self):
        with pytest.raises(ValidationError, match="olx.pl"):
            AnalyzeRequest(filter_url="https://www.allegro.pl/oferty/kupuj-teraz")

    def test_rejects_url_without_https(self):
        with pytest.raises(ValidationError, match="http"):
            AnalyzeRequest(filter_url="olx.pl/motoryzacja/")

    def test_valid_preset_vehicles(self):
        r = AnalyzeRequest(filter_url="https://www.olx.pl/auta/", prompt_preset="vehicles")
        assert r.prompt_preset == "vehicles"

    def test_valid_preset_realestate(self):
        r = AnalyzeRequest(filter_url="https://www.olx.pl/nieruchomosci/", prompt_preset="realestate")
        assert r.prompt_preset == "realestate"

    def test_rejects_unknown_preset(self):
        with pytest.raises(ValidationError, match="preset"):
            AnalyzeRequest(filter_url="https://www.olx.pl/", prompt_preset="electronics")

    def test_no_preset_allowed(self):
        r = AnalyzeRequest(filter_url="https://www.olx.pl/", prompt_preset=None,
                           custom_ad_prompt="Analyze this", custom_summary_prompt="Summarize")
        assert r.prompt_preset is None


class TestCheckoutRequest:

    def test_default_plan_is_monthly(self):
        r = CheckoutRequest()
        assert r.plan == "monthly"

    def test_valid_plans(self):
        for plan in ("monthly", "weekly", "daily"):
            assert CheckoutRequest(plan=plan).plan == plan

    def test_rejects_unknown_plan(self):
        with pytest.raises(ValidationError):
            CheckoutRequest(plan="yearly")
