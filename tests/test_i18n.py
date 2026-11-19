"""Static checks for the frontend dictionaries (spec 6.9)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app import engine, security

STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"
I18N_FILE = STATIC_DIR / "i18n.js"
ENTRY = re.compile(r'"([^"]+)"\s*:\s*"((?:[^"\\]|\\.)*)"')

API_ERRORS = (
    engine.GAME_OVER,
    engine.START_NEXT_ROUND,
    engine.ROUND_RUNNING,
    engine.NOT_YOUR_TURN,
    engine.CHOOSE_TARGET,
    engine.NOTHING_TO_TARGET,
    engine.NOT_YOUR_DECISION,
    engine.INVALID_TARGET,
    engine.NO_CARDS_LEFT,
    engine.TOO_MANY_AUTOMATIC,
    engine.BAD_PLAYER_COUNT,
    security.TOKEN_REQUIRED,
    security.GAME_NOT_FOUND,
    security.STALE_WRITE,
    security.BAD_NAMES,
    "invalid request",  # main.py RequestValidationError fallback
)


def dictionary(name: str) -> dict[str, str]:
    source = I18N_FILE.read_text(encoding="utf-8")
    match = re.search(rf"const {name} = Object\.freeze\(\{{(.*?)\n  \}}\);", source, re.DOTALL)
    assert match is not None, f"{name} dictionary not found"
    body = match.group(1)
    entries = ENTRY.findall(body)
    assert len(entries) == len({key for key, _ in entries}), f"duplicate key in {name}"
    return {key: json.loads(f'"{value}"') for key, value in entries}


def test_dictionaries_have_identical_key_sets() -> None:
    en, de = dictionary("EN"), dictionary("DE")
    assert set(en) == set(de)
    assert en and de


def test_no_empty_translations() -> None:
    for name in ("EN", "DE"):
        for key, value in dictionary(name).items():
            assert value.strip(), f"{name}.{key} is empty"


def test_every_api_error_message_is_translated() -> None:
    english = set(dictionary("EN").values())
    for message in API_ERRORS:
        assert message in english, f"missing translation for: {message}"


def test_all_referenced_keys_exist() -> None:
    english = dictionary("EN")
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html_keys = re.findall(r'data-i18n(?:-aria|-placeholder|-title)?="([^"]+)"', html)
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    script_keys = re.findall(r'\bt\("([A-Za-z0-9_.]+)"[,)]', script)
    for key in html_keys + script_keys:
        assert key in english, f"i18n key used but missing: {key}"
