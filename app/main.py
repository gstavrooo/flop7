"""Flop7 HTTP API, SSE stream and static hosting (spec section 4)."""

from __future__ import annotations

import asyncio
import json
import os
import random
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import insert, select, update
from sqlalchemy.exc import OperationalError
from starlette.background import BackgroundTask

from app import engine as rules
from app.db import create_db_engine, games, get_database_url, moves, seats
from app.migrate import apply_migrations
from app.ratelimit import RateLimiter
from app.security import (
    BAD_NAMES,
    GAME_NOT_FOUND,
    SECURITY_HEADERS,
    STALE_WRITE,
    TOKEN_REQUIRED,
    hash_token,
    new_game_id,
    new_seat_token,
    token_matches,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
BOT_DELAY_SECONDS = 1.5
SSE_POLL_SECONDS = 1.0
MAX_SSE_SECONDS = 55
RATE_LIMITED = "rate limit exceeded; try again shortly"
CREATE_WINDOW_SECONDS = 60.0
# One global lock: every process still relies on the version-guarded UPDATE, but
# threads in this process must not race to be the one that commits a bot decision.
_BOT_LOCK = threading.Lock()


class CreateGameBody(BaseModel):
    players: list[str]
    bot: bool = False


class MoveBody(BaseModel):
    seat: int
    version: int


class TargetBody(MoveBody):
    target: int


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def client_key(request: Request) -> str:
    """The visitor's IP; uvicorn's proxy headers keep this from being the proxy itself."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _state_view(state: dict[str, Any], game_id: str, version: int) -> dict[str, Any]:
    """Public view (spec 2.5) plus the id and version needed by clients."""
    view = rules.public_view(state)
    view["id"] = game_id
    view["version"] = version
    return view


def create_app(
    *,
    database_url: str | None = None,
    rng: Any | None = None,
    migrate_on_start: bool | None = None,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    """Application factory; tests inject a temporary database and a deterministic rng."""
    url = database_url or get_database_url()
    should_migrate = (
        (os.environ.get("MIGRATE_ON_START") == "1") if migrate_on_start is None else migrate_on_start
    )
    db_engine = create_db_engine(url)
    bot_rng = rng if rng is not None else random.SystemRandom()
    limiter = rate_limiter if rate_limiter is not None else RateLimiter()
    create_limit = int(os.environ.get("RATE_CREATE_PER_MIN", "30"))
    sse_limit = int(os.environ.get("RATE_SSE_MAX_PER_IP", "10"))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if should_migrate:
            apply_migrations(url)
        yield
        db_engine.dispose()

    app = FastAPI(title="Flop7", lifespan=lifespan)
    app.state.db_engine = db_engine

    @app.middleware("http")
    async def harden_headers(request, call_next):
        retry_after = None
        if create_limit > 0 and request.method == "POST" and request.url.path == "/api/games":
            retry_after = limiter.check(client_key(request), create_limit, CREATE_WINDOW_SECONDS)
        if retry_after is not None:
            response = JSONResponse(
                status_code=429,
                content={"detail": RATE_LIMITED},
                headers={"Retry-After": str(retry_after)},
            )
        else:
            response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        path = request.url.path
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif path == "/" or path.startswith("/static/"):
            # Revalidate on every navigation: cheap 304s, but deploys show up
            # without a hard reload (spec 4.4).
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, exc: RequestValidationError):
        errors = exc.errors()
        detail = errors[0].get("msg", "invalid request") if errors else "invalid request"
        return JSONResponse(status_code=422, content={"detail": detail})

    def load_row(connection, game_id: str):
        return connection.execute(select(games).where(games.c.id == game_id)).first()

    def require_token(connection, game_id: str, seat: int, token: str | None) -> None:
        if token is None:
            raise HTTPException(status_code=403, detail=TOKEN_REQUIRED)
        expected = connection.execute(
            select(seats.c.token_hash).where(seats.c.game_id == game_id, seats.c.seat == seat)
        ).scalar_one_or_none()
        if not token_matches(token, expected):
            raise HTTPException(status_code=403, detail=TOKEN_REQUIRED)

    def drive_bot(game_id: str) -> None:
        """Poll-driven bot decisions: one committed version per decision (spec 2.4)."""
        with _BOT_LOCK:
            for _ in range(3):
                try:
                    with db_engine.connect() as connection:
                        row = load_row(connection, game_id)
                    if row is None:
                        return
                    state = json.loads(row.state)
                    seat = rules.bot_seat(state)
                    if seat is None or state.get("winner") is not None or state["round_over"]:
                        return
                    decision = rules.bot_action(state)
                    if decision["action"] == "wait" or time.time() < state.get("bot_due_at", 0):
                        return
                    new_state = rules.bot_move(state, bot_rng)
                    new_state["bot_due_at"] = time.time() + BOT_DELAY_SECONDS
                except rules.IllegalMove:
                    return  # e.g. no cards left; the next poll can try again

                try:
                    with db_engine.begin() as connection:
                        result = connection.execute(
                            update(games)
                            .where(games.c.id == game_id, games.c.version == row.version)
                            .values(
                                state=json.dumps(new_state),
                                version=row.version + 1,
                                winner=new_state["winner"],
                            )
                        )
                        if result.rowcount != 1:
                            continue  # another viewer/instance committed first
                        connection.execute(
                            insert(moves).values(
                                game_id=game_id,
                                seat=seat,
                                action=decision["action"],
                                target=decision.get("target"),
                                created_at=_now(),
                            )
                        )
                    return
                except OperationalError:
                    return  # SQLite write lock lost; the next poll sees the winner's version

    def commit_move(game_id: str, version: int, seat: int, action: str, target, new_state) -> dict[str, Any]:
        # The guarded UPDATE is the single write path for state (spec 7).
        with db_engine.begin() as connection:
            result = connection.execute(
                update(games)
                .where(games.c.id == game_id, games.c.version == version)
                .values(state=json.dumps(new_state), version=version + 1, winner=new_state["winner"])
            )
            if result.rowcount != 1:
                raise HTTPException(status_code=409, detail=STALE_WRITE)
            connection.execute(
                insert(moves).values(
                    game_id=game_id,
                    seat=seat,
                    action=action,
                    target=target,
                    created_at=_now(),
                )
            )
        return _state_view(new_state, game_id, version + 1)

    def do_move(game_id: str, body: MoveBody, action: str, token: str | None, target=None) -> dict[str, Any]:
        with db_engine.connect() as connection:
            row = load_row(connection, game_id)
            if row is None:
                raise HTTPException(status_code=404, detail=GAME_NOT_FOUND)
            require_token(connection, game_id, body.seat, token)
            if row.version != body.version:
                raise HTTPException(status_code=409, detail=STALE_WRITE)
            state = json.loads(row.state)
            try:
                if action == "hit":
                    new_state = rules.hit(state, body.seat, bot_rng)
                elif action == "stay":
                    new_state = rules.stay(state, body.seat)
                elif action == "next":
                    new_state = rules.start_round(rules.next_round(state), bot_rng)
                else:
                    new_state = rules.target(state, body.seat, target, bot_rng)
            except rules.IllegalMove as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        return commit_move(game_id, body.version, body.seat, action, target, new_state)

    @app.get("/api/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/games", status_code=201)
    def create_game(body: CreateGameBody) -> dict[str, Any]:
        names = [name.strip() for name in body.players]
        if not 2 <= len(names) + int(body.bot) <= 6:
            raise HTTPException(status_code=422, detail=rules.BAD_PLAYER_COUNT)
        if any(not 1 <= len(name) <= 20 for name in names):
            raise HTTPException(status_code=422, detail=BAD_NAMES)

        state = rules.start_round(rules.new_game(names, bot=body.bot, rng=bot_rng), bot_rng)
        if body.bot:
            state["bot_due_at"] = time.time() + BOT_DELAY_SECONDS
        game_id = new_game_id()
        issued = [(seat, new_seat_token()) for seat in range(len(state["players"]))]
        with db_engine.begin() as connection:
            connection.execute(
                insert(games).values(
                    id=game_id, state=json.dumps(state), version=0, winner=None, created_at=_now()
                )
            )
            connection.execute(
                insert(seats),
                [
                    {"game_id": game_id, "seat": seat, "token_hash": hash_token(token)}
                    for seat, token in issued
                ],
            )

        view = _state_view(state, game_id, 0)
        view["seats"] = [
            {"seat": seat, "name": state["players"][seat]["name"], "token": token} for seat, token in issued
        ]
        return view  # the only response that ever contains seat tokens

    @app.post("/api/games/{game_id}/hit")
    def hit(
        game_id: str,
        body: MoveBody,
        x_seat_token: str | None = Header(default=None, alias="X-Seat-Token"),
    ) -> dict[str, Any]:
        return do_move(game_id, body, "hit", x_seat_token)

    @app.post("/api/games/{game_id}/stay")
    def stay(
        game_id: str,
        body: MoveBody,
        x_seat_token: str | None = Header(default=None, alias="X-Seat-Token"),
    ) -> dict[str, Any]:
        return do_move(game_id, body, "stay", x_seat_token)

    @app.post("/api/games/{game_id}/next")
    def next_round(
        game_id: str,
        body: MoveBody,
        x_seat_token: str | None = Header(default=None, alias="X-Seat-Token"),
    ) -> dict[str, Any]:
        return do_move(game_id, body, "next", x_seat_token)

    @app.post("/api/games/{game_id}/target")
    def target(
        game_id: str,
        body: TargetBody,
        x_seat_token: str | None = Header(default=None, alias="X-Seat-Token"),
    ) -> dict[str, Any]:
        return do_move(game_id, body, "target", x_seat_token, body.target)

    @app.get("/api/games/{game_id}")
    def get_game(game_id: str) -> dict[str, Any]:
        with db_engine.connect() as connection:
            if load_row(connection, game_id) is None:
                raise HTTPException(status_code=404, detail=GAME_NOT_FOUND)
        drive_bot(game_id)
        with db_engine.connect() as connection:
            row = load_row(connection, game_id)
        return _state_view(json.loads(row.state), game_id, row.version)

    @app.get("/api/games/{game_id}/events")
    async def events(
        request: Request,
        game_id: str,
        seconds: int = Query(default=MAX_SSE_SECONDS, ge=0, le=MAX_SSE_SECONDS),
    ) -> StreamingResponse:
        with db_engine.connect() as connection:
            if load_row(connection, game_id) is None:
                raise HTTPException(status_code=404, detail=GAME_NOT_FOUND)

        key = client_key(request)
        acquired = False
        if sse_limit > 0:
            acquired = limiter.acquire_slot(key, sse_limit)
            if not acquired:
                raise HTTPException(status_code=429, detail=RATE_LIMITED)

        async def stream():
            last_version = None
            deadline = time.monotonic() + seconds
            try:
                yield "retry: 1000\n\n"
                while True:
                    drive_bot(game_id)
                    with db_engine.connect() as connection:
                        row = load_row(connection, game_id)
                    if row is None:
                        break
                    if row.version != last_version:
                        last_version = row.version
                        yield (
                            "data: "
                            + json.dumps(_state_view(json.loads(row.state), game_id, row.version))
                            + "\n\n"
                        )
                    if time.monotonic() >= deadline:
                        break
                    # tech-debt: one poll per viewer per second; move to a DB-notify
                    # mechanism at scale (spec 4.2 trade-off).
                    await asyncio.sleep(SSE_POLL_SECONDS)
            finally:
                pass

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            background=BackgroundTask(limiter.release_slot, key) if acquired else None,
        )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    # Mounted last so the API routes win. StaticFiles emits ETag/Last-Modified and
    # answers 304 to conditional requests — including the root index.html.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")

    return app


app = create_app()
