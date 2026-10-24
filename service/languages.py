"""
Supported UI/report languages + the instruction injected into AI prompts
to make the model answer in the user's chosen language.
"""

import re

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


_SAME_LANG_LINE_RE = re.compile(r"^[^\n]*same language as[^\n]*\n?", re.IGNORECASE | re.MULTILINE)


def strip_language_conflicts(prompt: str, lang_code: str | None) -> str:
    """
    Remove the presets' own "Use the same language as the advertisement" lines
    when an explicit report language is chosen. The override appended at the end
    already says it wins, but removing the contradiction is more reliable.
    No-op for None / unknown codes (legacy "ad's own language" behavior).
    """
    if not lang_code or lang_code not in LANGUAGES:
        return prompt
    return _SAME_LANG_LINE_RE.sub("", prompt).rstrip()


def build_language_instruction(lang_code: str | None, structured: bool) -> str:
    """
    Appended to the end of an ad/summary prompt (preset or custom).
    Placed last on purpose — models weight the final instruction heavily,
    and this needs to override the presets' own "use the ad's language" line.

    Applies to EVERY supported language, English included: ads are mostly
    Polish, so "no override" for English meant English reports came out Polish.
    Returns "" only for None / unknown codes (legacy "same language as the ad").
    """
    if not lang_code or lang_code not in LANGUAGES:
        return ""

    _, english_name = LANGUAGES[lang_code]

    if structured:
        return (
            f"\n\n---\nIMPORTANT — response language override (this takes priority "
            f"over any earlier language instruction above):\n"
            f"Write all free-text / prose values in {english_name}, even though the "
            f"advertisement itself is usually in Polish. This includes items you take "
            f"from the ad's text: translate every entry of mentioned_fixes, red_flags "
            f"and positives, and text values such as color, heating, condition, "
            f"district descriptions and monthly_fees wording, into {english_name}. "
            f"Keep numbers, currency amounts, proper nouns (brand, model, street, "
            f"city names) and URLs as they are. Do NOT translate JSON keys. Do NOT "
            f"translate any field whose allowed values are given above as a fixed "
            f"set in quotes (e.g. \"worth_viewing\"|\"maybe\"|\"skip\", or "
            f"\"low\"|\"fair\"|\"high\"|\"unknown\") — keep those exact English enum "
            f"strings unchanged. Everything else (verdict_note, summary, deep_dive "
            f"text, red_flags, positives, market_summary, recommendation, "
            f"recommended_ads.label, etc.) must be written in {english_name}."
        )

    return (
        f"\n\n---\nIMPORTANT — response language override (this takes priority "
        f"over any earlier language instruction above): write your entire "
        f"response in {english_name}, even if the advertisement text is in another "
        f"language (translate anything you quote or take from it)."
    )


def build_language_reminder(lang_code: str | None, structured: bool) -> str:
    """
    Short reminder appended to the END OF THE USER MESSAGE (after the ad text).
    The system-prompt override alone loses to a long Polish ad body in the user
    turn; the last thing the model reads should be the language requirement.
    """
    if not lang_code or lang_code not in LANGUAGES:
        return ""
    _, english_name = LANGUAGES[lang_code]
    extra = (
        " Keep JSON keys and fixed enum values unchanged; translate every other "
        "text value, including list items copied from the ad."
        if structured else ""
    )
    return (
        f"\n\n---\nREMINDER: the text above may be in Polish. Write your entire "
        f"answer in {english_name}.{extra}"
    )
