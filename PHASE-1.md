# Phase 1 — Repository foundation & toolchain

You are building the project described in `FLOP7-SPEC.md` (read it fully first; re-read sections on demand). This session covers ONLY Phase 1. Work in the repo root. Prefer the simplest boring solution (see spec section 1).

Scope:
1. `git init`, default branch `main`, first commit by you (author: the repo owner's identity configured in git).
2. Read `FLOP7-SPEC.md` sections 1, 3, 8, 9.1, 10 and create:
   - `README.md` skeleton: project one-liner, stack, run/dev/test instructions (fill honestly as the project grows), a "Trade-offs" section (already list: ephemeral data on free hosting, cold starts, no auto-rollback).
   - `AGENTS.md` with the conventions from spec section 10 (they are normative for every later session).
   - `requirements.in` / `requirements-dev.in` (fastapi, uvicorn, sqlalchemy, alembic / pytest, ruff, pip-tools, playwright pinned to a current stable version) and compile BOTH to hashed lockfiles with pip-tools (`--generate-hashes`).
   - `pyproject.tom` with ruff configuration (line length 110, sensible defaults; format + lint).
   - `.gitignore` (venv, caches, `.playwright`, `*.db`).
3. CI skeleton `.github/workflows/ci.yml`: on pull_request, jobs `lint` (ruff check + ruff format --check) and `test` (install hashed dev lockfile, `python -m pytest -q` — no tests yet? then assert the pytest run succeeds with zero tests) — follow the hard rules from spec section 8 (pinned action SHAs, `persist-credentials: false`, least-privilege permissions). Add `README` note that other gates arrive in later phases.
4. Commit in small steps with clear messages; push if the user has a remote (ask).

Definition of Done (verify before reporting):
- ruff check + ruff format --check green; pip install from hashed lockfiles works.
- CI run on this branch is green.
- README/AGENTS rendered readable; no placeholder sections left.
- Report in ≤5 lines: what exists, CI link, deviations from the spec (with reasons).
