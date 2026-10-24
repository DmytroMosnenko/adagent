"""Report-language override: English must be enforced too, and the instruction
must reach the model both in the system prompt and at the end of the user message."""
from unittest.mock import AsyncMock, patch

import pytest

from service import ai_client
from service.languages import (
    build_language_instruction, build_language_reminder, strip_language_conflicts,
)

AD_PROMPT = "Intro.\n- Return ONLY the JSON.\n- Use the same language as the advertisement.\n"
SUMMARY_PROMPT = "Intro.\nUse the same language as the individual analyses.\nWrite verdicts in plain words.\n"


@pytest.mark.parametrize("structured", [True, False])
def test_english_now_gets_override(structured):
    assert "English" in build_language_instruction("en", structured)
    assert "English" in build_language_reminder("en", structured)


@pytest.mark.parametrize("code", [None, "", "xx"])
def test_none_or_unknown_keeps_legacy_behaviour(code):
    assert build_language_instruction(code, True) == ""
    assert build_language_reminder(code, True) == ""
    assert strip_language_conflicts(AD_PROMPT, code) == AD_PROMPT


def test_structured_override_covers_copied_lists_and_keeps_enums():
    text = build_language_instruction("de", True)
    assert "German" in text and "mentioned_fixes" in text
    assert "worth_viewing" in text and "Do NOT translate JSON keys" in text


def test_strip_removes_same_language_lines_only():
    out = strip_language_conflicts(AD_PROMPT, "en")
    assert "same language" not in out.lower()
    assert "Return ONLY the JSON." in out
    out = strip_language_conflicts(SUMMARY_PROMPT, "uk")
    assert "same language" not in out.lower()
    assert "Write verdicts in plain words." in out


@pytest.mark.asyncio
async def test_reminder_is_last_thing_in_user_message():
    reminder = build_language_reminder("en", True)
    with patch("service.ai_client._chat", new=AsyncMock(return_value="{}")) as chat, \
         patch("service.ai_client.ai_gate.run", new=lambda owner, fn: fn()):
        await ai_client.analyze_ad({"url": "https://www.olx.pl/d/oferta/x", "title": "Samochód"},
                                   "sys", "rid", reminder)
        assert chat.call_args.kwargs["user"].endswith(reminder)
        await ai_client.analyze_summary([{"url": "u", "analysis": "{}"}], "sys", "rid", reminder)
        assert chat.call_args.kwargs["user"].endswith(reminder)
        await ai_client.analyze_ad_templated({"url": "u", "title": "t"}, "{{AD_CONTENT}}", "rid", reminder)
        assert chat.call_args.kwargs["user"].endswith(reminder)
        await ai_client.analyze_summary_templated([{"url": "u", "analysis": "{}"}], "{{ADS}}", "rid", reminder)
        assert chat.call_args.kwargs["user"].endswith(reminder)
