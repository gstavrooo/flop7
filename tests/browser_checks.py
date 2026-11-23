"""Browser regression suite for spec section 8 (no framework, no screenshots/traces).

Launches uvicorn on a free port against a temporary SQLite database, then drives
Chromium with the plain Playwright API. Every scenario from the spec's
``browser-test`` row is a named ``test_*`` function below. Run with::

    python tests/browser_checks.py
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright
from sqlalchemy import create_engine, select, update

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import games

DESKTOP = {"width": 1280, "height": 900}
MOBILE = {"width": 390, "height": 844}

BLOCK_STORAGE = """
Object.defineProperty(window, 'localStorage', {
  configurable: true,
  get() { throw new Error('storage blocked'); }
});
"""

LAST_ROUND = [
    {"seat": 0, "points": 14, "status": "stayed", "flop7": False},
    {"seat": 1, "points": 9, "status": "bust", "flop7": False},
]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Runner:
    """Small harness shared by every scenario: HTTP, database and browser helpers."""

    def __init__(self, base_url: str, database_url: str, browser: Any) -> None:
        self.base_url = base_url.rstrip("/")
        self.database_url = database_url
        self.browser = browser

    # -- HTTP -----------------------------------------------------------------
    def api(self, method: str, path: str, body: dict | None = None, token: str | None = None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        if token is not None:
            headers["X-Seat-Token"] = token
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    def create(self, names=("Ann", "Bob"), *, bot: bool = False) -> dict:
        status, view = self.api("POST", "/api/games", {"players": list(names), "bot": bot})
        assert status == 201, f"create failed: {status} {view}"
        return view

    def get(self, game_id: str) -> dict:
        status, view = self.api("GET", f"/api/games/{game_id}")
        assert status == 200, f"get failed: {status} {view}"
        return view

    # -- database (deterministic scenarios) -----------------------------------
    def load(self, game_id: str) -> dict:
        engine = create_engine(self.database_url)
        try:
            with engine.connect() as connection:
                row = connection.execute(select(games.c.state).where(games.c.id == game_id)).one()
        finally:
            engine.dispose()
        return json.loads(row.state)

    def store(self, game_id: str, state: dict) -> None:
        """Write a prepared state and bump the version so the browser catches up."""
        engine = create_engine(self.database_url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    update(games)
                    .where(games.c.id == game_id)
                    .values(
                        state=json.dumps(state),
                        winner=state.get("winner"),
                        version=games.c.version + 1,
                    )
                )
        finally:
            engine.dispose()

    # -- browser --------------------------------------------------------------
    @contextmanager
    def page(
        self,
        viewport: dict = DESKTOP,
        *,
        init_script: str | None = None,
    ) -> Iterator[Page]:
        context = self.browser.new_context(viewport=viewport, locale="en-US")
        errors: list[str] = []
        if init_script:
            context.add_init_script(script=init_script)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            yield page
            assert not errors, f"browser page errors: {errors}"
        finally:
            context.close()

    def clean(self, game_id: str) -> dict:
        """Strip the official opening deal so a scenario controls every card."""
        state = self.load(game_id)
        state["pending"] = []
        state.pop("deal_index", None)
        for player in state["players"]:
            player.update(
                numbers=[],
                mods=[],
                second=False,
                status="active",
                round_score=0,
                round_flop7=False,
            )
        return state

    @staticmethod
    def ready(page: Page) -> None:
        """Wait until the app has booted and rendered (not just static HTML)."""
        page.wait_for_function(
            "() => window.Flop7I18n && document.querySelector('#setup-title').textContent.trim().length > 0"
        )

    def join(self, page: Page, view: dict, seat: int = 0) -> None:
        token = view["seats"][seat]["token"]
        fragment = urllib.parse.urlencode({"seat": seat, "token": token})
        page.goto(f"{self.base_url}/?game={view['id']}#{fragment}")
        self.ready(page)


# ---------------------------------------------------------------------------
# Scenarios — one per browser-test bullet from spec section 8.
# ---------------------------------------------------------------------------


def test_hot_seat_hit_stay_and_target(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["deck"] = [7]  # a single, known number card
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector('[data-action="hit"]')
        assert page.locator("#dock-deck").inner_text() == "Deck: 1"
        page.locator('[data-action="hit"]').click()
        page.wait_for_function("() => document.querySelector('#dock-deck').textContent === 'Deck: 0'")
        assert page.locator(".player-card").first.locator(".card").count() == 1
        # One card per turn: the turn marker moves to Bob and the human waits.
        page.wait_for_function(
            "() => document.querySelectorAll('.player-card')[1].classList.contains('is-turn')"
        )
        assert page.locator('[data-action="hit"]').count() == 0

    # Staying is an alternative to the single hit, on a fresh turn.
    stay_view = r.create(("Ann", "Bob"))
    state = r.clean(stay_view["id"])
    state["players"][0]["numbers"] = [4]
    r.store(stay_view["id"], state)
    with r.page() as page:
        r.join(page, stay_view, 0)
        page.wait_for_selector('[data-action="stay"]')
        page.locator('[data-action="stay"]').click()
        page.wait_for_selector(".player-card:nth-child(1) .status-badge.status-stayed")

    # A pending freeze is a human decision for the drawer.
    pending_view = r.create(("Ann", "Bob"))
    state = r.clean(pending_view["id"])
    state["pending"] = [{"kind": "freeze", "by": 0, "target": None, "source": "hit"}]
    state["discard"].append("freeze")
    r.store(pending_view["id"], state)
    with r.page() as page:
        r.join(page, pending_view, 0)
        page.wait_for_selector('[data-action="target"]')
        assert "freeze" in page.locator("#dock-prompt").inner_text().lower()
        page.locator('[data-action="target"]').nth(1).click()  # seat 1 (self is now a valid target)
        page.wait_for_selector(".player-card:nth-child(2) .status-badge.status-stayed")


def test_space_next_round_also_with_focused_summary(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["round_over"] = True
    state["last_round"] = LAST_ROUND
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector("#round-summary:not([hidden])")
        assert page.locator('[data-action="next"]').count() == 1
        summary = page.locator("#round-summary")
        summary.evaluate("el => el.setAttribute('tabindex', '-1')")
        summary.focus()
        page.keyboard.press("Space")
        page.wait_for_function("() => document.querySelector('#dock-round').textContent.includes('Round 2')")
        assert page.locator("#round-summary").is_hidden()


def test_winner_banner_scales_with_margin(r: Runner) -> None:
    for gap, scale in ((5, "scale-1"), (20, "scale-2"), (40, "scale-3")):
        view = r.create(("Ann", "Bob"))
        state = r.clean(view["id"])
        state["winner"] = 0
        state["players"][0]["total"] = 200 + gap
        state["players"][1]["total"] = 200
        r.store(view["id"], state)
        with r.page() as page:
            r.join(page, view, 0)
            page.wait_for_selector("#winner-banner:not([hidden])")
            assert scale in page.locator("#winner-banner").get_attribute("class")
            assert "Ann wins" in page.locator("#winner-title").inner_text()
            assert str(gap) in page.locator("#winner-margin").inner_text()


def test_rematch_keeps_names_bot_and_seat(r: Runner) -> None:
    view = r.create(("Ann", "Bob"), bot=True)
    state = r.clean(view["id"])
    state["bot_due_at"] = time.time() + 1000
    state["winner"] = 0
    state["players"][0]["total"] = 210
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector("#winner-banner:not([hidden])")
        page.locator('[data-action="rematch"]').click()
        page.wait_for_function(
            "old => new URL(location.href).searchParams.get('game') !== old", arg=view["id"]
        )
        new_id = page.evaluate("() => new URL(location.href).searchParams.get('game')")
        page.wait_for_selector("#invite-dialog[open]")  # new links after the deal, not a misleading toast
        saved = page.evaluate("id => JSON.parse(localStorage.getItem('flop7:v1:game:' + id))", new_id)
        assert saved and isinstance(saved["value"]["tokens"]["0"], str)

    # The rematch keeps exactly the same names and bot, and the seat still works.
    r.store(new_id, r.clean(new_id))  # deterministic: no random deal-time pending
    state = r.load(new_id)
    state["bot_due_at"] = time.time() + 1000
    r.store(new_id, state)
    new_view = r.get(new_id)
    assert [p["name"] for p in new_view["players"]] == ["Ann", "Bob", "Bot"]
    assert new_view["players"][2]["bot"] is True
    status, _ = r.api(
        "POST",
        f"/api/games/{new_id}/hit",
        {"seat": 0, "version": new_view["version"]},
        token=saved["value"]["tokens"]["0"],
    )
    assert status == 200, f"seat token no longer valid after rematch: {status}"


def test_colour_coding_signals_status(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["players"][0]["status"] = "bust"
    state["players"][0]["round_score"] = 0
    state["players"][1]["status"] = "stayed"
    state["players"][1]["round_score"] = 12
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector(".status-badge.status-bust")
        page.wait_for_selector(".status-badge.status-stayed")
        assert page.locator(".status-badge.status-bust").inner_text()
        assert page.locator(".status-badge.status-stayed").inner_text()
        bust = page.locator(".status-badge.status-bust").evaluate(
            "el => getComputedStyle(el).backgroundColor"
        )
        stayed = page.locator(".status-badge.status-stayed").evaluate(
            "el => getComputedStyle(el).backgroundColor"
        )
        assert bust != stayed, "status colours must differ"


def test_deck_counter_updates(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["deck"] = [5, 6]
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector('[data-action="hit"]')
        assert "2" in page.locator("#dock-deck").inner_text()
        page.locator('[data-action="hit"]').click()
        page.wait_for_function("() => document.querySelector('#dock-deck').textContent.includes('1')")


def test_tab_title_signals_your_turn(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    r.store(view["id"], r.clean(view["id"]))  # deterministic: no deal-time pending
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector('[data-action="hit"]')
        assert page.title() == "▶ Flop7"
        state = r.clean(view["id"])
        state["turn"] = 1  # only seat 0 has a token on this device
        r.store(view["id"], state)
        page.wait_for_function("() => document.title === 'Flop7'")


def test_solo_bot_thinking_and_explicit_next(r: Runner) -> None:
    view = r.create(("Human",), bot=True)
    with r.page() as page:
        r.join(page, view, 0)
        state = r.clean(view["id"])
        state["turn"] = 1
        state["bot_due_at"] = time.time() + 1000  # hold the bot in "thinking"
        r.store(view["id"], state)
        page.reload()
        page.wait_for_selector(".status-badge.status-thinking")
        assert "thinking" in page.locator("#dock-prompt").inner_text().lower()

        # Make the bot due and reload: the server commits exactly one decision.
        before = r.get(view["id"])["version"]
        state = r.clean(view["id"])
        state["bot_due_at"] = time.time() - 1
        r.store(view["id"], state)
        page.reload()
        deadline = time.time() + 10
        while time.time() < deadline and r.get(view["id"])["version"] <= before:
            time.sleep(0.2)
        assert r.get(view["id"])["version"] > before, "bot did not commit a decision"

        # Round break keeps an explicit Next round button (no auto-advance).
        state = r.clean(view["id"])
        state["round_over"] = True
        state["last_round"] = LAST_ROUND
        r.store(view["id"], state)
        page.reload()
        page.wait_for_selector('[data-action="next"]')
        page.locator('[data-action="next"]').click()
        page.wait_for_function("() => document.querySelector('#dock-round').textContent.includes('Round 2')")


def test_bot_chip_inside_player_box(r: Runner) -> None:
    with r.page() as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        page.wait_for_selector("#bot-chip", state="attached")
        assert page.locator("#bot-chip").is_hidden()
        page.locator("#add-bot").check()
        assert page.locator("#bot-chip").is_visible()
        assert page.evaluate("() => !!document.querySelector('.player-box .box-inner #bot-chip')")
        assert page.locator("#bot-chip").inner_text().strip() == "🤖 Bot"


def test_participant_preview_tracks_names_bot_and_language(r: Runner) -> None:
    with r.page() as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        preview = page.locator("#participant-preview")
        page.locator("#names").fill("Ann, Bob")
        assert "Ann · Bob" in preview.inner_text() and "2 players" in preview.inner_text()
        page.locator("#add-bot").check()
        assert "🤖 Bot" in preview.inner_text() and "3 players" in preview.inner_text()
        page.locator("#btn-language").click()  # -> German
        assert page.evaluate("() => document.documentElement.lang") == "de"
        assert "Spieler" in preview.inner_text() and "🤖 Bot" in preview.inner_text()
        page.locator("#add-bot").uncheck()
        page.locator("#names").fill("Ann")
        assert "1 Spieler" in preview.inner_text()
        page.locator("#btn-language").click()  # -> English
        assert "1 player" in preview.inner_text()


def test_card_explanation_and_progress(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["deck"] = [5]
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector('[data-action="hit"]')
        page.locator('[data-action="hit"]').click()
        page.wait_for_selector(".player-card .card")
        assert page.locator(".progress-step.filled").count() == 1
        page.locator(".player-card .card").first.click()
        page.wait_for_selector("#card-dialog[open]")
        assert "adds its value" in page.locator("#card-explanation").inner_text()
        page.locator("#card-dialog .dialog-close").click()
        assert page.locator("#card-dialog").is_hidden()


def test_language_theme_and_rules_round_trip(r: Runner) -> None:
    with r.page() as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        assert page.evaluate("() => document.documentElement.lang") == "en"
        page.locator("#btn-language").click()
        assert page.evaluate("() => document.documentElement.lang") == "de"
        assert page.locator("#setup-title").inner_text() == "Neues Spiel"
        page.locator("#btn-language").click()
        assert page.evaluate("() => document.documentElement.lang") == "en"
        assert page.locator("#setup-title").inner_text() == "New game"

        # Only two modes now: the toggle alternates light and dark forever.
        page.evaluate("() => Flop7Theme.set('light')")
        assert page.evaluate("() => document.documentElement.dataset.theme") == "light"
        page.locator("#btn-theme").click()  # light -> dark
        assert page.evaluate("() => document.documentElement.dataset.theme") == "dark"
        page.locator("#btn-theme").click()  # dark -> light
        assert page.evaluate("() => document.documentElement.dataset.theme") == "light"
        assert page.locator("#btn-theme .btn-icon").inner_text().strip() == "☀️"

        page.keyboard.press("?")
        page.wait_for_selector("#rules-dialog[open]")
        page.locator("#rules-dialog .dialog-close").click()
        assert page.locator("#rules-dialog").is_hidden()


def test_legacy_auto_theme_migrates_to_an_explicit_mode(r: Runner) -> None:
    """The removed third mode must not survive a reload as 'auto' (spec 6.8)."""
    with r.page() as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        page.evaluate("() => localStorage.setItem('flop7:v1:theme', JSON.stringify({value: 'auto'}))")
        page.reload()
        r.ready(page)
        mode = page.evaluate("() => Flop7Theme.get()")
        assert mode in ("light", "dark"), mode
        assert page.evaluate("() => document.documentElement.dataset.theme") == mode
        stored = page.evaluate("() => JSON.parse(localStorage.getItem('flop7:v1:theme')).value")
        assert stored == mode, stored
        # Invalid values degrade to light instead of an unthemed third state.
        page.evaluate("() => Flop7Theme.set('auto')")
        assert page.evaluate("() => Flop7Theme.get()") == "light"
        assert page.evaluate("() => Flop7Theme.get()") != "auto"


def test_rules_are_grouped_and_translated(r: Runner) -> None:
    with r.page() as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        page.keyboard.press("?")
        page.wait_for_selector("#rules-dialog[open]")
        assert page.locator("#rules-dialog .dialog-body").count() == 0
        items = page.locator("#rules-dialog .rules-list li")
        assert items.count() >= 7
        headings = page.locator("#rules-dialog .rules-list strong").all_inner_texts()
        assert all(heading.strip() for heading in headings)
        assert "your turn" in [heading.lower() for heading in headings], headings
        page.evaluate("() => Flop7I18n.setLanguage('de')")
        german = page.locator("#rules-dialog .rules-list strong").all_inner_texts()
        assert "dein zug" in [heading.lower() for heading in german]
        page.evaluate("() => Flop7I18n.setLanguage('en')")


def test_dark_mode_neutral_history(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["round_over"] = True
    state["last_round"] = LAST_ROUND
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector("#round-summary:not([hidden])")
        summary = page.locator("#round-summary")

        def colours() -> list[str]:
            return summary.evaluate(
                "el => [getComputedStyle(el).color, getComputedStyle(el).backgroundColor]"
            )

        page.evaluate("() => Flop7Theme.set('light')")
        light = colours()
        page.evaluate("() => Flop7Theme.set('dark')")
        dark = colours()
        assert light == dark, "round history must stay theme-neutral"
        assert light[0] == "rgb(17, 17, 17)" and light[1] == "rgb(142, 230, 240)"


def test_two_devices_live_updates(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["deck"] = [5, 6]  # a known number card, no random action/pending
    r.store(view["id"], state)
    with r.page() as first, r.page(MOBILE) as second:
        r.join(first, view, 0)
        r.join(second, view, 1)
        first.wait_for_selector('[data-action="hit"]')
        second.wait_for_selector("#dock-deck")
        before = second.locator("#dock-deck").inner_text()
        assert second.locator('[data-action="hit"]').count() == 0  # foreign seat

        first.locator('[data-action="hit"]').click()
        second.wait_for_function(
            "before => document.querySelector('#dock-deck').textContent !== before",
            arg=before,
            timeout=15000,
        )
        assert second.locator(".player-card").first.locator(".card").count() >= 1


def test_dock_touch_targets_on_mobile(r: Runner) -> None:
    with r.page(MOBILE) as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        start = page.locator("#btn-start").bounding_box()
        assert start and start["height"] >= 44
        view = r.create(("Ann", "Bob"))
        r.store(view["id"], r.clean(view["id"]))  # deterministic: no random deal-time action
        r.join(page, view, 0)
        page.wait_for_selector('[data-action="hit"]')
        for selector in ('[data-action="hit"]', '[data-action="stay"]'):
            box = page.locator(selector).bounding_box()
            assert box and box["height"] >= 44, f"{selector} touch target too small"
        dock = page.locator("#dock").bounding_box()
        assert dock and 0 <= dock["y"] and dock["y"] + dock["height"] <= MOBILE["height"] + 1


def test_waiting_bar_constant_height(r: Runner) -> None:
    with r.page(MOBILE) as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        heights = [page.locator(".waiting-bar").bounding_box()["height"]]

        view = r.create(("Ann", "Bob"))
        r.store(view["id"], r.clean(view["id"]))
        r.join(page, view, 0)
        page.wait_for_selector('[data-action="hit"]')
        heights.append(page.locator(".waiting-bar").bounding_box()["height"])

        state = r.clean(view["id"])
        state["turn"] = 1
        r.store(view["id"], state)
        page.wait_for_function("() => !document.querySelector('#dock .btn-hit')")
        heights.append(page.locator(".waiting-bar").bounding_box()["height"])

        state = r.clean(view["id"])
        state["round_over"] = True
        state["last_round"] = LAST_ROUND
        r.store(view["id"], state)
        page.wait_for_selector('[data-action="next"]')
        heights.append(page.locator(".waiting-bar").bounding_box()["height"])

        assert page.locator(".waiting-bar").is_visible()
        assert len(set(heights)) == 1 and heights[0] > 0, f"waiting bar jumped: {heights}"


def test_accessibility_basics(r: Runner) -> None:
    with r.page(MOBILE) as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        unnamed = page.evaluate(
            """() => [...document.querySelectorAll('button')]
                 .filter(b => !(b.getAttribute('aria-label') || b.textContent.trim() || b.title))
                 .map(b => b.outerHTML)"""
        )
        assert not unnamed, f"buttons without accessible names: {unnamed}"
        assert page.evaluate("() => [...document.querySelectorAll('img')].every(i => i.hasAttribute('alt'))")
        assert page.evaluate(
            "() => [...document.querySelectorAll('dialog')].every(d => d.tagName === 'DIALOG')"
        )
        assert page.locator("#dock-prompt").is_visible()
        assert page.locator(".waiting-bar").is_visible()


def test_blocked_and_corrupt_storage_degrade_gracefully(r: Runner) -> None:
    with r.page(init_script=BLOCK_STORAGE) as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        page.wait_for_selector("#storage-hint:not([hidden])")
        page.locator("#names").fill("Ann, Bob")
        page.locator("#btn-start").click()
        page.wait_for_selector("#table-screen:not([hidden])")
        assert page.locator(".player-card").count() == 2

    with r.page() as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        page.evaluate("() => localStorage.setItem('flop7:v1:theme', '{not json')")
        page.evaluate("() => localStorage.setItem('flop7:v1:game:broken', 'not-an-object')")
        page.reload()
        page.wait_for_selector("#setup-screen:not([hidden])")
        page.locator("#names").fill("Ann, Bob")
        page.locator("#btn-start").click()
        page.wait_for_selector("#table-screen:not([hidden])")


def test_malformed_invites_are_ignored(r: Runner) -> None:
    for fragment in ("#seat=abc&token=short", "#seat=0&token=short"):
        with r.page() as page:
            page.goto(r.base_url + "/?game=does-not-exist" + fragment)
            r.ready(page)
            page.wait_for_selector("#setup-screen:not([hidden])")
            assert page.evaluate("() => location.hash") == "", "fragment was not stripped"
            assert page.locator("#toast").is_visible(), "no hint for an unknown game"


def test_legacy_texts_keep_rendering(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["events"] = [
        {"id": 1, "type": "draws", "seat": 0, "card": 5, "text": "legacy draw text"},
        {"id": 2, "type": "old-style-log", "text": "older plain-text history entry"},
    ]
    state["next_event_id"] = 3
    state["players"][0]["numbers"] = [5]
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector(".player-card")
        assert page.locator(".player-card").count() == 2
        assert page.locator(".player-card .card").count() == 1


def test_connection_lost_chip_after_delay(r: Runner) -> None:
    """AC-14: the chip waits ~4 s; normal streams never warn (spec 6.6)."""
    view = r.create(("Ann", "Bob"))
    with r.page() as page:
        page.route("**/events*", lambda route: route.abort())  # force reconnect errors
        r.join(page, view, 0)
        chip = page.locator("#connection-chip")
        assert chip.is_hidden()
        page.wait_for_timeout(1500)
        assert chip.is_hidden(), "chip must not appear inside the 4 s grace period"
        page.wait_for_selector("#connection-chip:not([hidden])", timeout=8000)
        assert "connection" in chip.inner_text().lower()


def test_cold_start_wake_banner_on_delayed_create(r: Runner) -> None:
    """A sleeping host shows a translated wake hint while the first request is stalled."""
    with r.page() as page:

        def slow_create(route: Any) -> None:
            time.sleep(6)  # simulate the ~1 min cold start without waiting a minute
            route.continue_()

        page.route("**/api/games", slow_create)
        page.goto(r.base_url + "/")
        r.ready(page)
        page.evaluate(
            """() => {
              window.__wake = { shown: 0, hidden: 0, text: '' };
              const el = document.getElementById('wake-banner');
              new MutationObserver(() => {
                if (!el.hidden && !window.__wake.shown) {
                  window.__wake.shown = performance.now();
                  window.__wake.text = el.textContent;
                }
                if (el.hidden && window.__wake.shown && !window.__wake.hidden) {
                  window.__wake.hidden = performance.now();
                }
              }).observe(el, { attributes: true, attributeFilter: ['hidden'] });
            }"""
        )
        page.locator("#names").fill("Ann, Bob")
        page.evaluate("() => { window.__start = performance.now(); }")
        page.locator("#btn-start").click()
        page.wait_for_selector("#table-screen:not([hidden])", timeout=20000)
        wake = page.evaluate("() => Object.assign({}, window.__wake, { start: window.__start })")
        assert wake["shown"] > 0, "no wake banner during the stalled create request"
        assert 4000 <= wake["shown"] - wake["start"] <= 9000, "wake banner timing is wrong"
        assert "Waking up" in wake["text"], f"unexpected wake text: {wake['text']!r}"
        assert wake["hidden"] > wake["shown"], "wake banner was never cleared"


def test_failed_create_offers_translated_retry(r: Runner) -> None:
    """A failed POST never auto-retries but offers an explicit, translated retry action."""
    with r.page() as page:
        calls = {"count": 0}

        def flaky_create(route: Any) -> None:
            calls["count"] += 1
            if calls["count"] == 1:
                route.abort()
            else:
                route.continue_()

        page.route("**/api/games", flaky_create)
        page.goto(r.base_url + "/")
        r.ready(page)
        page.locator("#names").fill("Ann, Bob")
        page.locator("#btn-start").click()
        page.wait_for_selector("#toast:not([hidden])", timeout=10000)
        retry = page.locator("#toast-retry")
        assert retry.is_visible(), "no retry action after a failed create"
        assert retry.inner_text() == "Try again"
        assert calls["count"] == 1, "a POST must not be auto-retried"
        retry.click()
        page.wait_for_selector("#table-screen:not([hidden])", timeout=10000)
        assert calls["count"] == 2


def test_safe_get_retries_after_network_error(r: Runner) -> None:
    """GETs recover from a transient cold-start error without showing an error toast."""
    view = r.create(("Ann", "Bob"))
    with r.page() as page:
        calls = {"count": 0}

        def flaky_state(route: Any) -> None:
            calls["count"] += 1
            if calls["count"] == 1:
                route.abort()
            else:
                route.continue_()

        page.route(f"**/api/games/{view['id']}", flaky_state)
        r.join(page, view, 0)
        page.wait_for_selector("#table-screen:not([hidden])", timeout=10000)
        assert calls["count"] == 2, "the safe GET was not retried once"
        assert page.locator("#toast").is_hidden()


def test_stale_move_toast_and_reload(r: Runner) -> None:
    """AC-21: a 409 shows a translated toast and reloads state, never a double draw."""
    view = r.create(("Ann", "Bob"))
    r.store(view["id"], r.clean(view["id"]))  # deterministic: no random deal-time pending
    with r.page() as page:
        page.route("**/events*", lambda route: route.abort())  # isolate from SSE catch-up
        r.join(page, view, 0)
        page.wait_for_selector('[data-action="hit"]')
        state = r.clean(view["id"])
        state["deck"] = [3, 4]  # another device writes first: version +1
        r.store(view["id"], state)
        page.locator('[data-action="hit"]').click()
        page.wait_for_selector("#toast:not([hidden])", timeout=10000)
        assert page.locator("#dock-deck").inner_text().strip() == "Deck: 2"
        assert page.locator(".player-card .card").count() == 0, "stale click drew a card"


def test_invite_links_copy_qr_and_share(r: Runner) -> None:
    """AC-23: fragment tokens, client-side QR, copy feedback, conditional share."""
    with r.page() as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        page.locator("#names").fill("Ann, Bob")
        page.locator("#btn-start").click()
        page.wait_for_selector("#invite-dialog[open]")
        game_id = page.evaluate("() => new URL(location.href).searchParams.get('game')")
        assert r.get(game_id)["id"] == game_id
        rows = page.locator(".invite-row")
        assert rows.count() == 2
        own_link = rows.nth(0).locator("textarea.invite-link").input_value()
        other = rows.nth(1)
        link = other.locator("textarea.invite-link").input_value()
        assert "?game=" + game_id in link
        assert re.search(r"#seat=1&token=[A-Za-z0-9_-]{16,}", link)
        assert re.search(r"#seat=0&token=[A-Za-z0-9_-]{16,}", own_link)
        assert link.split("&token=")[1] != own_link.split("&token=")[1]
        assert page.evaluate("() => location.hash") == "", "token left in the address bar"

        other.locator('[data-action="qr"]').click()
        img = other.locator(".qr-slot img")
        img.wait_for(state="visible")
        assert img.get_attribute("src").startswith("data:image")
        assert img.get_attribute("alt")

        other.locator('[data-action="copy"]').click()
        page.wait_for_function(
            "() => document.querySelector('#invite-feedback').textContent.trim().length > 0"
        )
        assert page.locator("#invite-feedback").is_visible()
        expected = 1 if page.evaluate("() => !!navigator.share") else 0
        assert page.locator('[data-action="share"]').count() == expected


def test_setup_dock_has_start_button_without_prompt(r: Runner) -> None:
    with r.page(MOBILE) as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        assert page.locator("#dock-round").inner_text().strip() == ""
        assert page.locator("#dock-prompt").inner_text().strip() == ""
        start = page.locator("#dock #btn-start")
        assert start.is_visible()
        assert start.inner_text().strip().lower() == "start game"
        assert page.locator("#setup-form #btn-start").count() == 0


def test_tagline_uses_the_same_action_words_as_the_buttons(r: Runner) -> None:
    with r.page() as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        for language in ("en", "de"):
            page.evaluate("lang => Flop7I18n.setLanguage(lang)", language)
            words = page.evaluate(
                """() => {
                     const d = Flop7I18n.dictionaries[Flop7I18n.getLanguage()];
                     return { tagline: d.tagline, hit: d['action.hit'], stay: d['action.stay'] };
                   }"""
            )
            tagline = words["tagline"].lower()
            assert words["hit"].lower() in tagline, words
            assert words["stay"].lower() in tagline, words
            assert "bank" not in tagline and "sicher" not in tagline, words

        # German uses the official rulebook terms: Noch eine / Stopp / Bonuskarten.
        official = page.evaluate(
            """() => {
                 const d = Flop7I18n.dictionaries.de;
                 return [d['action.hit'], d['action.stay'], d.tagline, d['rules.modifiersTitle']];
               }"""
        )
        assert official[:2] == ["Noch eine", "Stopp"], official
        assert "noch eine" in official[2].lower() and "stopp" in official[2].lower(), official
        assert official[3] == "Bonuskarten", official


def test_invite_url_box_shows_the_whole_link(r: Runner) -> None:
    with r.page() as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        page.locator("#names").fill("Ann, Bob")
        page.locator("#btn-start").click()
        page.wait_for_selector("#invite-dialog[open]")
        box = page.locator(".invite-row").nth(1).locator("textarea.invite-link")
        assert box.evaluate("e => e.scrollWidth <= e.clientWidth + 1"), "URL clipped horizontally"
        assert box.evaluate("e => e.scrollHeight <= e.clientHeight + 1"), "URL clipped vertically"
        assert box.bounding_box()["width"] >= 300, "URL box too narrow"
        box.focus()
        assert box.evaluate("e => e.selectionEnd - e.selectionStart === e.value.length"), (
            "focus did not select the whole URL"
        )


def test_opening_deal_reveals_cards_one_by_one(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["players"][0]["numbers"] = [5]
    state["players"][1]["numbers"] = [6]
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_function("() => document.querySelectorAll('.player-card .card').length === 2")
        assert page.locator("#deal-hint").is_visible()
        info = page.evaluate(
            """() => [...document.querySelectorAll('.player-card .card')].map(c => {
                 const style = getComputedStyle(c);
                 return { name: style.animationName, delay: style.animationDelay };
               })"""
        )
        assert len(info) == 2
        assert all(x["name"] == "card-deal" for x in info), f"deal animation missing: {info}"
        assert info[0]["delay"] != info[1]["delay"], f"cards were not staggered: {info}"


def test_invite_dialog_waits_for_the_deal(r: Runner) -> None:
    with r.page() as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        page.locator("#names").fill("Ann, Bob")
        page.locator("#btn-start").click()
        page.wait_for_selector("#table-screen:not([hidden])")
        assert page.locator("#invite-dialog").evaluate("d => !d.open"), "invite dialog covered the deal"
        page.wait_for_selector("#invite-dialog[open]")


def test_no_invite_button_at_round_end(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["round_over"] = True
    state["last_round"] = LAST_ROUND
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector('[data-action="next"]')
        assert page.locator('#dock [data-action="invite"]').count() == 0
        assert page.locator("#dock-prompt").inner_text().strip()


def test_dock_text_and_buttons_are_centered(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    r.store(view["id"], r.clean(view["id"]))
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector('[data-action="hit"]')
        styles = page.evaluate(
            """() => ({
                 prompt: getComputedStyle(document.querySelector('#dock-prompt')).textAlign,
                 actions: getComputedStyle(document.querySelector('#dock-actions')).justifyContent,
                 top: getComputedStyle(document.querySelector('.dock-top')).justifyContent
               })"""
        )
        assert styles == {"prompt": "center", "actions": "center", "top": "center"}, styles
        centers = page.evaluate(
            """() => {
                 const dock = document.querySelector('#dock').getBoundingClientRect();
                 const buttons = [...document.querySelectorAll('#dock-actions button')]
                   .map(b => b.getBoundingClientRect());
                 const left = Math.min(...buttons.map(b => b.left));
                 const right = Math.max(...buttons.map(b => b.right));
                 return { dock: dock.left + dock.width / 2, group: (left + right) / 2 };
               }"""
        )
        assert abs(centers["dock"] - centers["group"]) <= 3, centers


def test_boxes_do_not_resize_when_status_changes(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["players"][0]["numbers"] = [5]
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector("#players .player-card")
        before = [box.bounding_box()["height"] for box in page.locator(".player-card").all()]
        hint_before = page.locator("#deal-hint").bounding_box()["height"]

        state = r.load(view["id"])
        state["players"][0].update(numbers=[5, 6, 7], second=True, status="stayed")
        r.store(view["id"], state)
        page.wait_for_selector(".player-card .status-badge.status-stayed")
        after = [box.bounding_box()["height"] for box in page.locator(".player-card").all()]
        hint_after = page.locator("#deal-hint").bounding_box()["height"]

        assert before and before == after, f"player cards resized: {before} -> {after}"
        assert hint_before == hint_after, f"deal hint box resized: {hint_before} -> {hint_after}"
        assert page.locator("#deal-hint").is_hidden()


def test_all_cards_stay_visible(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["players"][0]["numbers"] = [1, 2, 3, 4, 5, 6, 7]
    state["players"][0]["mods"] = ["+5", "x2"]
    r.store(view["id"], state)
    for viewport in (DESKTOP, MOBILE):
        with r.page(viewport) as page:
            r.join(page, view, 0)
            page.wait_for_function(
                "() => document.querySelectorAll('.player-card:first-child .card').length === 9"
            )
            info = page.evaluate(
                """() => {
                     const area = document.querySelector('.player-card .cards').getBoundingClientRect();
                     const cards = [...document.querySelectorAll('.player-card:first-child .card')]
                       .map(c => c.getBoundingClientRect());
                     const box = document.querySelector('.player-card:first-child').getBoundingClientRect();
                     return {
                       clipped: cards.some(c => c.bottom > area.bottom + 1 || c.right > area.right + 1),
                       outside: cards.some(c => c.bottom > box.bottom + 1),
                       scrolls: document.querySelector('.player-card:first-child .cards').scrollHeight >
                                document.querySelector('.player-card:first-child .cards').clientHeight + 1
                     };
                   }"""
            )
            assert not info["clipped"] and not info["outside"] and not info["scrolls"], (viewport, info)


def test_mobile_layout_fits_portrait_and_landscape(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["players"][0]["numbers"] = [5]
    r.store(view["id"], state)

    for viewport in (MOBILE, {"width": 844, "height": 390}):
        with r.page(viewport) as page:
            r.join(page, view, 0)
            page.wait_for_selector("#players .player-card")
            assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1"), (
                f"horizontal overflow at {viewport}"
            )
            dock = page.locator("#dock").bounding_box()
            assert dock and dock["y"] >= 0, f"dock off screen at {viewport}"
            assert abs(dock["y"] + dock["height"] - viewport["height"]) <= 1
            # The page itself never scrolls; only .main may scroll internally.
            metrics = page.evaluate(
                """() => ({
                     doc: document.documentElement.scrollHeight,
                     body: document.body.scrollHeight,
                     win: window.innerHeight,
                     overflow: getComputedStyle(document.body).overflowY,
                     main: getComputedStyle(document.querySelector('.main')).overflowY
                   })"""
            )
            assert metrics["doc"] <= metrics["win"] + 1, f"page scrolls vertically: {metrics}"
            assert metrics["body"] <= metrics["win"] + 1, f"body scrolls vertically: {metrics}"
            assert metrics["overflow"] == "hidden" and metrics["main"] == "auto", metrics
            if viewport["height"] <= 560:
                assert dock["height"] <= 160, f"short viewport dock too tall: {dock['height']}"

            page.keyboard.press("?")
            page.wait_for_selector("#rules-dialog[open]")
            dialog = page.locator("#rules-dialog").bounding_box()
            assert dialog and 0 <= dialog["x"] and dialog["x"] + dialog["width"] <= viewport["width"] + 1
            assert dialog["height"] <= viewport["height"] * 0.93, f"rules dialog too tall: {dialog}"


def test_mobile_table_fits_without_any_scrolling(r: Runner) -> None:
    """A normal phone game shows every seat at once; no page or table scroll."""
    for names in (
        ("Ann", "Bob", "Chris", "Dana"),
        ("Ann", "Bob", "Chris", "Dana", "Emi", "Finn"),
    ):
        view = r.create(names)
        state = r.clean(view["id"])
        for player in state["players"]:
            player["numbers"] = [5]
        r.store(view["id"], state)
        with r.page(MOBILE) as page:
            r.join(page, view, 0)
            page.wait_for_selector("#players .player-card")
            assert page.locator(".player-card").count() == len(names)
            metrics = page.evaluate(
                """() => {
                     const main = document.querySelector('.main');
                     const cards = [...document.querySelectorAll('.player-card')]
                       .map(c => c.getBoundingClientRect());
                     return {
                       pageExtra: document.documentElement.scrollHeight - window.innerHeight,
                       mainExtra: main.scrollHeight - main.clientHeight,
                       lastBottom: Math.max(...cards.map(c => c.bottom)),
                       dockTop: document.querySelector('#dock').getBoundingClientRect().top
                     };
                   }"""
            )
            assert metrics["pageExtra"] <= 1, metrics
            assert metrics["mainExtra"] <= 1, f"table needs scrolling: {metrics}"
            assert metrics["lastBottom"] <= metrics["dockTop"] + 1, metrics


def test_copy_feedback_stays_inside_invite_dialog(r: Runner) -> None:
    with r.page() as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        page.locator("#names").fill("Ann, Bob")
        page.locator("#btn-start").click()
        page.wait_for_selector("#invite-dialog[open]")
        button = page.locator(".invite-row").nth(1).locator('[data-action="copy"]')

        page.evaluate(
            "() => Object.defineProperty(navigator, 'clipboard', {configurable: true, "
            "value: {writeText: () => Promise.resolve()}})"
        )
        button.click()
        page.wait_for_function(
            "() => document.querySelector('#invite-feedback').textContent.includes('copied')"
        )
        assert page.locator("#invite-feedback").is_visible()
        assert button.inner_text().strip().lower() == "link copied"

        page.evaluate(
            "() => Object.defineProperty(navigator, 'clipboard', {configurable: true, "
            "value: {writeText: () => Promise.reject(new Error('denied'))}})"
        )
        button.click()
        page.wait_for_function(
            "() => document.querySelector('#invite-feedback').textContent.includes('failed')"
        )
        field = page.locator(".invite-row").nth(1).locator("textarea.invite-link")
        assert field.evaluate(
            "e => document.activeElement === e && e.selectionEnd - e.selectionStart === e.value.length"
        ), "failed copy did not select the link for manual copying"


def test_spectator_sees_watching_prompt(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    r.store(view["id"], r.clean(view["id"]))  # deterministic: no random deal-time action
    with r.page() as page:
        page.goto(r.base_url + "/?game=" + view["id"])
        r.ready(page)
        page.wait_for_selector("#table-screen:not([hidden])")
        assert page.locator("#dock-prompt").inner_text().strip() == "You are watching this game."
        assert page.locator('#dock [data-action="hit"]').count() == 0


def test_card_labels_and_deal_hint_follow_play(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["players"][0]["numbers"] = [5]
    state["deck"] = [6]
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector('[data-action="hit"]')
        page.locator('[data-action="hit"]').click()
        page.wait_for_function(
            "() => document.querySelectorAll('.player-card:first-child .card').length === 2"
        )
        labels = page.evaluate(
            "() => [...document.querySelectorAll('.player-card:first-child .card')]"
            ".map(c => c.getAttribute('aria-label'))"
        )
        assert labels == ["Card: 5", "New card: 6"], labels
        assert page.locator("#deal-hint").is_hidden(), "opening-deal hint lingered after the first draw"


def test_keyboard_shortcuts_and_guards(r: Runner) -> None:
    """AC-25: h/s/target keys work; inputs and open dialogs swallow shortcuts."""
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["deck"] = [5, 6, 7]
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector('[data-action="hit"]')

        page.keyboard.press("?")
        page.wait_for_selector("#rules-dialog[open]")
        page.keyboard.press("h")
        assert page.locator(".player-card .card").count() == 0, "dialog did not guard 'h'"
        page.locator("#rules-dialog .dialog-close").click()

        page.evaluate(
            """() => {
                 const input = document.createElement('input');
                 input.id = 'focus-probe';
                 input.style.cssText = 'position:fixed;top:0;z-index:9999';
                 document.body.appendChild(input);
                 input.focus();
               }"""
        )
        page.keyboard.press("h")
        assert page.locator(".player-card .card").count() == 0, "input did not guard 'h'"
        page.evaluate("() => document.querySelector('#focus-probe').remove()")

        page.keyboard.press("h")
        page.wait_for_function("() => document.querySelectorAll('.player-card .card').length === 1")
        # The single card handed the turn to Bob, so "s" is not the human's to press.
        page.wait_for_function("() => !document.querySelector('#dock .btn-hit')")
        # Bring the turn back to seat 0 to exercise the stay shortcut.
        state = r.clean(view["id"])
        state["turn"] = 0
        state["players"][0]["numbers"] = [5]
        r.store(view["id"], state)
        page.wait_for_selector('[data-action="stay"]')
        page.keyboard.press("s")
        page.wait_for_selector(".player-card:nth-child(1) .status-badge.status-stayed")

        pending = r.create(("Ann", "Bob"))
        pstate = r.clean(pending["id"])
        pstate["pending"] = [{"kind": "freeze", "by": 0, "target": None, "source": "hit"}]
        pstate["discard"].append("freeze")
        r.store(pending["id"], pstate)
        r.join(page, pending, 0)
        page.wait_for_selector('[data-action="target"]')
        page.keyboard.press("2")  # targets[1] = seat 1; self is targets[0] now
        page.wait_for_selector(".player-card:nth-child(2) .status-badge.status-stayed")


def test_topbar_icon_labels_and_sound_pressed(r: Runner) -> None:
    """AC-15: centred emoji buttons, translated aria-labels, sound aria-pressed."""
    icons = {"📖", "🌐", "🔊", "🔇", "☀️", "🌙", "🔄"}
    with r.page() as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        selectors = ("#btn-rules", "#btn-language", "#btn-sound", "#btn-theme", "#btn-new-game")
        for selector in selectors:
            button = page.locator(selector)
            assert button.get_attribute("aria-label")
            assert button.get_attribute("title")
            assert button.text_content().strip() in icons, (selector, button.text_content())
        # Each emoji must be centred on both axes inside its square button.
        offsets = page.evaluate(
            """() => ['#btn-rules', '#btn-language', '#btn-sound', '#btn-theme']
                 .map(s => {
                   const box = document.querySelector(s).getBoundingClientRect();
                   const icon = document.querySelector(s + ' .btn-icon').getBoundingClientRect();
                   return {
                     dx: (icon.left + icon.width / 2) - (box.left + box.width / 2),
                     dy: (icon.top + icon.height / 2) - (box.top + box.height / 2)
                   };
                 })"""
        )
        assert all(abs(o["dx"]) <= 1 and abs(o["dy"]) <= 1 for o in offsets), offsets
        backgrounds = page.evaluate(
            """() => ['#btn-rules', '#btn-language', '#btn-sound', '#btn-theme']
                 .map(s => getComputedStyle(document.querySelector(s)).backgroundColor)"""
        )
        assert len(set(backgrounds)) == 1, f"sound button looks different from the others: {backgrounds}"
        sound = page.locator("#btn-sound")
        assert sound.get_attribute("aria-pressed") == "true"
        assert sound.inner_text().strip() == "🔊"
        sound.click()
        assert sound.get_attribute("aria-pressed") == "false"
        assert sound.inner_text().strip() == "🔇"
        assert "aus" in sound.get_attribute("aria-label").lower() or "off" in (
            sound.get_attribute("aria-label").lower()
        )
        page.locator("#btn-language").click()
        assert page.evaluate("() => document.documentElement.lang") == "de"
        assert page.locator("#btn-rules").get_attribute("aria-label")
        assert page.locator("#btn-sound").get_attribute("aria-label")


def test_text_contrast_on_dotted_background_and_topbar(r: Runner) -> None:
    """Grey hint text and top-left controls must stay readable in both themes."""
    measure = """() => {
      const css = (el, prop) => getComputedStyle(el).getPropertyValue(prop).trim();
      const rgb = (s) => (s.match(/\\d+(\\.\\d+)?/g) || []).slice(0, 3).map(Number);
      const lum = (c) => {
        const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
        return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
      };
      const ratio = (a, b) => {
        const x = lum(a), y = lum(b);
        return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
      };
      const hex = (s) => s.replace('#', '').match(/../g).map((h) => parseInt(h, 16));
      const bgOf = (el) => {
        let node = el;
        while (node) {
          const bg = css(node, 'background-color');
          if (bg && !/rgba\\(0, 0, 0, 0\\)|transparent/.test(bg)) return rgb(bg);
          node = node.parentElement;
        }
        return [0, 0, 0];
      };
      const root = document.documentElement;
      return {
        hint: ratio(rgb(css(document.querySelector('.hint'), 'color')),
                    bgOf(document.querySelector('.hint'))),
        icon: ratio(rgb(css(document.querySelector('#btn-rules'), 'color')),
                    bgOf(document.querySelector('#btn-rules'))),
        pressed: ratio(rgb(css(document.querySelector('#btn-sound'), 'color')),
                       bgOf(document.querySelector('#btn-sound'))),
        dotVsText: Math.abs(lum(hex(css(root, '--dot'))) - lum(hex(css(root, '--muted'))))
      };
    }"""
    with r.page() as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        for mode in ("light", "dark"):
            page.evaluate("mode => Flop7Theme.set(mode)", mode)
            values = page.evaluate(measure)
            for key in ("hint", "icon", "pressed"):
                assert values[key] >= 4.5, f"{mode}: {key} contrast {values[key]:.2f} < 4.5"
            assert values["dotVsText"] >= 0.15, f"{mode}: dots blend with text ({values['dotVsText']:.2f})"


def test_target_prompt_uses_card_names_and_marks_seats(r: Runner) -> None:
    view = r.create(("Ann", "Bob", "Chris"))
    state = r.clean(view["id"])
    state["pending"] = [{"kind": "freeze", "by": 0, "target": None, "source": "hit"}]
    state["discard"].append("freeze")
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector('[data-action="target"]')
        prompt = page.locator("#dock-prompt").inner_text()
        assert "target for Freeze" in prompt, prompt
        assert "for freeze" not in prompt, prompt
        assert page.locator(".player-card.is-target").count() == 3
        assert page.locator(".target-flag").count() == 3
        page.evaluate("() => Flop7I18n.setLanguage('de')")
        assert "für Freeze" in page.locator("#dock-prompt").inner_text()


def test_frozen_badge_is_distinct_from_stayed(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["players"][0].update(status="stayed", round_score=8, frozen=True)
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector(".status-frozen")
        first = page.locator(".player-card").first
        assert first.locator(".status-stayed").count() == 1
        assert first.locator(".status-frozen").count() == 1
        assert first.locator(".status-frozen").inner_text().strip().lower() == "frozen"


def test_tie_banner_shows_when_200_is_shared(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state.update(round_over=True, winner=None, last_round=LAST_ROUND)
    for player in state["players"]:
        player.update(total=200, status="stayed")
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector("#tie-banner:not([hidden])")
        assert "tie" in page.locator("#tie-banner").inner_text().lower()


def test_event_line_shows_translated_action(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    r.store(view["id"], r.clean(view["id"]))
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector('[data-action="hit"]')
        state = r.load(view["id"])
        state["events"].append({"id": state["next_event_id"], "type": "freezes", "by": 0, "target": 1})
        state["next_event_id"] += 1
        r.store(view["id"], state)
        page.wait_for_function("() => document.querySelector('#dock-event').textContent.includes('freezes')")


def test_hotseat_prompt_names_the_current_player(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    r.store(view["id"], r.clean(view["id"]))  # deterministic: no opening action card
    token = view["seats"][0]["token"]
    with r.page() as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        page.evaluate(
            """(args) => {
              localStorage.setItem('flop7:v1:game:' + args.id, JSON.stringify({
                value: { tokens: {0: args.token}, mySeat: 0, hotseat: true, invites: [] }
              }));
            }""",
            {"id": view["id"], "token": token},
        )
        page.goto(r.base_url + "/?game=" + view["id"])
        r.ready(page)
        page.wait_for_selector('[data-action="hit"]')
        assert "Ann" in page.locator("#dock-prompt").inner_text()


def test_deep_link_shows_loading_state(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    with r.page() as page:

        def slow_get(route: Any) -> None:
            time.sleep(1.5)
            route.continue_()

        page.route("**/api/games/" + view["id"], slow_get)
        page.goto(r.base_url + "/?game=" + view["id"], wait_until="commit")
        page.wait_for_selector("#loading-screen:not([hidden])", timeout=5000)
        assert page.locator("#setup-screen").is_hidden()
        page.wait_for_selector("#table-screen:not([hidden])", timeout=10000)
        assert page.locator("#loading-screen").is_hidden()


def test_double_start_creates_only_one_game(r: Runner) -> None:
    with r.page() as page:
        calls = {"n": 0}

        def slow_create(route: Any) -> None:
            calls["n"] += 1
            time.sleep(0.6)
            route.continue_()

        page.route("**/api/games", slow_create)
        page.goto(r.base_url + "/")
        r.ready(page)
        page.locator("#names").fill("Ann, Bob")
        page.evaluate("() => { const b = document.getElementById('btn-start'); b.click(); b.click(); }")
        page.wait_for_selector("#table-screen:not([hidden])", timeout=10000)
        assert calls["n"] == 1, f"created {calls['n']} games"


def test_dock_focus_survives_state_refresh(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["deck"] = [1, 2]
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector('[data-action="hit"]')
        page.locator('[data-action="hit"]').focus()
        state = r.load(view["id"])
        state["deck"] = [1]
        r.store(view["id"], state)
        page.wait_for_function("() => document.querySelector('#dock-deck').textContent === 'Deck: 1'")
        assert (
            page.evaluate(
                "() => document.activeElement && document.activeElement.getAttribute('data-action')"
            )
            == "hit"
        )


def test_spectator_has_no_invite_button(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state.update(round_over=True, winner=0, last_round=LAST_ROUND)
    state["players"][0]["total"] = 210
    r.store(view["id"], state)
    with r.page() as page:
        page.goto(r.base_url + "/?game=" + view["id"])
        r.ready(page)
        page.wait_for_selector("#winner-banner:not([hidden])")
        assert page.locator('[data-action="invite"]').count() == 0


def test_rules_document_keyboard_shortcuts(r: Runner) -> None:
    with r.page() as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        page.keyboard.press("?")
        page.wait_for_selector("#rules-dialog[open]")
        hint = page.locator("#rules-dialog .dialog-hint")
        assert "H" in hint.inner_text() and "S" in hint.inner_text(), hint.inner_text()
        page.evaluate("() => Flop7I18n.setLanguage('de')")
        assert "Noch eine" in hint.inner_text(), hint.inner_text()
        page.keyboard.press("Escape")


def test_card_hint_and_progress_have_accessible_labels(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    r.store(view["id"], r.clean(view["id"]))
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector(".player-card .progress")
        assert "Tap a card" in page.locator(".cards-hint").inner_text()
        progress = page.locator(".player-card").first.locator(".progress")
        assert "0 / 7 unique numbers" == progress.get_attribute("aria-label")
        page.evaluate("() => Flop7I18n.setLanguage('de')")
        assert "Tippe auf eine Karte" in page.locator(".cards-hint").inner_text()
        assert "verschiedene Zahlenkarten" in progress.get_attribute("aria-label")


def test_duplicate_names_are_rejected(r: Runner) -> None:
    with r.page() as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        page.locator("#names").fill("Ann, Ann")
        page.locator("#btn-start").click()
        error = page.locator("#setup-error")
        page.wait_for_selector("#setup-error:not([hidden])")
        assert "different name" in error.inner_text(), error.inner_text()
        assert page.locator("#setup-screen").is_visible()
        page.evaluate("() => Flop7I18n.setLanguage('de')")
        assert "anderen Namen" in error.inner_text(), error.inner_text()


def test_unknown_api_error_is_not_leaked(r: Runner) -> None:
    with r.page() as page:

        def unknown_error(route: Any) -> None:
            route.fulfill(
                status=400,
                content_type="application/json",
                body=json.dumps({"detail": "internal-secret-token"}),
            )

        page.route("**/api/games", unknown_error)
        page.goto(r.base_url + "/")
        r.ready(page)
        page.locator("#names").fill("Ann, Bob")
        page.locator("#btn-start").click()
        error = page.locator("#setup-error")
        page.wait_for_selector("#setup-error:not([hidden])")
        assert "internal-secret-token" not in error.inner_text()
        assert "Something went wrong" in error.inner_text(), error.inner_text()


def test_new_card_label_expires_without_a_rerender(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["players"][0]["numbers"] = [5]
    state["deck"] = [6]
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector('[data-action="hit"]')
        page.locator('[data-action="hit"]').click()
        page.wait_for_function(
            "() => [...document.querySelectorAll('.player-card:first-child .card')]"
            ".map(c => c.getAttribute('aria-label')).join('|') === 'Card: 5|New card: 6'"
        )
        page.wait_for_function(
            "() => [...document.querySelectorAll('.player-card:first-child .card')]"
            ".map(c => c.getAttribute('aria-label')).join('|') === 'Card: 5|Card: 6'",
            timeout=6000,
        )


def test_waiting_bar_is_indeterminate(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.clean(view["id"])
    state["turn"] = 1
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_function("() => !document.querySelector('#dock .btn-hit')")
        info = page.evaluate(
            """() => {
              const fill = document.querySelector('#waiting-fill');
              const style = getComputedStyle(fill);
              return { animation: style.animationName, width: fill.getBoundingClientRect().width,
                       barWidth: fill.parentElement.getBoundingClientRect().width };
            }"""
        )
        assert info["animation"] == "wait-slide", info
        assert 0 < info["width"] < info["barWidth"], info


def test_manifest_and_share_metadata_are_linked(r: Runner) -> None:
    with r.page() as page:
        page.goto(r.base_url + "/")
        r.ready(page)
        assert page.locator('link[rel="manifest"]').get_attribute("href") == "/static/manifest.json"
        assert "push your luck" in page.locator('meta[name="description"]').get_attribute("content")
        assert page.locator('meta[property="og:title"]').get_attribute("content") == "Flop7"
        status, manifest = r.api("GET", "/static/manifest.json")
        assert status == 200 and manifest["short_name"] == "Flop7"


def run_tests(runner: Runner) -> list[tuple[str, Exception]]:
    tests = [
        (name, func)
        for name, func in sorted(globals().items())
        if name.startswith("test_") and callable(func)
    ]
    failed: list[tuple[str, Exception]] = []
    for name, func in tests:
        try:
            func(runner)
        except Exception as exc:  # noqa: BLE001 — report and continue with other scenarios
            failed.append((name, exc))
            print(f"FAIL {name}: {type(exc).__name__}: {exc}", flush=True)
            if os.environ.get("BROWSER_DEBUG"):
                traceback.print_exc()
        else:
            print(f"PASS {name}", flush=True)
    print(f"\n{len(tests) - len(failed)}/{len(tests)} browser scenarios passed", flush=True)
    return failed


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="flop7-browser-") as tmp:
        port = free_port()
        db_path = os.path.join(tmp, "flop7.db")
        database_url = f"sqlite:///{db_path}"
        log_path = os.path.join(tmp, "uvicorn.log")
        env = os.environ.copy()
        env.update(
            {
                "DATABASE_URL": database_url,
                "PORT": str(port),
                "MIGRATE_ON_START": "1",
                # The suite intentionally creates dozens of games in one minute from
                # one IP; production limits would reject them (spec 8 browser gate).
                "RATE_CREATE_PER_MIN": "0",
                "RATE_SSE_MAX_PER_IP": "0",
                "PYTHONUNBUFFERED": "1",
            }
        )
        with open(log_path, "w", encoding="utf-8") as log:
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--log-level",
                    "warning",
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                base_url = f"http://127.0.0.1:{port}"
                for _ in range(80):
                    try:
                        with urllib.request.urlopen(base_url + "/api/health", timeout=1):
                            break
                    except OSError:
                        time.sleep(0.25)
                else:
                    raise RuntimeError(f"server did not start:\n{Path(log_path).read_text()}")

                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    try:
                        failed = run_tests(Runner(base_url, database_url, browser))
                    finally:
                        browser.close()
                return 1 if failed else 0
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
