"""
Preset registry — single source of truth for analysis presets.

Split out from adagent_server.py so tasks.py can also import it
(adagent_server.py imports tasks.py, so tasks.py can't import back from it
without a circular import).

output:
  "structured" — AI returns strict JSON, parsed by report_builder.py and
                  rendered into the rich report.html.j2 template.
  "raw"        — AI returns free-form text, stored as-is in result_json
                  and rendered via report_custom.html (same path already
                  used for user-typed custom prompts).

templated:
  True  — the prompt file itself contains {{AD_URL}} / {{AD_CONTENT}} /
          {{ADS}} placeholders and is substituted directly into a single
          user message (see ai_client.analyze_ad_templated /
          analyze_summary_templated).
  False — the prompt file is used as-is as the system prompt, and the
          user message is built by ai_client._format_ad() (legacy behavior).
"""

import json
from pathlib import Path

PRESETS_DIR = Path("presets")
PRESETS = {}

if PRESETS_DIR.exists() and PRESETS_DIR.is_dir():
    for file_path in PRESETS_DIR.glob("*.json"):
        preset_key = file_path.stem

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                PRESETS[preset_key] = json.load(f)
        except json.JSONDecodeError as e:
            print(f"Can't read file {file_path.name}: {e}")
else:
    print(f"Folder {PRESETS_DIR} not found. Create it and add .json files (see presets_example folder).")