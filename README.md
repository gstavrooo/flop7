# Flop7

A browser-based, multi-player push-your-luck card game with live updates and per-seat tokens — no accounts, no login.

## Status

Phases 1–6 are complete: repository foundation, the pure rules engine, the FastAPI application
with SQLite persistence/Alembic/seat-token security, the vanilla-JS frontend (setup, table,
live SSE updates, invites, i18n, themes, dialogs, keyboard shortcuts), the full test pyramid with
all CI gates, and the manual GHCR/Render deploy pipeline. The source of truth is
[`FLOP7-SPEC.md`](FLOP7-SPEC.md); the phase prompts (`PHASE-1.md` … `PHASE-8.md`) describe the build order.

## Stack

| Concern | Choice |
| --- | --- |
| Runtime | FastAPI, uvicorn; Python 3.15 slim in the container (digest-pinned), CI/lockfiles on 3.14 |
| Frontend | Vanilla JS/CSS in `app/static/`, no build step, no npm |
| Storage | SQLite file via SQLAlchemy Core; Alembic migrations applied on boot |
| Hosting | Render free tier, region Frankfurt; image in GHCR, base image pinned by digest |
| Dependencies | `requirements.in` / `requirements-dev.in` compiled to hashed lockfiles with pip-tools |
| Quality gates | pytest (engine/API/migrations/i18n), Playwright browser regressions, container smoke + Trivy, ruff, shellcheck, actionlint, zizmor, gitleaks |

## Development

Requires Python 3.14. The lockfiles were compiled for that interpreter and are hashed.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
```

Run the checks locally:

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m pytest -q
.venv/bin/python -m playwright install chromium  # once
.venv/bin/python tests/browser_checks.py        # 20 named browser scenarios, ~20 s
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

The frontend at `/` is a single page: create a game (optionally hot-seat and/or with a bot),
then hand out the per-seat invite links from the Invite dialog. Invite tokens travel only in the
URL fragment, which the app strips from the address bar after reading; tokens for "your" seats
persist in namespaced local storage and degrade to session-only if storage is blocked or corrupt.
Language (de/en), theme (auto/light/dark) and sound are global preferences, persisted and applied
before first paint. Keyboard shortcuts: `H`/`Space` hit, `S` stay, `1..6` choose a target, `?`
opens the rules; shortcuts are ignored in inputs and dialogs.

## CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every pull request with all five
required gates from spec section 8: `test` (pytest from the hashed dev lockfile), `container-test`
(`scripts/test-image.sh`: build, HTTP smoke via `scripts/smoke.sh`, Trivy fixable HIGH/CRITICAL
scan with `.trivyignore`), `browser-test` (Playwright/Chromium on pinned `ubuntu-24.04`, mobile
and desktop, no screenshots/traces), `lint` (ruff, shellcheck, actionlint, zizmor offline) and
`gitleaks` (whole history). All actions and tool images are pinned by SHA/digest.

Branches are trunk-based: squash-merge only, linear history, no direct pushes to `main`. After
opening a PR, auto-merge is enabled with `gh pr merge <nr> --auto --squash`.

[`.github/dependabot.yml`](.github/dependabot.yml) opens grouped weekly PRs for pip, GitHub
Actions and the Docker digest. [`.github/workflows/dependabot-automerge.yml`](.github/workflows/dependabot-automerge.yml)
opts those PRs into auto-merge for **patch/minor** updates only (squash); majors stay manual.

## Deployment (manual in Phase 6)

The current live service is <https://flop7-01ed.onrender.com> (free plan, Frankfurt; ephemeral DB).

[`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) is **manual-only** for now — Phase 7
adds the push/schedule triggers. Run it on `main` with:

```sh
gh workflow run deploy.yml --ref main
```

The single job builds the image for `linux/amd64`, smoke-tests that exact image and Trivy-scans it
via `scripts/test-image.sh`, pushes it to `ghcr.io/<owner>/flop7:<full-commit-sha>` with
`packages: write`, then uses `scripts/render_deploy.py` to find-or-create the Render service
(`flop7`, `free`, `frankfurt`, auto-deploy off), patch its image, trigger a deploy of exactly that
tag and poll to `live`. Finally `scripts/smoke.sh` checks the public URL (health, frontend, create,
403, 200, 409, SSE) and `scripts/prune_ghcr.py` prunes old images. Values move between steps via
env/outputs, never interpolated into `run:`.

Prerequisites (one-time, manual):

1. A free Render account in the Frankfurt region.
2. A Render API key with service read/write and registry-credential read access, stored as the
   repository secret `RENDER_API_KEY` (verify with `gh secret list`).
3. For the private GHCR image: a Render **Registry Credential** using a GitHub PAT with
   `read:packages`. The deploy script picks the single GitHub credential automatically; if there
   are several, set the repository variable `RENDER_REGISTRY_CREDENTIAL_ID`.
4. If the API key sees several workspaces, set the repository variable `RENDER_OWNER_ID`.

**Image retention:** GHCR keeps only the three newest SHA-tagged versions; the tag being deployed
is always protected. This stays inside the ~500 MB free private-package allowance, and Render
pulls the image on every deploy/restart, so older versions are never needed at runtime.

## Trade-offs

- **Ephemeral data:** the Render free tier loses the filesystem on redeploy, restart or
  scale-to-zero. Game data is not durable — a deliberate choice for zero-cost hosting.
- **Cold starts:** after roughly 15 minutes idle the first request takes about a minute to wake
  the instance. The frontend must reconnect gracefully (SSE reconnect covers this).
- **No auto-rollback:** a failed deploy leaves a red CI/CD run and a fix-forward commit; it never
  silently reverts to an older image.
- **SQLite + polling SSE:** version-checked writes and polling avoid queues and extra services at
  the cost of a request per viewer per second.
