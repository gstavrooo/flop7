# Flop7

A browser-based, multi-player push-your-luck card game with live updates and per-seat tokens — no accounts, no login.

## Status

Phase 1 — repository foundation and toolchain — is complete. The game engine, API, frontend and
deployment pipeline arrive in later phases; there is no runnable application yet. The source of
truth is [`FLOP7-SPEC.md`](FLOP7-SPEC.md); the phase prompts (`PHASE-1.md` … `PHASE-8.md`) describe
the build order.

## Stack

| Concern | Choice |
| --- | --- |
| Runtime | Python 3.14, FastAPI, uvicorn |
| Frontend | Vanilla JS/CSS in `app/static/`, no build step, no npm (from Phase 4) |
| Storage | SQLite file via SQLAlchemy Core; Alembic migrations applied on boot (from Phase 3) |
| Hosting | Render free tier, region Frankfurt; image in GHCR, base image pinned by digest (from Phase 6) |
| Dependencies | `requirements.in` / `requirements-dev.in` compiled to hashed lockfiles with pip-tools |
| Quality gates | ruff (lint + format) and pytest today; container, browser and secret-scan gates in later phases |

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

Not applicable yet: Phase 1 ships tooling and documentation only. Once the application lands,
uvicorn will serve both the JSON API and the static frontend on `PORT` (default `8080`), with
Alembic migrations applied at boot when `MIGRATE_ON_START=1`. The exact command is documented here
in the phase that adds the app.

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
