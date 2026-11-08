"""
Clickable ad mentions in the overview ("recommendation") block.

The model is asked to wrap each named listing in {{ref:N}}...{{/ref}}, but it
sometimes wraps only part of the name, omits the wrapper, or slips on the
marker syntax. Whatever it does, the buyer must see a link on the WHOLE
mention (where we can identify it), and never a leaked "{{ref" tag.
"""
import re

from service.report_builder import _build_recommended_links, _linkify_recommendation

PHONE = {"url": "https://www.olx.pl/d/oferta/realme-1.html", "title": "Realme GT Neo 3T 8/128 GB",
         "make": "Realme", "model": "GT Neo 3T", "district": "", "spec_items": []}
IPHONE = {"url": "https://www.olx.pl/d/oferta/iphone-2.html", "title": "iPhone 11 64GB",
          "make": "Apple", "model": "iPhone 11", "district": "",
          "spec_items": [{"label": "Storage", "value": "64 GB"}]}
IPHONE_2 = dict(IPHONE, url="https://www.olx.pl/d/oferta/iphone-3.html")


def linked(text, ads):
    results = [{"url": a["url"]} for a in ads]
    out = str(_linkify_recommendation(text, _build_recommended_links([], ads), ads, results))
    return re.findall(r'<a [^>]*>(.*?)<span class="rec-inline-arrow"> ↓</span></a>', out), out


def test_marker_wrapping_only_part_of_the_name_is_extended():
    got, out = linked("Перевірте {{ref:1}}Realme GT{{/ref}} Neo 3T 8/128 ГБ із коробкою.", [PHONE])
    assert got == ["Realme GT Neo 3T 8/128 ГБ"]
    assert "із коробкою" in out and "{{" not in out


def test_mention_without_markers_links_whole_name():
    got, _ = linked("Перевірте Realme GT Neo 3T 8/128 ГБ із коробкою.", [PHONE])
    assert got == ["Realme GT Neo 3T 8/128 ГБ"]


def test_bare_model_is_linked_when_brand_is_not_repeated():
    """'iPhone 11', not 'Apple iPhone 11' — used to stay dead text."""
    got, _ = linked("Розгляньте iPhone 11 на 64 ГБ за помірну ціну.", [PHONE, IPHONE])
    assert got == ["iPhone 11"]


def test_bare_model_not_guessed_when_two_ads_share_it():
    got, _ = linked("Розгляньте iPhone 11 на 64 ГБ.", [IPHONE, IPHONE_2])
    assert got == []


def test_marker_syntax_slips_are_tolerated_and_never_leak():
    text = "Перевірте {{ ref: 1 }}Realme GT Neo 3T{{ /ref }} і {{Ref:#2}}iPhone 11{{/REF}}."
    got, out = linked(text, [PHONE, IPHONE])
    assert len(got) == 2
    assert "{{" not in out and "}}" not in out


def test_extension_stops_at_words_that_are_not_part_of_the_name():
    got, _ = linked("Перевірте {{ref:1}}Realme GT{{/ref}} перш за все.", [PHONE])
    assert got == ["Realme GT"]


def test_extension_does_not_swallow_the_next_marked_listing():
    text = "{{ref:2}}iPhone 11{{/ref}} 64 {{ref:1}}Realme GT{{/ref}} Neo"
    got, _ = linked(text, [PHONE, IPHONE])
    assert len(got) == 2                       # two separate links, no nesting
    assert got[0].startswith("iPhone 11") and got[1].startswith("Realme GT")


def test_unresolvable_ref_keeps_text_unlinked_without_tags():
    got, out = linked("Перевірте {{ref:9}}якийсь телефон{{/ref}}.", [PHONE])
    assert got == [] and "якийсь телефон" in out and "{{" not in out
