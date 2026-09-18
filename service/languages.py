"""
Supported UI/report languages + the instruction injected into AI prompts
to make the model answer in the user's chosen language.
"""

# code -> (native display name, English name used inside the AI prompt)
LANGUAGES: dict[str, tuple[str, str]] = {
    "en": ("English",      "English"),
    "pl": ("Polski",       "Polish"),
    "uk": ("Українська",   "Ukrainian"),
    "ru": ("Русский",      "Russian"),
    "de": ("Deutsch",      "German"),
    "fr": ("Français",     "French"),
    "es": ("Español",      "Spanish"),
    "it": ("Italiano",     "Italian"),
    "pt": ("Português",    "Portuguese"),
    "nl": ("Nederlands",   "Dutch"),
    "cs": ("Čeština",      "Czech"),
    "sk": ("Slovenčina",   "Slovak"),
    "ro": ("Română",       "Romanian"),
    "hu": ("Magyar",       "Hungarian"),
    "tr": ("Türkçe",       "Turkish"),
    "sv": ("Svenska",      "Swedish"),
    "da": ("Dansk",        "Danish"),
    "fi": ("Suomi",        "Finnish"),
    "el": ("Ελληνικά",     "Greek"),
    "bg": ("Български",    "Bulgarian"),
    "hr": ("Hrvatski",     "Croatian"),
    "lt": ("Lietuvių",     "Lithuanian"),
    "lv": ("Latviešu",     "Latvian"),
    "et": ("Eesti",        "Estonian"),
    "ar": ("العربية",      "Arabic"),
    "zh": ("中文",          "Chinese"),
    "ja": ("日本語",        "Japanese"),
    "ko": ("한국어",        "Korean"),
    "vi": ("Tiếng Việt",   "Vietnamese"),
    "hi": ("हिन्दी",        "Hindi"),
}

DEFAULT_LANGUAGE = "en"


def language_options() -> list[dict]:
    """For the <select> in index.html."""
    return [
        {"code": code, "label": native if native == english else f"{native} ({english})"}
        for code, (native, english) in LANGUAGES.items()
    ]


def detect_language(accept_language_header: str | None) -> str:
    """
    Parse an Accept-Language header (e.g. 'pl-PL,pl;q=0.9,en-US;q=0.8')
    and return the first supported language code, or the default.
    """
    if not accept_language_header:
        return DEFAULT_LANGUAGE
    for part in accept_language_header.split(","):
        tag = part.split(";")[0].strip().lower()
        primary = tag.split("-")[0]
        if primary in LANGUAGES:
            return primary
    return DEFAULT_LANGUAGE


def build_language_instruction(lang_code: str | None, structured: bool) -> str:
    """
    Appended to the end of an ad/summary prompt (preset or custom).
    Placed last on purpose — models weight the final instruction heavily,
    and this needs to override the presets' own "use the ad's language" line.

    Returns "" for None / unknown / the default language (no override needed —
    keeps the existing "same language as the ad" behavior).
    """
    if not lang_code or lang_code not in LANGUAGES or lang_code == DEFAULT_LANGUAGE:
        return ""

    _, english_name = LANGUAGES[lang_code]

    if structured:
        return (
            f"\n\n---\nIMPORTANT — response language override (this takes priority "
            f"over any earlier language instruction above):\n"
            f"Write all free-text / prose values in {english_name}. Do NOT translate "
            f"JSON keys. Do NOT translate any field whose allowed values are given "
            f"above as a fixed set in quotes (e.g. \"worth_viewing\"|\"maybe\"|\"skip\", "
            f"or \"low\"|\"fair\"|\"high\"|\"unknown\") — keep those exact English enum "
            f"strings unchanged. Everything else (verdict_note, summary, deep_dive "
            f"text, red_flags, positives, market_summary, recommendation, "
            f"recommended_ads.label, etc.) must be written in {english_name}."
        )

    return (
        f"\n\n---\nIMPORTANT — response language override (this takes priority "
        f"over any earlier language instruction above): write your entire "
        f"response in {english_name}."
    )
