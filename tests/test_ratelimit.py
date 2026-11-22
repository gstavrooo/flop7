"""Rate-limit guards: bounded windows, SSE slots and the 429 responses (spec 8 style)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import RATE_LIMITED, create_app
from app.migrate import apply_migrations
from app.ratelimit import RateLimiter


class FixedRng:
    def shuffle(self, _sequence) -> None:
        pass


def test_hit_budget_window_and_key_isolation() -> None:
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0])
    assert limiter.check("ip", 2, 10) is None
    assert limiter.check("ip", 2, 10) is None
    retry_after = limiter.check("ip", 2, 10)
    assert retry_after is not None and 1 <= retry_after <= 11
    assert limiter.check("other-ip", 2, 10) is None
    now[0] = 10.01
    assert limiter.check("ip", 2, 10) is None


def test_slots_are_bounded_and_released() -> None:
    limiter = RateLimiter(max_keys=2)
    assert limiter.acquire_slot("a", 2)
    assert limiter.acquire_slot("a", 2)
    assert not limiter.acquire_slot("a", 2)
    limiter.release_slot("a")
    assert limiter.acquire_slot("a", 2)
    limiter.release_slot("a")
    limiter.release_slot("a")
    assert limiter.acquire_slot("b", 1)
    assert limiter.acquire_slot("d", 1)
    assert not limiter.acquire_slot("c", 1)  # max_keys reached
    limiter.release_slot("b")
    assert limiter.acquire_slot("c", 1)


@pytest.fixture
def limited_client(tmp_path, monkeypatch):
    monkeypatch.setenv("RATE_CREATE_PER_MIN", "2")
    monkeypatch.setenv("RATE_SSE_MAX_PER_IP", "1")
    url = f"sqlite:///{tmp_path / 'flop7.db'}"
    apply_migrations(url)
    app = create_app(database_url=url, rng=FixedRng())
    with TestClient(app) as client:
        yield client


def test_game_creation_returns_429_with_retry_after(limited_client: TestClient) -> None:
    body = {"players": ["A", "B"]}
    for _ in range(2):
        assert limited_client.post("/api/games", json=body).status_code == 201
    blocked = limited_client.post("/api/games", json=body)
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == RATE_LIMITED
    assert int(blocked.headers["Retry-After"]) >= 1
    assert blocked.headers["x-content-type-options"] == "nosniff"
    assert blocked.headers["Cache-Control"] == "no-store"
    assert limited_client.get("/api/health").json() == {"ok": True}


def test_sse_streams_are_capped_per_client(limited_client: TestClient) -> None:
    game_id = limited_client.post("/api/games", json={"players": ["A", "B"]}).json()["id"]
    with limited_client.stream("GET", f"/api/games/{game_id}/events?seconds=2") as first:
        assert first.status_code == 200
        blocked = limited_client.get(f"/api/games/{game_id}/events?seconds=0")
        assert blocked.status_code == 429
        assert blocked.json()["detail"] == RATE_LIMITED
