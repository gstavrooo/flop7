# Phase 2 — Game engine as pure functions (TDD)

Read `FLOP7-SPEC.md` sections 2 (normative, exact values) and 10. This session covers ONLY Phase 2: the complete rules engine, test-first. No FastAPI, no database, no frontend yet.

Rules of engagement:
- Create `app/engine.py`: pure functions on a JSON-serialisable state dict; a typed `IllegalMove(ValueError)`; random only via an injected `rng` (no module-level randomness). Section 2.2's error strings verbatim.
- Write `tests/test_engine.py` FIRST for every rule, then implement. Cover at least: deck composition; duplicate number busts; second-chance used exactly once and then discarded; flop7 bonus ends round for all; modifier order (x2 numbers first, then +N); freeze self when alone and freeze opponents via target; flip3 on opponents incl. bust mid-way; action cards drawn during flip3 are queued, played after (or lost on bust/round end); pending auto-resolution with 0/1 targets; owner-inactive discards; second-chance-given-away case; wrong turn/start-next-round errors; round summary survives the hand reset; win ≥ 200 sole leader; tie at 200 plays another round; bot banks at ≥ 18 points or ≥ 4 numbers unless protected, picks the strongest target, waits at round break; deck reshuffle from discard; "no cards left"; structured events with unique monotonic ids (last 12).
- Deterministic tests: pass a seeded `random.Random` or an explicit fake rng; never sleep; no network/state.

Definition of Done:
- `pytest -q` fully green, `ruff check` + `ruff format --check` green.
- Every spec 2.x bullet maps to at least one test (say so in the report).
- Report ≤5 lines: test count, coverage of the 2.x list, any spec ambiguity you resolved (and how).
