"""
scraper.is_single_ad_url — one listing vs. search/filter page, decided from the parsed
host + path only, behind the same allowlist as /analyze. Pure Python: no DB, no browser.
"""
import pytest

from service import funnel, scraper

ADS = [
    # OLX
    "https://www.olx.pl/d/oferta/mini-cooper-s-2018-CID5-ID123abc.html",
    "https://olx.pl/d/oferta/iphone-13-CID99-IDxyz.html",
    "https://m.olx.pl/d/oferta/mieszkanie-CID3-ID1.html",
    "https://www.olx.pl/d/oferta/mini-cooper-CID5-ID123abc.html?reason=extended_search_extended_distance",
    "https://www.olx.pl/d/oferta/mini-cooper-CID5-ID123abc.html#photo-2",
    "https://www.olx.pl/d/oferta/mini-cooper-CID5-ID123abc/",
    "HTTPS://WWW.OLX.PL/d/oferta/mini-cooper-CID5-ID123abc.html",       # upper-case scheme + host
    # Otomoto
    "https://www.otomoto.pl/osobowe/oferta/mini-paceman-cooper-s-ID6HxYz1.html",
    "https://www.otomoto.pl/dostawcze/oferta/renault-master-ID6Habc2.html",
    "https://www.otomoto.pl/motocykle-i-quady/oferta/yamaha-r6-ID6Hdef3.html",
    "https://www.otomoto.pl/oferta/mini-paceman-ID6HxYz1.html",
    "https://www.otomoto.pl/osobowe/oferta/mini-paceman-ID6HxYz1.html?utm_source=share",
    # Otodom
    "https://www.otodom.pl/pl/oferta/mieszkanie-3-pokoje-mokotow-ID4abcd",
    "https://www.otodom.pl/en/oferta/flat-warsaw-ID4abcd",
    "https://www.otodom.pl/oferta/dom-ID4zzzz",
    "https://www.otodom.pl/pl/oferta/mieszkanie-3-pokoje-mokotow-ID4abcd/",
]

FILTERS = [
    "https://www.olx.pl/motoryzacja/samochody/q-mini/",
    "https://www.olx.pl/motoryzacja/samochody/q-mini/?search%5Bfilter_float_price:to%5D=60000",
    "https://www.olx.pl/elektronika/telefony/",
    "https://www.olx.pl/d/motoryzacja/samochody/",                  # /d/ category page, not /d/oferta/
    "https://www.olx.pl/d/oferta/",                                  # no slug
    "https://www.olx.pl/",
    "https://www.otomoto.pl/osobowe/mini",
    "https://www.otomoto.pl/osobowe/mini/paceman?search%5Bfilter_float_year:from%5D=2014",
    "https://www.otomoto.pl/osobowe/oferta/",                        # no slug
    "https://www.otomoto.pl/osobowe/mini/oferta-specjalna",          # "oferta" only as a prefix of a word
    "https://www.otodom.pl/pl/wyniki/sprzedaz/mieszkanie/cala-polska",
    "https://www.otodom.pl/pl/wyniki/sprzedaz/mieszkanie/mazowieckie?priceMax=900000",
    "https://www.otodom.pl/pl/oferta/",                              # no slug
    "https://www.otodom.pl/pl/wyniki/wynajem/oferta/warszawa",       # "oferta" too deep in the path
]

HOSTILE = [
    "https://anything/?olx.pl",
    "https://anything/d/oferta/x-ID1.html?olx.pl",
    "https://olx.pl.evil.com/d/oferta/x-ID1.html",
    "https://evil.com/olx.pl/d/oferta/x-ID1.html",
    "https://evil-olx.pl/d/oferta/x-ID1.html",
    "https://notolx.pl/d/oferta/x-ID1.html",
    "https://user@olx.pl@evil.com/d/oferta/x-ID1.html",
    "https://user:pass@olx.pl/d/oferta/x-ID1.html",
    "https://olx.pl@evil.com/d/oferta/x-ID1.html",
    "https://evil.com\\@olx.pl/d/oferta/x-ID1.html",
    "http://127.0.0.1/d/oferta/x-ID1.html",
    "http://127.0.0.1/",
    "http://[::1]/osobowe/oferta/x-ID1.html",
    "http://169.254.169.254/pl/oferta/x-ID1",
    "https://olx.pl:8443/d/oferta/x-ID1.html",
    "https://olx.pl./d/oferta/x-ID1.html",
    "ftp://www.olx.pl/d/oferta/x-ID1.html",
    "//www.olx.pl/d/oferta/x-ID1.html",
    "www.olx.pl/d/oferta/x-ID1.html",
    "javascript://www.olx.pl/d/oferta/x-ID1.html%0aalert(1)",
    "https://www.olx.pl/d/oferta/x ID1.html",
    "",
    "   ",
]


@pytest.mark.parametrize("url", ADS)
def test_ad_urls_are_single_ads(url):
    assert scraper.is_single_ad_url(url) is True


@pytest.mark.parametrize("url", FILTERS)
def test_search_and_category_urls_stay_filter_mode(url):
    assert scraper.is_single_ad_url(url) is False


@pytest.mark.parametrize("url", HOSTILE)
def test_hostile_or_malformed_urls_are_never_single_ads(url):
    assert scraper.is_single_ad_url(url) is False


def test_surrounding_whitespace_is_ignored_like_in_analyze():
    # url_guard strips the ends exactly as /analyze does before validating
    assert scraper.is_single_ad_url("  https://www.olx.pl/d/oferta/x-CID5-ID1.html \n") is True
    # …but whitespace INSIDE the URL is rejected
    assert scraper.is_single_ad_url("https://www.olx.pl/d/oferta/x -CID5-ID1.html") is False


def test_non_string_input_does_not_raise():
    assert scraper.is_single_ad_url(None) is False  # type: ignore[arg-type]


@pytest.mark.parametrize("url", ADS)
def test_funnel_label_agrees_for_ads(url):
    """The logging label ("d:ad", "osobowe:ad", …) and the pipeline switch must not drift apart."""
    assert funnel.url_label(url)[1].endswith(":ad")


@pytest.mark.parametrize("url", [u for u in FILTERS if "/oferta" not in u])
def test_funnel_label_agrees_for_non_ads(url):
    assert not funnel.url_label(url)[1].endswith(":ad")


def test_single_ad_label_is_d_ad_for_olx():
    assert funnel.url_label("https://www.olx.pl/d/oferta/mini-CID5-ID1.html") == ("olx.pl", "d:ad")
