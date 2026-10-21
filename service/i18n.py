"""
Tiny gettext-style UI translation (English source string = key).

Why not Babel/gettext: the UI has ~250 short strings in two languages and the
project prefers no extra moving parts. A missing key simply falls back to the
English text, so an untranslated string can never break a page.

Usage
  templates:   {{ t("Sign in") }}   /   {{ t("first {n} ads", n=5) }}
  Python:      translate("Sign in", "pl")
"""
from __future__ import annotations

from typing import Callable, Optional

UI_LANGUAGES = ("en", "pl")
DEFAULT_UI_LANGUAGE = "en"


def normalize(lang: Optional[str]) -> Optional[str]:
    """Return 'en' / 'pl' for a supported code (or a regional variant), else None."""
    if not lang:
        return None
    primary = lang.strip().lower().split("-")[0].split("_")[0]
    return primary if primary in UI_LANGUAGES else None


def pick_ui_language(query_lang: Optional[str],
                     cookie_lang: Optional[str],
                     accept_language: Optional[str]) -> str:
    """?lang= beats the ui_lang cookie beats Accept-Language beats English."""
    for candidate in (query_lang, cookie_lang):
        found = normalize(candidate)
        if found:
            return found
    if accept_language:
        for part in accept_language.split(","):
            tag = part.split(";")[0].strip()
            primary = tag.lower().split("-")[0]
            if primary == "pl":
                return "pl"
            if primary == "en":
                return "en"
    return DEFAULT_UI_LANGUAGE


def translate(text: str, lang: Optional[str] = None, **kw) -> str:
    """Translate `text` (English) into `lang`; format with **kw afterwards."""
    out = text
    if lang == "pl" and isinstance(text, str):
        hit = PL.get(text)
        if hit is None:
            low = text.lower()
            hit = PL.get(low)
            if hit is not None and text[:1].isupper():
                hit = hit[:1].upper() + hit[1:]
        if hit is not None:
            out = hit
    if kw:
        out = out.format(**kw)
    return out


def translator(lang: Optional[str]) -> Callable[..., str]:
    def t(text: str, **kw) -> str:
        return translate(text, lang, **kw)
    return t


# ── Polish ────────────────────────────────────────────────────────────────────
PL: dict[str, str] = {
    # ── base / footer / consent ──
    "AdAgent — AI-powered ad analysis": "AdAgent — analiza ogłoszeń z AI",
    "Paste an OLX, Otomoto or Otodom search link. AI reads every ad, scores it 1–10, flags red flags and known model weak points, and ranks the offers for you.":
        "Wklej link do wyszukiwania z OLX, Otomoto lub Otodom. AI przeczyta każde ogłoszenie, oceni je w skali 1–10, wskaże czerwone flagi i typowe usterki modelu oraz ułoży oferty w ranking.",
    "My History": "Historia",
    "Sign out": "Wyloguj",
    "Upgrade →": "Przejdź na Pro →",
    "Sign in": "Zaloguj",
    "Go Pro →": "Wersja Pro",
    "Billing Details": "Rozliczenia",
    "Privacy Policy": "Polityka prywatności",
    "Terms of Service": "Regulamin",
    "Cookie settings": "Ustawienia cookies",
    "AdAgent is not affiliated with OLX Group, Otomoto, or Otodom": "AdAgent nie jest powiązany z OLX Group, Otomoto ani Otodom",
    "Cookie consent": "Zgoda na cookies",
    "We use cookies to measure advertising and analytics only if you agree.": "Używamy plików cookie do pomiaru skuteczności reklam i statystyk tylko za Twoją zgodą.",
    "Reject": "Odrzuć",
    "Accept": "Akceptuję",

    # ── landing ──
    "AdAgent — Analyze OLX ads with AI": "AdAgent — analiza ogłoszeń OLX, Otomoto i Otodom z AI",
    "Find the best deal,": "Znajdź najlepszą ofertę,",
    "powered by AI": "z pomocą AI",
    "Paste any OLX search link. AdAgent reads every ad, scores it 1–10, flags red flags, and gives you a ranked leaderboard in minutes.":
        "Wklej link do wyszukiwania z OLX, Otomoto lub Otodom. AdAgent przeczyta każde ogłoszenie, oceni je w skali 1–10, wskaże czerwone flagi i przygotuje ranking ofert.",
    "OLX / Otomoto / Otodom filter URL": "Link do wyszukiwania z OLX / Otomoto / Otodom",
    "Go to OLX, apply your filters, copy the URL from the browser bar.": "Wejdź na OLX, Otomoto lub Otodom, ustaw filtry i skopiuj adres z paska przeglądarki.",
    "Your search link (OLX, Otomoto or Otodom)": "Twój link do wyszukiwania (OLX, Otomoto lub Otodom)",
    "Open OLX, Otomoto or Otodom, set your filters (brand, price, year…), then copy the address from the browser bar and paste it here.":
        "Wejdź na OLX, Otomoto lub Otodom, ustaw filtry (marka, cena, rok…), skopiuj adres z paska przeglądarki i wklej go tutaj.",
    "Example AdAgent report: a ranked leaderboard of car offers with scores and verdicts":
        "Przykładowy raport AdAgent: ranking ofert samochodów z ocenami i werdyktami",
    "Example": "Przykład",
    "What a finished report looks like": "Tak wygląda gotowy raport",
    "Open the full sample report →": "Otwórz pełny przykładowy raport →",
    "Analysis preset": "Rodzaj analizy",
    "Custom prompts": "Własne prompty",
    "Write your own analysis instructions": "Napisz własne instrukcje analizy",
    "Per-ad analysis prompt": "Prompt analizy pojedynczego ogłoszenia",
    "You are an expert buyer. Analyze this ad and return a short verdict…": "Jesteś doświadczonym kupującym. Przeanalizuj to ogłoszenie i podaj krótki werdykt…",
    "Summary prompt": "Prompt podsumowania",
    "Based on the analyses above, summarize the market and recommend the best options…": "Na podstawie powyższych analiz podsumuj rynek i wskaż najlepsze oferty…",
    "Report language": "Język raportu",
    "AI analysis and summary will be written in this language.": "Analiza AI i podsumowanie zostaną napisane w tym języku.",
    "Free tier:": "Wersja darmowa:",
    "first {n} ads analyzed.": "liczba analizowanych ogłoszeń: {n}.",
    "Upgrade to Pro": "Przejdź na Pro",
    "to unlock all ads.": "aby odblokować wszystkie ogłoszenia.",
    "all ads will be analyzed.": "wszystkie ogłoszenia zostaną przeanalizowane.",
    "Analyze →": "Analizuj →",
    "No sign-up needed for the free tier. Analysis takes about 5–10 minutes — you can close the tab and come back.":
        "Wersja darmowa nie wymaga rejestracji. Analiza trwa ok. 5–10 minut — możesz zamknąć kartę i wrócić później.",
    "See a sample report →": "Zobacz przykładowy raport →",
    "How it works": "Jak to działa",
    "Paste a search link": "Wklej link do wyszukiwania",
    "Set your filters on OLX, Otomoto or Otodom and copy the address from the browser bar.": "Ustaw filtry na OLX, Otomoto lub Otodom i skopiuj adres z paska przeglądarki.",
    "AI reads every ad": "AI czyta każde ogłoszenie",
    "Each listing is analyzed one by one — the ad text is cross-checked with what is known about that exact model or building.":
        "Każde ogłoszenie jest analizowane osobno — treść oferty jest zestawiana z tym, co wiadomo o danym modelu lub budynku.",
    "Get a ranked report": "Odbierz raport z rankingiem",
    "A leaderboard with scores, red flags and a market summary — so you know which offers to view first.":
        "Ranking z ocenami, czerwonymi flagami i podsumowaniem rynku — wiesz, które oferty obejrzeć w pierwszej kolejności.",
    "What is in the report": "Co zawiera raport",
    "Score 1–10 and a verdict": "Ocena 1–10 i werdykt",
    "worth viewing, maybe, or skip, with a one-line reason.": "warto obejrzeć, może albo pomiń — z jednozdaniowym uzasadnieniem.",
    "Red flags and positives": "Czerwone flagi i plusy",
    "short, concrete points taken from the ad itself.": "krótkie, konkretne punkty wyciągnięte z samego ogłoszenia.",
    "Known weak points at this mileage": "Typowe usterki przy tym przebiegu",
    "for cars: typical failures of the model and engine compared with the repairs the seller mentions.":
        "dla samochodów: znane awarie modelu i silnika zestawione z naprawami, o których pisze sprzedawca.",
    "Real-world consumption": "Realne spalanie",
    "owner-level fuel figures, not just the factory number.": "wartości z doświadczeń właścicieli, a nie tylko dane fabryczne.",
    "Location and building profile": "Profil lokalizacji i budynku",
    "for flats and houses: who the area suits and the typical issues of that building type and era.":
        "dla mieszkań i domów: dla kogo nadaje się okolica oraz typowe problemy budynków z danej epoki i technologii.",
    "Market summary": "Podsumowanie rynku",
    "price range, average price and which ads to look at first.": "przedział cen, średnia cena i wskazówka, które ogłoszenia obejrzeć najpierw.",
    "Frequently asked questions": "Najczęstsze pytania",
    "How long does an analysis take?": "Ile trwa analiza?",
    "Usually 5–10 minutes, depending on how many ads the search returns. You can close the tab — the analysis continues, and you can get a notification when it is done.":
        "Zwykle 5–10 minut, w zależności od liczby ogłoszeń w wyszukiwaniu. Możesz zamknąć kartę — analiza trwa dalej, a po zakończeniu możesz dostać powiadomienie.",
    "Does it work for real estate too?": "Czy działa też dla nieruchomości?",
    "Yes. Choose the Real Estate preset for Otodom and OLX property searches, or the Vehicles preset for Otomoto and OLX motor searches.":
        "Tak. Wybierz rodzaj analizy „Nieruchomości” dla wyszukiwań na Otodom i OLX albo „Pojazdy” dla Otomoto i OLX Motoryzacja.",
    "Can I trust the AI verdict?": "Czy mogę ufać werdyktowi AI?",
    "Treat it as a fast, well-informed first filter, not as an inspection. The AI can make mistakes and the ad itself may be inaccurate — always check the car or property in person before you pay.":
        "Traktuj go jako szybki, dobrze poinformowany pierwszy filtr, a nie oględziny. AI może się mylić, a samo ogłoszenie może być nieścisłe — zawsze obejrzyj samochód lub nieruchomość osobiście, zanim zapłacisz.",
    "Is AdAgent affiliated with OLX, Otomoto or Otodom?": "Czy AdAgent jest powiązany z OLX, Otomoto lub Otodom?",
    "No. AdAgent is an independent tool and is not affiliated with, or endorsed by, OLX Group, Otomoto or Otodom.":
        "Nie. AdAgent to niezależne narzędzie — nie jest powiązany z OLX Group, Otomoto ani Otodom i nie jest przez nie rekomendowany.",
    "What is free and what is Pro?": "Co jest darmowe, a co w Pro?",
    "The free tier analyzes the first {n} ads of every search. Pro analyzes all ads and keeps your report history.":
        "Wersja darmowa analizuje pierwsze ogłoszenia z każdego wyszukiwania (limit: {n}). Pro analizuje wszystkie ogłoszenia i zapisuje historię raportów.",
    "Can I cancel the subscription?": "Czy mogę anulować subskrypcję?",
    "Yes, at any time from the billing portal. You keep Pro access until the end of the period you already paid for.":
        "Tak, w dowolnej chwili w panelu rozliczeń. Dostęp Pro zachowujesz do końca opłaconego okresu.",
    "What you get with AdAgent Pro": "Co daje AdAgent Pro",
    "Free": "Darmowy",
    "$0": "0 zł",
    "First {n} ads per search": "Limit ogłoszeń na wyszukiwanie: {n}",
    "Leaderboard & AI scores": "Ranking i oceny AI",
    "Full search results": "Pełne wyniki wyszukiwania",
    "Report history": "Historia raportów",
    "You're on the free plan": "Korzystasz z planu darmowego",
    "/month": "/mies.",
    "All ads analyzed": "Analiza wszystkich ogłoszeń",
    "Upgrade now →": "Przejdź na Pro →",

    # ── report ──
    "AdAgent Report": "Raport AdAgent",
    "New search": "Nowe wyszukiwanie",
    "History": "Historia",
    "Ads:": "Ogłoszenia:",
    "View {site} filter ↗": "Zobacz filtr {site} ↗",
    "More ads were found but not analyzed: {more} (free plan: {shown} ads shown). Upgrade to AdAgent Pro to unlock all {total} results.":
        "Znaleziono więcej ogłoszeń, które nie zostały przeanalizowane: {more} (plan darmowy: pokazano {shown}). Przejdź na AdAgent Pro, aby odblokować wszystkie wyniki ({total}).",
    "Upgrade — {price}/month →": "Przejdź na Pro — {price}/mies. →",
    "Leaderboard — ranked by AI rating": "Ranking — według oceny AI",
    "Top Picks": "Najlepsze oferty",
    "Details ↓": "Szczegóły ↓",
    "Show {n} more ranked ad": "Pokaż kolejną pozycję w rankingu ({n})",
    "Show {n} more ranked ads": "Pokaż kolejne pozycje w rankingu ({n})",
    "Market Overview": "Przegląd rynku",
    "Price range": "Przedział cen",
    "Avg. price": "Śr. cena",
    "Avg. per m²": "Śr. cena za m²",
    "Ads analyzed": "Przeanalizowane ogłoszenia",
    "Ad Details": "Szczegóły ogłoszeń",
    "Full location & building analysis": "Pełna analiza lokalizacji i budynku",
    "Full model & reliability analysis": "Pełna analiza modelu i niezawodności",
    "Location profile": "Profil lokalizacji",
    "General specs (official)": "Dane ogólne (oficjalne)",
    "Renovations / upgrades / included items mentioned": "Wspomniane remonty / ulepszenia / wyposażenie w cenie",
    "All mentioned fixes / maintenance": "Wszystkie wspomniane naprawy / serwis",
    "Known issues for this building type / era": "Znane problemy dla tego typu i epoki budynku",
    "Known weak points for this model": "Znane słabe punkty tego modelu",
    "Running costs & market liquidity": "Koszty utrzymania i płynność rynkowa",
    "Real-world fuel consumption": "Realne spalanie",
    "Raw scraped data": "Surowe dane z ogłoszenia",
    "Location": "Lokalizacja",
    "Parameters": "Parametry",
    "Description": "Opis",
    "Page text (CSS extraction missed description)": "Tekst strony (nie udało się wyodrębnić opisu)",
    "Error": "Błąd",
    "Jump to market overview": "Przejdź do przeglądu rynku",
    "Overview": "Przegląd",
    "Navigate back": "Wróć",
    "Top": "Góra",
    "Show less": "Pokaż mniej",
    "Leaderboard": "Ranking",
    # labels produced by report_builder.py
    "Worth Viewing": "Warto obejrzeć",
    "Maybe": "Może",
    "Skip": "Pomiń",
    "Unknown": "Nieznane",
    "Fair price": "Uczciwa cena",
    "High price": "Wysoka cena",
    "Low price": "Niska cena",
    "Make": "Marka",
    "Model": "Model",
    "Year": "Rok",
    "Mileage": "Przebieg",
    "Engine": "Silnik",
    "Fuel": "Paliwo",
    "Gearbox": "Skrzynia biegów",
    "Body": "Nadwozie",
    "Color": "Kolor",
    "Type": "Typ",
    "Area": "Powierzchnia",
    "Rooms": "Pokoje",
    "Floor": "Piętro",
    "Built": "Rok budowy",
    "Condition": "Stan",
    "Heating": "Ogrzewanie",
    "Elevator": "Winda",
    "Parking": "Parking",
    "Monthly fees": "Opłaty miesięczne",
    "Yes": "Tak",
    "No": "Nie",
    # enum values the AI returns (looked up case-insensitively)
    "petrol": "benzyna", "diesel": "diesel", "electric": "elektryczny", "hybrid": "hybryda",
    "lpg": "LPG", "petrol+lpg": "benzyna + LPG",
    "manual": "manualna", "automatic": "automatyczna", "semi-automatic": "półautomatyczna",
    "sedan": "sedan", "hatchback": "hatchback", "suv": "SUV", "estate": "kombi", "coupe": "coupe",
    "convertible": "kabriolet", "van": "van", "pickup": "pickup",
    "apartment": "mieszkanie", "house": "dom", "studio": "kawalerka", "plot": "działka",
    "commercial": "lokal użytkowy", "garage": "garaż",
    "new": "nowy", "very good": "bardzo dobry", "good": "dobry", "to renovate": "do remontu",
    "developer state": "stan deweloperski",
    "underground": "podziemny", "street": "uliczny", "none": "brak",

    # ── status page ──
    "Analyzing… — AdAgent": "Analizuję… — AdAgent",
    "Analyzing your search…": "Analizuję Twoje wyszukiwanie…",
    "Starting up…": "Uruchamianie…",
    "This takes 5–10 minutes depending on the number of ads.": "To potrwa 5–10 minut, w zależności od liczby ogłoszeń.",
    "You can safely close this tab — the analysis continues in the background.": "Możesz bezpiecznie zamknąć tę kartę — analiza trwa w tle.",
    "Open report →": "Otwórz raport →",
    "Sign in to get notified by email": "Zaloguj się, aby dostać powiadomienie e-mail",
    "Send notification on email when finished": "Wyślij powiadomienie e-mail po zakończeniu",
    "Notify me in this browser when finished": "Powiadom mnie w przeglądarce po zakończeniu",
    "Report ID:": "ID raportu:",
    "Bookmark this page to return later:": "Dodaj tę stronę do zakładek, aby wrócić później:",
    "We'll email you when it's done.": "Wyślemy Ci e-mail, gdy będzie gotowe.",
    "Email notification turned off.": "Powiadomienie e-mail wyłączone.",
    "Couldn't save that — try again.": "Nie udało się zapisać — spróbuj ponownie.",
    "Not supported in this browser": "Ta przeglądarka tego nie obsługuje",
    "Browser notifications enabled.": "Powiadomienia w przeglądarce włączone.",
    "Notifications blocked — check your browser's site settings.": "Powiadomienia zablokowane — sprawdź ustawienia witryny w przeglądarce.",
    "AdAgent — analysis failed": "AdAgent — analiza nie powiodła się",
    "AdAgent — report ready": "AdAgent — raport gotowy",
    "Your ad analysis couldn't be completed.": "Nie udało się dokończyć analizy ogłoszeń.",
    "Your ad analysis has finished.": "Analiza ogłoszeń została zakończona.",
    "Analysis failed.": "Analiza nie powiodła się.",
    "Analysis complete.": "Analiza zakończona.",
    "View details →": "Zobacz szczegóły →",
    "⚠️ Failed — ": "⚠️ Błąd — ",
    "✅ Ready — ": "✅ Gotowe — ",
    "Collecting ad links…": "Zbieram linki do ogłoszeń…",
    "Collecting ad links from all pages…": "Zbieram linki do ogłoszeń ze wszystkich stron…",
    "Found {found} ads. Starting analysis…": "Znaleziono ogłoszeń: {found}. Rozpoczynam analizę…",
    "Scraping & analyzing ad {analyzed} of {total}…": "Analizuję ogłoszenie {analyzed} z {total}…",
    "Ads found: {found} · Processed: {analyzed} / {total}": "Znalezione ogłoszenia: {found} · Przetworzone: {analyzed} / {total}",

    # ── subscribe / success ──
    "AdAgent Pro — Pricing": "AdAgent Pro — cennik",
    "Unlock": "Odblokuj",
    "full analysis": "pełną analizę",
    "See every ad in your search — not just the first {n}.": "Zobacz każde ogłoszenie z wyszukiwania — nie tylko pierwsze {n}.",
    "You're already on AdAgent Pro.": "Masz już AdAgent Pro.",
    "Run a search →": "Uruchom wyszukiwanie →",
    "AI score & verdict per ad": "Ocena AI i werdykt dla każdego ogłoszenia",
    "Ranked leaderboard": "Ranking ofert",
    "All ads in results": "Wszystkie ogłoszenia w wynikach",
    "Continue free": "Kontynuuj za darmo",
    "Pro — Most popular": "Pro — najpopularniejszy",
    "Full report history": "Pełna historia raportów",
    "Cancel any time": "Anuluj w dowolnym momencie",
    "Subscribe": "Subskrybuj",
    "Other plans": "Inne plany",
    "{period} plan": "Plan {period}",
    "Monthly": "miesięczny", "Weekly": "tygodniowy", "Daily": "dzienny",
    "How it works:": "Jak to działa:",
    "after payment we email you a sign-in link. Click it to access your account and all your reports.":
        "po opłaceniu wyślemy Ci e-mail z linkiem do logowania. Kliknij go, aby wejść na konto i zobaczyć wszystkie raporty.",
    "Payment is processed securely by Stripe. We never store card details. Cancel any time from your Stripe portal.":
        "Płatność jest bezpiecznie obsługiwana przez Stripe. Nie przechowujemy danych karty. Subskrypcję możesz anulować w dowolnym momencie w panelu Stripe.",
    "Subscribed! — AdAgent": "Subskrypcja aktywna! — AdAgent",
    "You're now on AdAgent Pro!": "Masz teraz AdAgent Pro!",
    "We've sent a sign-in link to your email address.": "Wysłaliśmy link do logowania na Twój adres e-mail.",
    "Click it to access your account and start analyzing all ads.": "Kliknij go, aby wejść na konto i analizować wszystkie ogłoszenia.",
    "Start analyzing →": "Zacznij analizować →",
    "Check your inbox for the sign-in link.": "Sprawdź skrzynkę odbiorczą — czeka tam link do logowania.",
    "Request another sign-in link": "Poproś o nowy link do logowania",

    # ── auth / error / history ──
    "Sign in — AdAgent": "Logowanie — AdAgent",
    "Sign in to AdAgent": "Zaloguj się do AdAgent",
    "Enter your email and we'll send you a sign-in link. No password needed.": "Podaj adres e-mail, a wyślemy Ci link do logowania. Hasło nie jest potrzebne.",
    "Sign-in link sent to": "Link do logowania wysłano na adres",
    "Check your inbox (and spam folder).": "Sprawdź skrzynkę odbiorczą (oraz folder spam).",
    "We couldn't send the sign-in link right now. Please try again in a few minutes.": "Nie udało się teraz wysłać linku do logowania. Spróbuj ponownie za kilka minut.",
    "Email address": "Adres e-mail",
    "Send sign-in link →": "Wyślij link do logowania →",
    "Don't have an account? Just enter your email — one will be created automatically.": "Nie masz konta? Wystarczy, że podasz e-mail — konto utworzymy automatycznie.",
    "Error — AdAgent": "Błąd — AdAgent",
    "No matching ads": "Brak pasujących ogłoszeń",
    "Analysis failed": "Analiza nie powiodła się",
    "Found nothing — try again →": "Nic nie znaleziono — spróbuj ponownie →",
    "Start a new search": "Zacznij nowe wyszukiwanie",
    "Something went wrong during the analysis.": "Coś poszło nie tak podczas analizy.",
    "My Reports — AdAgent": "Moje raporty — AdAgent",
    "My Reports": "Moje raporty",
    "New search →": "Nowe wyszukiwanie →",
    "Your reports show only {n} ads each. Upgrade for full results.": "Limit ogłoszeń w raporcie: {n}. Przejdź na Pro, aby zobaczyć pełne wyniki.",
    "Upgrade to Pro →": "Przejdź na Pro →",
    "Date": "Data",
    "Filter URL": "Link wyszukiwania",
    "Preset": "Rodzaj",
    "Ads": "Ogłoszenia",
    "Status": "Status",
    "View": "Zobacz",
    "No reports yet.": "Brak raportów.",
    "Run your first analysis →": "Uruchom pierwszą analizę →",
    "Limited to {n} ads (free tier).": "Limit {n} ogłoszeń (wersja darmowa).",
    "Vehicles": "Pojazdy", "Realestate": "Nieruchomości", "Custom": "Własny",
    "done": "gotowy", "failed": "błąd", "pending": "oczekuje", "running": "w toku",

    # ── messages produced by server / tasks ──
    "Invalid URL. Must be from olx.pl, otomoto.pl or otodom.pl": "Nieprawidłowy adres. Link musi pochodzić z olx.pl, otomoto.pl lub otodom.pl.",
    "Please provide both custom prompts or choose a preset.": "Podaj oba własne prompty albo wybierz gotowy rodzaj analizy.",
    "Daily limit of free analyses reached. Upgrade to Pro or try again tomorrow.": "Osiągnięto dzienny limit darmowych analiz. Przejdź na Pro albo spróbuj jutro.",
    "Free analyses are temporarily unavailable due to high demand. Please try again later or upgrade to Pro.": "Darmowe analizy są chwilowo niedostępne z powodu dużego obciążenia. Spróbuj później albo przejdź na Pro.",
    "No ads match this search. Your filters are valid — the site just doesn't currently have anything listed that fits them. Try widening your filters (price range, category, etc.).":
        "Brak ogłoszeń pasujących do tego wyszukiwania. Filtry są poprawne — serwis nie ma obecnie ofert, które je spełniają. Spróbuj poszerzyć filtry (zakres cen, kategoria itd.).",
    "No ads found for this search. This is usually a temporary block by the site rather than a real empty result — it's worth trying again.":
        "Nie znaleziono ogłoszeń dla tego wyszukiwania. Zwykle to chwilowa blokada po stronie serwisu, a nie naprawdę pusty wynik — warto spróbować ponownie.",

    # ── e-mails ──
    "Your AdAgent sign-in link": "Twój link do logowania w AdAgent",
    "Click the link below to sign in to AdAgent (valid for {m}):": "Kliknij poniższy link, aby zalogować się do AdAgent (ważny {m}):",
    "If you did not request this, ignore this email.": "Jeśli to nie Ty prosiłeś o ten link, zignoruj tę wiadomość.",
    "Click the button below to sign in. This link expires in {m}.": "Kliknij przycisk poniżej, aby się zalogować. Link wygaśnie za {m}.",
    "Or copy this link:": "Lub skopiuj ten link:",
    "If you did not request this, you can safely ignore this email.": "Jeśli to nie Ty prosiłeś o ten link, możesz spokojnie zignorować tę wiadomość.",
    "{n} minutes": "{n} min",
    "{n} hours": "{n} godz.",
    "Your AdAgent report failed": "Twój raport AdAgent nie powiódł się",
    "Your report couldn't be completed": "Nie udało się ukończyć Twojego raportu",
    "Unfortunately something went wrong while analyzing your ads. You can open the report page for details, or start a new search.":
        "Niestety podczas analizy ogłoszeń coś poszło nie tak. Otwórz stronę raportu, aby zobaczyć szczegóły, albo zacznij nowe wyszukiwanie.",
    "View Details": "Zobacz szczegóły",
    "Your AdAgent report is ready": "Twój raport AdAgent jest gotowy",
    "Your report is ready!": "Twój raport jest gotowy!",
    "The analysis you started has finished. Click below to view the full report.": "Rozpoczęta przez Ciebie analiza została zakończona. Kliknij poniżej, aby zobaczyć cały raport.",
    "View My Report": "Zobacz mój raport",
    "Welcome to AdAgent Pro!": "Witaj w AdAgent Pro!",
    "Thank you for subscribing to AdAgent Pro!": "Dziękujemy za subskrypcję AdAgent Pro!",
    "Click the link below to sign in and access your report history:": "Kliknij poniższy link, aby się zalogować i zobaczyć historię raportów:",
    "Your subscription is now active. You can now analyze all ads in any OLX search.": "Twoja subskrypcja jest aktywna. Możesz teraz analizować wszystkie ogłoszenia w każdym wyszukiwaniu.",
    "Access My Account": "Wejdź na moje konto",

    # ── Stripe Checkout text ──
    "By subscribing you accept the [Terms of Service]({terms}) and the [Privacy Policy]({privacy}), and you ask for the digital service to start immediately, acknowledging that you lose your right of withdrawal once it has begun.":
        "Subskrybując, akceptujesz [Regulamin]({terms}) i [Politykę prywatności]({privacy}) oraz wyrażasz zgodę na natychmiastowe rozpoczęcie świadczenia usługi cyfrowej, przyjmując do wiadomości, że po jej rozpoczęciu tracisz prawo do odstąpienia od umowy.",
}
