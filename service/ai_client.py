from __future__ import annotations
import json
import re
import asyncio
from typing import Optional
import openai
from .config import settings
from .logger import get_logger

logger = get_logger(__name__)

_client: Optional[openai.AsyncOpenAI] = None


def _get_client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


async def _chat(system: str, user: str, model: str, max_tokens: int, retries: int = 2) -> str:
    for attempt in range(retries + 1):
        try:
            r = await _get_client().chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
            )
            return r.choices[0].message.content.strip()
        except openai.RateLimitError:
            if attempt < retries:
                await asyncio.sleep(10 * (attempt + 1))
            else:
                raise
        except openai.OpenAIError as exc:
            logger.error("[ai] OpenAI error: %s", exc)
            raise


def parse_json_safe(text: str, fallback: dict) -> dict:
    """Extract JSON from AI response with multiple fallback strategies."""
    if not text:
        return fallback
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return fallback


def _format_ad(data: dict) -> str:
    """
    Format scraped ad data for the AI prompt.

    Priority order:
      1. Structured fields (title, price, location, parameters, description)
         — extracted by CSS selectors, most reliable.
      2. page_text — full visible page text, captures everything the CSS
         selectors may have missed (e.g. Mileage on Otomoto, language-specific
         field labels).  Provided as a fallback section so the AI can fill gaps.
      3. raw_text / error — legacy fallback or error marker.

    Otomoto ads in particular may have rich data that wasn't picked up by
    structured selectors; page_text lets the AI find it regardless of DOM layout
    or language used on the site.
    """
    structured_keys = ("title", "price", "location", "parameters", "description")
    lines = [f"URL: {data.get('url', '')}"]

    for key in structured_keys:
        if data.get(key):
            lines.append(f"\n{key.capitalize()}:\n{data[key]}")

    # Include full page text when structured extraction was partial or missing.
    # "Partial" = we got fewer than 3 structured fields; always include for
    # otomoto/otodom where CSS coverage is less reliable.
    structured_found = sum(1 for k in structured_keys if data.get(k))
    url = data.get("url", "")
    is_non_olx = "otomoto.pl" in url or "otodom.pl" in url
    page_text = data.get("page_text", "")

    if page_text and (structured_found < 3 or is_non_olx):
        lines.append(f"\nFull page text (use to extract any fields missing above):\n{page_text[:8_000]}")

    if data.get("raw_text") and not page_text:
        lines.append(f"\nRaw text:\n{data['raw_text']}")

    if data.get("error"):
        lines.append(f"\nError:\n{data['error']}")

    return "\n".join(lines)


async def analyze_ad(ad_data: dict, system_prompt: str) -> str:
    """Run per-ad analysis. Returns raw AI response string."""
    user_msg = _format_ad(ad_data)
    return await _chat(
        system=system_prompt,
        user=user_msg,
        model=settings.OPENAI_AD_MODEL,
        max_tokens=settings.OPENAI_AD_MAX_TOKENS,
    )


async def analyze_summary(ad_analyses: list[dict], system_prompt: str) -> str:
    """
    Run summary analysis across all ad results.
    ad_analyses: list of {url, analysis (str)}
    Returns raw AI response string.
    """
    blocks = [
        f"=== Ad #{i+1}  {r['url']} ===\n{r['analysis']}"
        for i, r in enumerate(ad_analyses)
        if not r.get("analysis", "").startswith("AI ERROR")
    ]
    combined = "\n\n".join(blocks) if blocks else "No ads were successfully analyzed."
    return await _chat(
        system=system_prompt,
        user=combined,
        model=settings.OPENAI_SUMMARY_MODEL,
        max_tokens=settings.OPENAI_SUMMARY_MAX_TOKENS,
    )
