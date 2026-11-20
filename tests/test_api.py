"""API tests: status matrix, integrity, tokens, bot driving, SSE and headers (spec 8)."""

from __future__ import annotations

import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update

from app import engine as rules
from app.db import games, moves, seats
from app.main import create_app
from app.migrate import apply_migrations
from app.security import (
    BAD_NAMES,
    GAME_NOT_FOUND,
    SECURITY_HEADERS,
    STALE_WRITE,
    TOKEN_REQUIRED,
    hash_token,
)


class FixedRng:
    """Keeps the deck order so tests can predict the next card."""

    def shuffle(self, _sequence) -> None:
        pass


@pytest.fixture
def env(tmp_path):
    url = f"sqlite:///{tmp_path / 'flop7.db'}"
    apply_migrations(url)
    app = create_app(database_url=url, rng=FixedRng())
    with TestClient(app) as client:
        yield client, url


def create(client, players=("A", "B"), *, bot: bool = False) -> dict:
    response = client.post("/api/games", json={"players": list(players), "bot": bot})
    assert response.status_code == 201, response.text
    return response.json()


def auth(view: dict, seat: int = 0) -> dict[str, str]:
    return {"X-Seat-Token": view["seats"][seat]["token"]}


def row_for(url: str, game_id: str):
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return connection.execute(select(games).where(games.c.id == game_id)).first()
    finally:
        engine.dispose()


def set_state(url: str, game_id: str, state: dict, *, version: int | None = None) -> None:
    engine = create_engine(url)
    values = {"state": json.dumps(state), "winner": state["winner"]}
    if version is not None:
        values["version"] = version
    try:
        with engine.begin() as connection:
            connection.execute(update(games).where(games.c.id == game_id).values(**values))
    finally:
        engine.dispose()


def with_deck(state: dict, cards: list) -> dict:
    """`cards` are given in draw order."""
    new = copy.deepcopy(state)
    new["deck"] = list(reversed(cards))
    new["discard"] = []
    return new


# --- Create, validation and public view ---------------------------------------


def test_create_returns_201_public_view_and_one_time_tokens(env):
    client, url = env
    view = create(client)
    game_id = view["id"]

    assert view["version"] == 0
    assert view["round"] == 1
    assert view["winner"] is None and view["round_over"] is False
    # Official opening deal consumed one card per player (action cards may draw more).
    assert view["deck_count"] <= 92 and "deck" not in view
    assert len(view["scores"]) == 2 and view["targets"] == []
    assert all("token" not in str(event) for event in view["events"])  # deal events, no secrets
    assert [seat["seat"] for seat in view["seats"]] == [0, 1]
    assert all(len(seat["token"]) >= 20 for seat in view["seats"])

    fetched = client.get(f"/api/games/{game_id}")
    assert fetched.status_code == 200
    assert "seats" not in fetched.json()
    assert all(seat["token"] not in fetched.text for seat in view["seats"])

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            stored = connection.execute(select(seats).where(seats.c.game_id == game_id)).all()
    finally:
        engine.dispose()
    assert len(stored) == 2
    for stored_seat in stored:
        token = view["seats"][stored_seat.seat]["token"]
        assert stored_seat.token_hash == hash_token(token)
        assert token not in stored_seat.token_hash


def test_names_are_trimmed(env):
    client, _ = env
    view = create(client, ("  A  ", "B"))
    assert [player["name"] for player in view["players"]] == ["A", "B"]


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"players": ["A"]}, rules.BAD_PLAYER_COUNT),
        ({"players": list("ABCDEF"), "bot": True}, rules.BAD_PLAYER_COUNT),
        ({"players": ["A", ""]}, BAD_NAMES),
        ({"players": ["A" * 21, "B"]}, BAD_NAMES),
        ({"players": ["A", "B"], "bot": "not-a-bool"}, None),
        ({}, None),
    ],
)
def test_create_validation_returns_422(env, body, detail):
    client, _ = env
    response = client.post("/api/games", json=body)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    if detail is not None:
        assert response.json()["detail"] == detail


# --- Status matrix and token/version rules ------------------------------------


def test_missing_and_wrong_seat_token_403(env):
    client, _ = env
    view = create(client)
    game_id = view["id"]
    body = {"seat": 0, "version": 0}

    assert client.post(f"/api/games/{game_id}/hit", json=body).json()["detail"] == TOKEN_REQUIRED
    assert client.post(f"/api/games/{game_id}/hit", json=body).status_code == 403
    assert (
        client.post(
            f"/api/games/{game_id}/hit", json=body, headers={"X-Seat-Token": "not-a-token"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/games/{game_id}/hit",
            json={"seat": 1, "version": 0},
            headers=auth(view, 0),
        ).status_code
        == 403
    )


def test_unknown_game_returns_404(env):
    client, _ = env
    assert client.get("/api/games/unknown").json()["detail"] == GAME_NOT_FOUND
    assert (
        client.post(
            "/api/games/unknown/hit",
            json={"seat": 0, "version": 0},
            headers={"X-Seat-Token": "anything"},
        ).status_code
        == 404
    )
    assert client.get("/api/games/unknown/events?seconds=0").status_code == 404


def test_stale_version_returns_409_and_draws_once(env):
    client, url = env
    view = create(client)
    game_id = view["id"]
    set_state(url, game_id, with_deck(json.loads(row_for(url, game_id).state), [5, 6]))

    first = client.post(f"/api/games/{game_id}/hit", json={"seat": 0, "version": 0}, headers=auth(view))
    assert first.status_code == 200
    stale = client.post(f"/api/games/{game_id}/hit", json={"seat": 0, "version": 0}, headers=auth(view))
    assert stale.status_code == 409
    assert stale.json()["detail"] == STALE_WRITE
    state = json.loads(row_for(url, game_id).state)
    assert state["players"][0]["numbers"] == [5]


def test_double_submit_draws_exactly_one_card(env):
    client, url = env
    view = create(client)
    game_id = view["id"]
    set_state(url, game_id, with_deck(json.loads(row_for(url, game_id).state), [5, 6]))
    body = {"seat": 0, "version": 0}

    def submit(_):
        return TestClient(client.app).post(f"/api/games/{game_id}/hit", json=body, headers=auth(view))

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert json.loads(row_for(url, game_id).state)["players"][0]["numbers"] == [5]


def test_moves_are_persisted(env):
    client, url = env
    view = create(client, ("A", "B", "C"))
    game_id = view["id"]
    set_state(url, game_id, with_deck(json.loads(row_for(url, game_id).state), [5, 4, 3]))

    # One card per turn: seat 0 draws, then seat 1, then seat 2 decides the pending.
    assert (
        client.post(
            f"/api/games/{game_id}/hit", json={"seat": 0, "version": 0}, headers=auth(view)
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/games/{game_id}/hit", json={"seat": 1, "version": 1}, headers=auth(view, 1)
        ).status_code
        == 200
    )
    state = json.loads(row_for(url, game_id).state)
    state["pending"] = [{"kind": "freeze", "by": 2, "target": None, "source": "hit"}]
    set_state(url, game_id, state)
    response = client.post(
        f"/api/games/{game_id}/target",
        json={"seat": 2, "version": 2, "target": 0},
        headers=auth(view, 2),
    )
    assert response.status_code == 200, response.text

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            audit = connection.execute(
                select(moves.c.seat, moves.c.action, moves.c.target)
                .where(moves.c.game_id == game_id)
                .order_by(moves.c.id)
            ).all()
    finally:
        engine.dispose()
    assert [tuple(entry) for entry in audit] == [(0, "hit", None), (1, "hit", None), (2, "target", 0)]


def test_illegal_moves_use_stable_409_messages(env):
    client, url = env
    view = create(client)
    game_id = view["id"]
    move_url = f"/api/games/{game_id}"

    def post(action: str, seat: int, version: int, **extra):
        body = {"seat": seat, "version": version, **extra}
        return client.post(f"{move_url}/{action}", json=body, headers=auth(view, seat))

    assert post("stay", 1, 0).json()["detail"] == rules.NOT_YOUR_TURN
    assert post("next", 0, 0).json()["detail"] == rules.ROUND_RUNNING
    assert post("target", 0, 0, target=1).json()["detail"] == rules.NOTHING_TO_TARGET

    state = json.loads(row_for(url, game_id).state)
    state["pending"] = [{"kind": "freeze", "by": 0, "target": None, "source": "hit"}]
    set_state(url, game_id, state)
    assert post("hit", 0, 0).json()["detail"] == rules.CHOOSE_TARGET
    assert post("target", 1, 0, target=0).json()["detail"] == rules.NOT_YOUR_DECISION
    assert post("target", 0, 0, target=99).json()["detail"] == rules.INVALID_TARGET

    state = json.loads(row_for(url, game_id).state)
    state["pending"] = []
    state["deck"] = []
    state["discard"] = []
    set_state(url, game_id, state)
    assert post("hit", 0, 0).json()["detail"] == rules.NO_CARDS_LEFT

    state = json.loads(row_for(url, game_id).state)
    state["round_over"] = True
    state["deck"] = [5, 6, 7]  # the next round deals starting cards again
    set_state(url, game_id, state)
    assert post("hit", 0, 0).json()["detail"] == rules.START_NEXT_ROUND
    assert post("next", 1, 0).status_code == 200

    state = json.loads(row_for(url, game_id).state)
    state["winner"] = 0
    set_state(url, game_id, state)
    assert post("hit", 0, 1).json()["detail"] == rules.GAME_OVER


# --- Server-side bot -----------------------------------------------------------


def make_bot_due(url: str, game_id: str, *, due: float | None = None) -> None:
    state = json.loads(row_for(url, game_id).state)
    assert rules.bot_seat(state) == 1
    state["turn"] = 1
    state = with_deck(state, [5, 4, 3])
    state["bot_due_at"] = time.time() - 1 if due is None else due
    set_state(url, game_id, state)


def test_solo_bot_plays_server_side(env):
    client, url = env
    view = create(client, ("Human",), bot=True)
    game_id = view["id"]
    make_bot_due(url, game_id)

    before = row_for(url, game_id).version
    response = client.get(f"/api/games/{game_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == before + 1
    assert any(event["type"] == "draws" and event["seat"] == 1 for event in body["events"])
    assert "seats" not in body  # no client-side bot credentials needed

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            audit = connection.execute(
                select(moves.c.seat, moves.c.action).where(moves.c.game_id == game_id)
            ).all()
    finally:
        engine.dispose()
    assert [tuple(entry) for entry in audit] == [(1, "hit")]

    make_bot_due(url, game_id, due=time.time() + 100)
    version = row_for(url, game_id).version
    client.get(f"/api/games/{game_id}")
    assert row_for(url, game_id).version == version  # not due yet


def test_human_hit_then_bot_hit_alternate_turns(env):
    """A human card is followed by exactly one bot card, then the turn returns."""
    client, url = env
    view = create(client, ("Human",), bot=True)
    game_id = view["id"]
    state = json.loads(row_for(url, game_id).state)
    state["bot_due_at"] = time.time() - 1  # make the bot due on the next poll
    set_state(url, game_id, with_deck(state, [5, 6]))

    first = client.post(f"/api/games/{game_id}/hit", json={"seat": 0, "version": 0}, headers=auth(view))
    assert first.status_code == 200
    assert first.json()["turn"] == 1  # the bot is up now

    before = row_for(url, game_id).version
    polled = client.get(f"/api/games/{game_id}")
    assert polled.json()["version"] == before + 1
    assert polled.json()["turn"] == 0  # the bot's single card passed it back

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            audit = connection.execute(
                select(moves.c.seat, moves.c.action).where(moves.c.game_id == game_id)
            ).all()
    finally:
        engine.dispose()
    assert [tuple(entry) for entry in audit] == [(0, "hit"), (1, "hit")]


def test_concurrent_bot_viewers_commit_exactly_one_decision(env):
    client, url = env
    view = create(client, ("Human",), bot=True)
    game_id = view["id"]
    make_bot_due(url, game_id)
    before = row_for(url, game_id).version

    def poll(_):
        # Fresh TestClients, no lifespan: several "viewers" hitting one game at once.
        return TestClient(client.app).get(f"/api/games/{game_id}").status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(poll, range(8)))
    assert statuses == [200] * 8
    assert row_for(url, game_id).version == before + 1

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            count = connection.execute(select(moves.c.id).where(moves.c.game_id == game_id)).all()
    finally:
        engine.dispose()
    assert len(count) == 1


# --- SSE, logs and headers -----------------------------------------------------


def test_sse_emits_retry_then_one_data_line_per_version(env):
    client, _url = env
    view = create(client)
    game_id = view["id"]

    response = client.get(f"/api/games/{game_id}/events?seconds=0")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.startswith("retry: 1000\n\n")
    data_lines = [line for line in response.text.splitlines() if line.startswith("data: ")]
    assert len(data_lines) == 1
    payload = json.loads(data_lines[0][len("data: ") :])
    assert payload["version"] == 0 and "deck" not in payload

    assert client.get(f"/api/games/{game_id}/events?seconds=56").status_code == 422
    assert client.get("/api/games/unknown/events?seconds=0").status_code == 404


@pytest.mark.parametrize(("name", "expected"), SECURITY_HEADERS.items())
def test_security_headers_on_every_response_kind(env, name, expected):
    client, view = env[0], create(env[0])
    for path in ("/api/health", "/", "/api/games/unknown", f"/api/games/{view['id']}"):
        response = client.get(path)
        assert response.headers[name] == expected, path


def test_revalidation_headers_for_html_and_static_assets(env):
    client, _ = env
    for path in ("/", "/static/index.html"):
        response = client.get(path)
        assert response.status_code == 200
        assert "etag" in response.headers and "last-modified" in response.headers
        assert response.headers["cache-control"] == "no-cache"
        conditional = client.get(path, headers={"If-None-Match": response.headers["etag"]})
        assert conditional.status_code == 304


def test_tokens_never_appear_in_logs_or_later_responses(env, caplog):
    client, _url = env
    view = create(client)
    game_id = view["id"]
    token = view["seats"][0]["token"]

    client.get(f"/api/games/{game_id}")
    client.post(
        f"/api/games/{game_id}/hit",
        json={"seat": 0, "version": 0},
        headers={"X-Seat-Token": token},
    )
    assert token not in caplog.text
    assert token not in json.dumps(client.get(f"/api/games/{game_id}").json())
