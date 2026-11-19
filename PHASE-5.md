# Phase 5 — Full test pyramid & CI gates

Read `FLOP7-SPEC.md` sections 6, 8 and 11 (acceptance list). This session covers ONLY Phase 5: the browser regression suite, i18n static tests and the complete CI gate set. The API/engine test suites already exist.

Build:
1. `tests/browser_checks.py`: Playwright script (no framework needed if the plain-API style from the spec is simpler), launching uvicorn on a free port with a temp SQLite DB; viewport switch mobile 390×844 / desktop; **no screenshots, no traces**. Cover every scenario named in spec section 8's `browser-test` row (hot-seat flow, space-next, winner scale, rematch, colour coding, deck counter, tab title, solo bot with explicit next + thinking badge, bot chip in box, participant preview across names/bot/language, card explanation, language/theme/rules round trip, dark-mode neutral history, two devices live, dock touch targets, constant waiting bar, accessibility basics, blocked/corrupt storage, malformed invites, legacy text robustness).
2. `tests/test_i18n.py`: key parity de/en; every key used in HTML/JS exists; every API error text (extract from sources statically, e.g. AST scan for the error strings of spec 2.2) is translated in both languages.
3. Extend `ci.yml` to the full gate set of spec section 8: `test`, `container-test` (`scripts/test-image.sh` with container smoke + Trivy HIGH/CRITICAL fixable fail + ignore file), `browser-test` (**runner pinned to ubuntu-24.04** with the explanatory comment), `lint` (ruff, shellcheck via container, actionlint, zizmor offline), and `gitleaks` for secrets (pin the action SHA). All required.
4. `scripts/smoke.sh` (deploy smoke, spec 9.2 step 6) and `scripts/test-image.sh` if not yet present.

Definition of Done:
- All gates green on a PR run; every browser scenario from the list exists as a named test.
- CI runtime for browser-test < 6 min.
- Report ≤5 lines: scenario count, uncovered items (if any, with reasons), CI link.
