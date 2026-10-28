"""
Pre-generated example reports for the landing-page instant demo.

Static data, rendered server-side into the landing page: a click costs no LLM call,
no scraping and not even a network request. This is illustrative content, shown
to visitors under an "Example" label — NOT a live search. Replace the rows with
real data from a finished report whenever you like; the template only needs this shape.

Public repo: no prompts, presets or secrets belong here — just example verdicts.

Shape per example:
  id, icon, title{pl,en}, query{pl,en}, rows[...], summary{pl,en}, top{red_flags{pl,en}, positives{pl,en}}
  row: title, price (PLN int), score (1-10), verdict (worth_viewing|maybe|skip),
       price_tag (fair|high), reason{pl,en}
"""
from __future__ import annotations

DEMO_EXAMPLES: list[dict] = [
    {
        "id": "car1",
        "icon": "🚗",
        "title": {"pl": "Auto z automatem, do 60 tys. zł", "en": "Automatic car, up to 60k PLN"},
        "query": {"pl": "OLX Motoryzacja · automat · diesel · do 60 000 zł",
                  "en": "OLX Motors · automatic · diesel · up to 60,000 PLN"},
        "rows": [
            {"title": "Renault Megane 1.5 Blue dCi Intens EDC", "price": 54900, "score": 8.1,
             "verdict": "worth_viewing", "price_tag": "fair",
             "reason": {"pl": "Dealer deklaruje szeroki serwis, w tym wymianę rozrządu i oleju w skrzyni EDC.",
                        "en": "The dealer states extensive servicing, including the timing belt and EDC gearbox oil."}},
            {"title": "Renault Megane 1.5 Blue dCi Intens EDC", "price": 52900, "score": 7.2,
             "verdict": "worth_viewing", "price_tag": "fair",
             "reason": {"pl": "Przed zakupem koniecznie potwierdź historię serwisową i termin wymiany rozrządu.",
                        "en": "Before buying, confirm the service history and when the timing belt was last changed."}},
            {"title": "Audi Q3", "price": 54900, "score": 6.7,
             "verdict": "worth_viewing", "price_tag": "fair",
             "reason": {"pl": "Udokumentowana historia i atrakcyjna specyfikacja — sprawdź stan skrzyni i podwozia.",
                        "en": "Documented history and a good spec — have the gearbox and underbody checked."}},
            {"title": "MINI Countryman Cooper SD ALL4", "price": 59900, "score": 6.5,
             "verdict": "maybe", "price_tag": "fair",
             "reason": {"pl": "Warto rozważyć po sprawdzeniu historii serwisowej, zwłaszcza automatu i napędu ALL4.",
                        "en": "Worth considering once the service history is checked, especially the automatic and ALL4 drive."}},
            {"title": "Kia Sportage", "price": 57900, "score": 6.4,
             "verdict": "maybe", "price_tag": "fair",
             "reason": {"pl": "Warto rozważyć po sprawdzeniu historii serwisowej i dokładnym teście 7DCT.",
                        "en": "Worth considering after checking the service history and test-driving the 7DCT closely."}},
        ],
        "summary": {
            "pl": "Ceny od 52 900 do 59 900 zł, średnio ok. 56 100 zł. Zacznij od ofert nr 1–3 — mają najlepiej udokumentowany serwis.",
            "en": "Prices run from 52,900 to 59,900 PLN, about 56,100 on average. Start with offers 1–3 — they have the best documented servicing.",
        },
        "top": {
            "red_flags": {"pl": ["Skrzynia EDC: poproś o potwierdzenie wymiany oleju w historii serwisowej",
                                 "W opisie brak informacji o liczbie właścicieli"],
                          "en": ["EDC gearbox: ask for proof of the oil change in the service history",
                                 "The ad does not say how many owners the car had"]},
            "positives": {"pl": ["Deklarowany szeroki serwis (rozrząd, olej w skrzyni)",
                                 "Cena zgodna z rynkiem dla tej wersji"],
                          "en": ["Extensive servicing declared (timing belt, gearbox oil)",
                                 "Price in line with the market for this version"]},
        },
    },
    {
        "id": "car2",
        "icon": "🚙",
        "title": {"pl": "Octavia kombi diesel, do 45 tys. zł", "en": "Octavia estate diesel, up to 45k PLN"},
        "query": {"pl": "Otomoto · Škoda Octavia · kombi · diesel · do 45 000 zł",
                  "en": "Otomoto · Škoda Octavia · estate · diesel · up to 45,000 PLN"},
        "rows": [
            {"title": "Škoda Octavia III 2.0 TDI Ambition kombi, 2015", "price": 44500, "score": 7.8,
             "verdict": "worth_viewing", "price_tag": "fair",
             "reason": {"pl": "Komplet faktur z serwisu i wymieniony rozrząd — najlepiej udokumentowana oferta.",
                        "en": "Full service invoices and a replaced timing belt — the best documented offer."}},
            {"title": "Škoda Octavia III 1.6 TDI Active kombi, 2016", "price": 41900, "score": 7.1,
             "verdict": "worth_viewing", "price_tag": "fair",
             "reason": {"pl": "Dobra cena, ale sprzedawca nie podaje przebiegu w opisie — poproś o książkę serwisową.",
                        "en": "Good price, but the seller gives no mileage in the text — ask for the service book."}},
            {"title": "Škoda Octavia II 1.9 TDI Elegance kombi, 2011", "price": 24900, "score": 6.2,
             "verdict": "maybe", "price_tag": "fair",
             "reason": {"pl": "Niska cena, ale wiek auta wymaga oględzin zawieszenia i układu wydechowego.",
                        "en": "Low price, but the car's age calls for a check of the suspension and exhaust."}},
            {"title": "Škoda Octavia III 2.0 TDI Style kombi, 2014", "price": 44900, "score": 6.0,
             "verdict": "maybe", "price_tag": "high", "reason": {
                 "pl": "Cena powyżej typowej dla tego rocznika, a opis jest bardzo ogólnikowy.",
                 "en": "Priced above what is typical for this year, and the description is very generic."}},
            {"title": "Škoda Octavia III 1.6 TDI Ambition kombi, 2015", "price": 38500, "score": 4.3,
             "verdict": "skip", "price_tag": "fair",
             "reason": {"pl": "Opis sugeruje naprawę powypadkową bez żadnej dokumentacji — lepiej pominąć.",
                        "en": "The text hints at accident repairs with no documentation — better to skip."}},
        ],
        "summary": {
            "pl": "Ceny od 24 900 do 44 900 zł, średnio ok. 38 900 zł. Najpierw obejrzyj ofertę nr 1 i 2; ofertę nr 5 pomiń.",
            "en": "Prices run from 24,900 to 44,900 PLN, about 38,900 on average. View offers 1 and 2 first; skip offer 5.",
        },
        "top": {
            "red_flags": {"pl": ["Sprawdź, czy przebieg w liczniku zgadza się z fakturami",
                                 "Zapytaj o historię z Carfax/AutoDNA"],
                          "en": ["Check that the odometer matches the invoices",
                                 "Ask for a Carfax/AutoDNA history report"]},
            "positives": {"pl": ["Komplet faktur z serwisu", "Wymieniony rozrząd"],
                          "en": ["Full service invoices", "Timing belt already replaced"]},
        },
    },
    {
        "id": "flat1",
        "icon": "🏢",
        "title": {"pl": "2 pokoje w Krakowie, do 650 tys. zł", "en": "2-room flat in Kraków, up to 650k PLN"},
        "query": {"pl": "Otodom · mieszkanie · Kraków · 2 pokoje · do 650 000 zł",
                  "en": "Otodom · flat · Kraków · 2 rooms · up to 650,000 PLN"},
        "rows": [
            {"title": "2 pokoje, 44 m², Krowodrza, blok z 2008 r.", "price": 639000, "score": 8.0,
             "verdict": "worth_viewing", "price_tag": "fair",
             "reason": {"pl": "Nowsze budownictwo, miejsce postojowe w cenie, czynsz podany w opisie.",
                        "en": "Newer building, parking space included, monthly fees stated in the ad."}},
            {"title": "2 pokoje, 41 m², Podgórze, kamienica po remoncie", "price": 625000, "score": 7.2,
             "verdict": "worth_viewing", "price_tag": "fair",
             "reason": {"pl": "Warto obejrzeć — zapytaj o stan instalacji i fundusz remontowy wspólnoty.",
                        "en": "Worth viewing — ask about the condition of the installations and the owners' renovation fund."}},
            {"title": "2 pokoje, 47 m², Prądnik Biały, blok z lat 80.", "price": 598000, "score": 6.4,
             "verdict": "maybe", "price_tag": "fair",
             "reason": {"pl": "Dobra lokalizacja, ale budynek z wielkiej płyty — sprawdź ocieplenie i opłaty.",
                        "en": "Good location, but a large-panel building — check the insulation and the fees."}},
            {"title": "2 pokoje, 38 m², Nowa Huta, blok z lat 70.", "price": 520000, "score": 5.9,
             "verdict": "maybe", "price_tag": "fair",
             "reason": {"pl": "Niższa cena, ale mały metraż i starszy budynek — sprawdź stan klatki i pionów.",
                        "en": "Lower price, but small and in an older building — check the stairwell and risers."}},
            {"title": "2 pokoje, 45 m², Bronowice, parter", "price": 649000, "score": 4.5,
             "verdict": "skip", "price_tag": "high",
             "reason": {"pl": "Cena wyraźnie powyżej ofert o podobnym metrażu, a to parter bez balkonu.",
                        "en": "Priced well above similar-sized offers, and it is a ground floor with no balcony."}},
        ],
        "summary": {
            "pl": "Ceny od 520 000 do 649 000 zł, średnio ok. 606 000 zł. Najpierw obejrzyj oferty nr 1 i 2; ofertę nr 5 pomiń.",
            "en": "Prices run from 520,000 to 649,000 PLN, about 606,000 on average. View offers 1 and 2 first; skip offer 5.",
        },
        "top": {
            "red_flags": {"pl": ["Zapytaj o wysokość czynszu administracyjnego i planowane remonty",
                                 "Sprawdź księgę wieczystą"],
                          "en": ["Ask about the monthly admin fee and planned renovations",
                                 "Check the land and mortgage register"]},
            "positives": {"pl": ["Budynek z 2008 r.", "Miejsce postojowe w cenie"],
                          "en": ["Building from 2008", "Parking space included"]},
        },
    },
]
