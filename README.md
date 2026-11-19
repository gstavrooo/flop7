# Flop7

A browser-based, multi-player push-your-luck card game with live updates and per-seat tokens — no accounts, no login.

## Status

Phases 1–3 are complete: repository foundation, the pure rules engine, and the FastAPI application
with SQLite persistence, Alembic migrations and the seat-token security model. The frontend is a
placeholder (`app/static/index.html`); the real UI and the deployment pipeline arrive in later
phases. The source of truth is [`FLOP7-SPEC.md`](FLOP7-SPEC.md); the phase prompts
(`PHASE-1.md` … `PHASE-8.md`) describe the build order.

## Stack

| Concern | Choice |
| --- | --- |
| Runtime | Python 3.14, FastAPI, uvicorn |
| Frontend | Vanilla JS/CSS in `app/static/`, no build step, no npm (from Phase 4) |
| Storage | SQLite file via SQLAlchemy Core; Alembic migrations applied on boot |
| Hosting | Render free tier, region Frankfurt; image in GHCR, base image pinned by digest (from Phase 6) |
| Dependencies | `requirements.in` / `requirements-dev.in` compiled to hashed lockfiles with pip-tools |
| Quality gates | ruff (lint + format) and pytest (engine, API, migrations) today; container, browser and secret-scan gates in later phases |

## Development

Requires Python 3.14. The lockfiles were compiled for that interpreter and are hashed.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
```

Run the checks that exist today:

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m pytest -q
```

After editing a `*.in` file, recompile both lockfiles (runtime first — the dev file includes it):

```sh
.venv/bin/python -m piptools compile --generate-hashes --no-strip-extras -o requirements.txt requirements.in
.venv/bin/python -m piptools compile --generate-hashes --allow-unsafe --no-strip-extras -o requirements-dev.txt requirements-dev.in
```

## Running

uvicorn serves the JSON API and the static files from one app. `DATABASE_URL` defaults to
`sqlite:///flop7.db`; `PORT` defaults to `8080`. Set `MIGRATE_ON_START=1` to apply Alembic
migrations before serving (the app refuses to start against a legacy, unmigrated schema):

```sh
MIGRATE_ON_START=1 .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
```

Endpoints: `POST /api/games`, `POST /api/games/{id}/{hit,stay,next,target}`,
`GET /api/games/{id}`, `GET /api/games/{id}/events?seconds=N` (SSE), `GET /api/health`, plus the
static root. Seat tokens are returned only in the create response and must be sent back in the
`X-Seat-Token` header; a stale `version` yields 409.

## CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every pull request with two gates
today: `lint` (ruff check and format check) and `test` (pytest from the hashed dev lockfile).
The remaining gates from spec section 8 — `container-test`, `browser-test`, gitleaks, and
shellcheck/actionlint/zizmor inside `lint` — arrive in later phases.

Branches are trunk-based: squash-merge only, linear history, no direct pushes to `main`.

## Trade-offs

- **Ephemeral data:** the Render free tier loses the filesystem on redeploy, restart or
  scale-to-zero. Game data is not durable — a deliberate choice for zero-cost hosting.
- **Cold starts:** after roughly 15 minutes idle the first request takes about a minute to wake
  the instance. The frontend must reconnect gracefully (SSE reconnect covers this).
- **No auto-rollback:** a failed deploy leaves a red CI/CD run and a fix-forward commit; it never
  silently reverts to an older image.
- **SQLite + polling SSE:** version-checked writes and polling avoid queues and extra services at
  the cost of a request per viewer per second.
