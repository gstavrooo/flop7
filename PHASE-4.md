# Phase 4 — Frontend core (setup, table, live updates)

Read `FLOP7-SPEC.md` sections 6.1–6.7 and 4.1. This session covers ONLY Phase 4: the complete vanilla-JS frontend wired to the API. No Playwright yet (phase 5), but keep the DOM stable/testable (ids per spec examples).

Build `app/static/`: `index.html`, `app.js`, `i18n.js`, `theme.js`, `style.css` (+ vendored minimal QR library) implementing:
- i18n dictionary de/en with EVERY string (spec 6.9; keys cover all UI incl. status/badges, invites, errors, card explanations, rules), language button cycling + persistence + full live re-translation.
- topbar (6.3): wordmark + tagline + icon-only buttons, aria-labels translated, sound aria-pressed, theme tri-state pre-paint.
- setup panel (6.4): names input, player-box with the 🤖 Bot chip inside, hotseat/bot checkboxes, submit, live participant preview, role=alert errors, validation.
- table + dock (6.5): per-player cards/badges/scores/progress, colour-coded statuses, prompt line, hit/stay buttons, waiting bar with constant height, deck counter, round break with summary + explicit next (enabled for any token holder), winner banner scaled by margin, rematch (names/bot/seats preserved).
- 409 UX (6.5): toast + state reload; SSE live view (6.6): EventSource reconnect, 4 s connection chip, stream close on leave/new game, tab-title turn signal, WebAudio beeps (6.6 classes), toast usage per originals.
- invites & storage (6.7, 5): fragment URLs, strip after read, copy/QR/share per foreign seat, unsent invites survive reload, validated + namespaced storage, degrade gracefully on blocked/corrupt storage.
- rules dialog `?`, card explanation dialog, keyboard guards (6.8).
- Play one full hot-seat game and one solo-bot game manually at the end as a functional check.

Definition of Done:
- App serves via uvicorn; a complete 2-player hot-seat game and a solo game against the bot work end-to-end incl. invites between two browser profiles.
- ruff green; no console errors; no hardcoded UI strings outside the dictionaries.
- Report ≤5 lines: which spec items are done/not yet, manual test result, deviations.
