"""Browser regression suite for spec section 8 (no framework, no screenshots/traces).

Launches uvicorn on a free port against a temporary SQLite database, then drives
Chromium with the plain Playwright API. Every scenario from the spec's
``browser-test`` row is a named ``test_*`` function below. Run with::

    python tests/browser_checks.py
"""

from __future__ import annotations

import json
import os
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

    @staticmethod
    def ready(page: Page) -> None:
        """Wait until the app has booted and rendered (not just static HTML)."""
        page.wait_for_function(
            "() => window.Flop7I18n "
            "&& document.querySelector('#setup-title').textContent.trim().length > 0 "
            "&& document.querySelector('#dock-prompt').textContent.trim().length > 0"
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
    state = r.load(view["id"])
    state["deck"] = [7]  # a single, known number card
    r.store(view["id"], state)
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector('[data-action="hit"]')
        assert page.locator("#dock-deck").inner_text() == "Deck: 1"
        page.locator('[data-action="hit"]').click()
        page.wait_for_function("() => document.querySelector('#dock-deck').textContent === 'Deck: 0'")
        assert page.locator(".player-card").first.locator(".card").count() == 1
        page.locator('[data-action="stay"]').click()
        page.wait_for_selector(".player-card .status-badge.status-stayed")

    # A pending freeze is a human decision for the drawer.
    pending_view = r.create(("Ann", "Bob"))
    state = r.load(pending_view["id"])
    state["pending"] = [{"kind": "freeze", "by": 0, "target": None, "source": "hit"}]
    state["discard"].append("freeze")
    r.store(pending_view["id"], state)
    with r.page() as page:
        r.join(page, pending_view, 0)
        page.wait_for_selector('[data-action="target"]')
        assert "freeze" in page.locator("#dock-prompt").inner_text().lower()
        page.locator('[data-action="target"]').first.click()
        page.wait_for_selector(".player-card:nth-child(2) .status-badge.status-stayed")


def test_space_next_round_also_with_focused_summary(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.load(view["id"])
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
        state = r.load(view["id"])
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
    state = r.load(view["id"])
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
        saved = page.evaluate("id => JSON.parse(localStorage.getItem('flop7:v1:game:' + id))", new_id)
        assert saved and isinstance(saved["value"]["tokens"]["0"], str)

    # The rematch keeps exactly the same names and bot, and the seat still works.
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
    state = r.load(view["id"])
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
    state = r.load(view["id"])
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
    with r.page() as page:
        r.join(page, view, 0)
        page.wait_for_selector('[data-action="hit"]')
        assert page.title() == "▶ Flop7"
        state = r.load(view["id"])
        state["turn"] = 1  # only seat 0 has a token on this device
        r.store(view["id"], state)
        page.wait_for_function("() => document.title === 'Flop7'")


def test_solo_bot_thinking_and_explicit_next(r: Runner) -> None:
    view = r.create(("Human",), bot=True)
    with r.page() as page:
        r.join(page, view, 0)
        state = r.load(view["id"])
        state["turn"] = 1
        state["bot_due_at"] = time.time() + 1000  # hold the bot in "thinking"
        r.store(view["id"], state)
        page.reload()
        page.wait_for_selector(".status-badge.status-thinking")
        assert "thinking" in page.locator("#dock-prompt").inner_text().lower()

        # Make the bot due and reload: the server commits exactly one decision.
        before = r.get(view["id"])["version"]
        state = r.load(view["id"])
        state["bot_due_at"] = time.time() - 1
        r.store(view["id"], state)
        page.reload()
        deadline = time.time() + 10
        while time.time() < deadline and r.get(view["id"])["version"] <= before:
            time.sleep(0.2)
        assert r.get(view["id"])["version"] > before, "bot did not commit a decision"

        # Round break keeps an explicit Next round button (no auto-advance).
        state = r.load(view["id"])
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
    state = r.load(view["id"])
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

        page.locator("#btn-theme").click()  # auto -> light
        assert page.evaluate("() => document.documentElement.dataset.theme") == "light"
        page.locator("#btn-theme").click()  # light -> dark
        assert page.evaluate("() => document.documentElement.dataset.theme") == "dark"
        page.locator("#btn-theme").click()  # dark -> auto
        assert page.evaluate("() => !document.documentElement.hasAttribute('data-theme')")

        page.keyboard.press("?")
        page.wait_for_selector("#rules-dialog[open]")
        page.locator("#rules-dialog .dialog-close").click()
        assert page.locator("#rules-dialog").is_hidden()


def test_dark_mode_neutral_history(r: Runner) -> None:
    view = r.create(("Ann", "Bob"))
    state = r.load(view["id"])
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
        r.join(page, view, 0)
        page.wait_for_selector('[data-action="hit"]')
        heights.append(page.locator(".waiting-bar").bounding_box()["height"])

        state = r.load(view["id"])
        state["turn"] = 1
        r.store(view["id"], state)
        page.wait_for_function("() => !document.querySelector('#dock .btn-hit')")
        heights.append(page.locator(".waiting-bar").bounding_box()["height"])

        state = r.load(view["id"])
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
    state = r.load(view["id"])
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
