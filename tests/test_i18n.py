"""Static checks for the frontend dictionaries (spec 6.9)."""

from __future__ import annotations

import ast
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


def error_strings_from_source() -> set[str]:
    """Extract stable API error texts statically (spec 6.9 / phase 5).

    Module-level UPPERCASE string constants in the engine and security modules
    are the typed error messages; ``detail=`` string literals in main.py are
    the HTTP-layer fallbacks. No import of the values themselves: a changed
    message must fail this test until both dictionaries are updated.
    """
    found: set[str] = set()
    for filename in ("engine.py", "security.py", "main.py"):
        tree = ast.parse((STATIC_DIR.parent / filename).read_text(encoding="utf-8"))
        if filename == "main.py":
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    value = keyword.value
                    if (
                        keyword.arg == "detail"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                    ):
                        found.add(value.value)
        else:
            for node in tree.body:
                if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
                    continue
                if not isinstance(node.value.value, str):
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        found.add(node.value.value)
    return found


def test_every_api_error_message_is_translated() -> None:
    english = set(dictionary("EN").values())
    for message in API_ERRORS:
        assert message in english, f"missing translation for: {message}"


def test_source_discovered_error_strings_are_translated_in_both_languages() -> None:
    discovered = error_strings_from_source()
    assert len(discovered) >= len(API_ERRORS) - 1  # guard against an empty/static scan
    en, de = dictionary("EN"), dictionary("DE")
    for message in sorted(discovered):
        keys = [key for key, value in en.items() if value == message]
        assert keys, f"EN has no dictionary entry for API error: {message}"
        for key in keys:
            assert key in de, f"DE missing key {key} for API error: {message}"
            assert de[key] != message, f"DE key {key} was not translated: {message}"


def test_all_referenced_keys_exist() -> None:
    english = dictionary("EN")
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html_keys = re.findall(r'data-i18n(?:-aria|-placeholder|-title)?="([^"]+)"', html)
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    script_keys = re.findall(r'\bt\("([A-Za-z0-9_.]+)"[,)]', script)
    for key in html_keys + script_keys:
        assert key in english, f"i18n key used but missing: {key}"
