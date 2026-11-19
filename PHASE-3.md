# Phase 3 — API, data model, migrations, security

Read `FLOP7-SPEC.md` sections 4, 5, 7 and 2.4/2.5. This session covers ONLY Phase 3: FastAPI application + persistence + security. No frontend yet (StaticFiles may mount an empty dir).

Build:
1. `app/db.py`: SQLAlchemy Core engine from `DATABASE_URL` (default `sqlite:///flop7.db` for local; CI passes its own path), tables per spec section 7 (`games`, `seats`, `moves`).
2. Alembic: `app/migrations/` with one initial migration creating all three tables (working `downgrade()`); `app/migrate.py` applies `upgrade(head)` on boot when `MIGRATE_ON_START=1`; refuse a legacy non-migrated DB loudly (spec section 7).
3. `app/main.py` per spec section 4.1–4.4:
   - `POST /api/games` (validation 2–6 incl. bot, 201 + one-time seat tokens),
   - `POST /api/games/{id}/{hit|stay|next}` and `/target` with `X-Seat-Token`, seat-token sha256 + `hmac.compare_digest`, version-guarded UPDATE (rowcount 0 → 409 "game changed in the meantime, reload and try again"), moves audit insert,
   - `GET /api/games/{id}` + snapshot-driven bot due-logic (spec 2.4: persisted `bot_due_at`, 1.5 s, one commit per decision, SQLite-conflict safe),
   - `GET /api/games/{id}/events` SSE (~55 s cap, retry:1000, one data line per version),
   - `GET /api/health`,
   - security headers middleware + revalidation-caching headers (spec 4.4),
   - public view per spec 2.5/4.3 (deck hidden, deck_count, scores, targets, structured events).
4. `tests/test_api.py` + `tests/test_migrate.py` per spec section 8 (all named cases: status matrix, stale write, solo bot server-side, one committed decision for concurrent viewers, moves persisted, tokens once + digest, header tests, migration round-trip, legacy refusal).

Definition of Done:
- pytest + ruff green; endpoints verified via the tests (no manual clicking).
- No token in any log line; no secret in code.
- Report ≤5 lines: endpoint list, test count, deviations/ambiguities resolved.
