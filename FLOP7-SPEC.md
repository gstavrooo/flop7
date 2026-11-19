# Flop7 — Product & Engineering Specification (v1.0)

Rebuild specification for a private repository. This document is the single source of truth:
it describes **what** to build and **which properties** the result must have. It intentionally
contains no code. Implementation choices follow the constraints below; where the spec is
silent, prefer the simplest boring solution and record the decision in the README.

---

## 1. Product overview

Flop7 is a multi-player, push-your-luck card game played in the browser. 2–6 participants,
hot-seat on one device or remotely via invite links, optionally against one server-side bot.
Live updates without manual refresh. One public HTTPS URL hosts the game; access to the app
is open by design, while **each game is protected by per-seat secrets** (see section 5).

Non-goals: accounts/login, persistent accounts or profiles, payments, chat.

Quality bar: production mindset — CI gates, security headers, one runnable check per
non-trivial logic, honest docs, no over-engineering.

## 2. Game rules (exact values, normative)

A game consists of rounds. Players draw ("hit") until they bank ("stay"). The game ends when
one player reaches a total of **200** or more and is the sole leader (ties at 200+ play
another round; the round result is still scored).

### 2.1 Deck (exact composition, total 87 cards)

- Number cards `0`..`12`: one `0`, one `1`, two `2`s, three `3`s … twelve `12`s (78 cards).
- Modifiers: `+2`, `+4`, `+6`, `+8`, `+10`, `x2` (one each).
- Action cards: `freeze`, `flip3`, `second` (three each).

The deck is shuffled once and consumed; when empty, the discard pile is reshuffled into the
deck (logged as `reshuffle`). If both are empty, hitting fails with the illegal-move message
"no cards left".

### 2.2 Turn flow

- Rounds: every player starts each round `active` with empty hand (`numbers: []`, `mods: []`,
  `second: false`). The starting seat rotates by round: round r starts at seat `(r-1) % n`.
- On your turn: `hit` (draw one card) or `stay` (bank). You may hit any number of times.
- **Bust**: drawing a number you already hold ends your round: 0 points this round, the
  duplicate card goes to the discard pile, status becomes `bust`, turn passes.
  If you hold a `second` protection, it is consumed instead (discarded, card logged) and
  you keep playing — one protection per round.
- Modifiers apply immediately and accumulate: `+N` adds N; **`x2` doubles the sum of your
  numbers (modifiers added afterwards, in the order: numbers², then `+N`)**.
- **Flop7**: holding **7 different numbers** at once earns **+15** points and **ends the
  round for everyone immediately** (your drawn hand is kept visible, others finish as-is).
- Round end (all non-active): each player's round score is added to their total; the round
  result (per player: points, status, flop7 flag) stays visible until a human starts the
  next round. Round action `next` is required and explicit; it is legal for any player who
  holds at least one seat token, any time after the round ended (prevents games stalling).
- Illegal moves raise typed errors with **stable, human-readable English messages**
  (they become API 409 details and must be translatable): `game is over`,
  `start the next round first`, `round is still running`, `not your turn`,
  `choose a target first`, `nothing to target`, `not your decision`, `invalid target`,
  `no cards left`, `too many automatic moves`, `a game needs 2 to 6 players including the optional bot`.

### 2.3 Action cards

- `freeze`: the drawer chooses a target (another active player); the target banks their
  current round score and sits out the rest of the round (status `stayed`). Exception:
  if the drawer is the only active player left, they freeze themselves. Freeze means the
  round score is **added** to the target's total at round end.
- `flip3`: the target draws up to three cards following all rules (may bust mid-way and
  stop); **action cards drawn during a flip3 are queued** and — if the target is still
  active and the round not over — played by the target afterwards. Lost on bust/round end.
- `second`: grants one-time bust protection. Drawing a `second` while already protected
  forces the drawer to **give it to another active player without protection** (queued as
  a decision, not immediately resolved). Protection transfers, never duplicated.
- **Pending decisions**: action cards needing a target land in a pending queue; the player
  recorded as `by` must call `target`. Cards that have **no or exactly one** valid target
  are resolved automatically (given to the only option or discarded if none). A pending
  action whose owner is no longer active is discarded. Multiple pending decisions resolve
  in order, stopping at the first that needs a human choice.

### 2.4 Bot (optional, one per game, counts toward the 6-player limit)

- Server-side, appended as last player named `Bot`. Never sees the hidden deck.
- Strategy: with no pending decision — `stay` when the round score is **≥ 18 points** or
  the player holds **≥ 4 numbers**, unless protected by `second`; otherwise `hit`.
- Pending decision: target the strongest opponent by current round score (never itself if
  others are possible).
- **Thinking pause of 1.5 s** between bot decisions, implemented server-side: the due time
  is persisted in the game state (`bot_due_at`), so refreshes, reconnects or multiple
  server instances can never bypass or duplicate it. Live viewers' polling drives due bot
  decisions; **each decision commits separately** (one version increment per bot move).
- Solo play (1 human + bot) must work; the bot needs no client-side credentials or scripts.

### 2.5 Public game view

Every response clients receive contains the full state **except the hidden deck**; instead
a `deck_count` and, per player, derived scores (`score` per player), valid `targets` for a
pending decision, the `version`, and the last ≤ 12 structured events (see 4.3).

## 3. Architecture (target only)

| Concern | Decision |
|---|---|
| Runtime | One FastAPI app serving the JSON API **and** the static frontend (`app/`, no separate web server) |
| Frontend | Vanilla JS + CSS in `app/static/`, **no build step, no npm/node_modules**; one vendored, minimally-sized QR library |
| Database | SQLite file (ephemeral on the host, see below) via SQLAlchemy Core; **Alembic** migrations, applied on application start (`MIGRATE_ON_START=1`) |
| State | The whole game state is one JSON column; no ORM models needed |
| Process | uvicorn, `PORT` from the environment (default 8080), plain `CMD` via `sh -c` |
| Container | Small official Python slim base image, **pinned by digest**; non-root user (`nobody`); pip removed after hashed `pip install --require-hashes`; keep runtime deps minimal (fastapi, uvicorn, sqlalchemy, alembic; no DB drivers beyond SQLite) |
| Dependencies | `requirements.in` + `requirements-dev.in` compiled to hashed lockfiles with pip-tools; Dependabot keeps them current |
| Hosting | Render **free tier**, region Frankfurt, one web service "flop7", image pulled from GHCR |
| Image registry | GitHub Container Registry (`ghcr.io/<owner>/flop7`), pushed by CI |
| Public access | The app has **no login**; anyone with the URL sees only new games they create or are invited to (seat tokens, section 5) |
| Ephemeral data | Free-tier instances lose the filesystem on redeploy/restart/scale-to-zero: game data is **not durable**. Document this in the README as a deliberate trade-off for 0-cost hosting. Spin-down after ~15 min idle: first request cold-starts (~1 min) — the frontend must survive this gracefully (SSE reconnect already covers it) |
| no-Terraform | No IaC: the host is created once via API (CI does it), configuration is code |

## 4. API contract

JSON only. All state-changing endpoints are POST and **must include the displayed game
version**; all errors are JSON `{"detail": "<english message>"}` matching section 2.2 texts.

### 4.1 Endpoints

| Method & path | Semantics |
|---|---|
| `POST /api/games` | Body `{"players": ["A","B", …], "bot": false}`. Names: 1–20 chars, trimmed, unique handling not required; **2–6 players including the optional bot** (else 422). Returns **201** with the public view plus `seats`: `[{seat, name, token}]` — the **only time seat tokens are ever transmitted** |
| `POST /api/games/{id}/hit` / `stay` / `next` | Body `{"seat": int, "version": int}`. Requires header `X-Seat-Token` (else 403, message "this device does not control that seat"). `next` requires the round to be over and the game not yet won |
| `POST /api/games/{id}/target` | Body adds `"target": int`. Same token/version rules; resolves the oldest pending decision |
| `GET /api/games/{id}` | Public view without seat tokens. Also drives due bot decisions (2.4) |
| `GET /api/games/{id}/events?seconds=N` | **SSE** stream (`text/event-stream`): emits `retry: 1000` first, then the full public view as one `data:` JSON line per version change; closes after ~55 s (0 ≤ N ≤ 55); browser reconnects. 404 check before streaming |
| `GET /api/health` | `{"ok": true}` |

### 4.2 Version & move integrity (normative)

- Concurrent safety: the row is locked for the move (SQLite: rely on the version check);
  the write is an `UPDATE … WHERE id = ? AND version = ?` — if it affects **no** row, the
  move is rejected **409** with "game changed in the meantime, reload and try again".
- A stale version in the request (client showed an older state): same 409. A client must
  never be able to draw two cards from one click (repeated request → 409/illegal move).
- Known trade-off to document: with SQLite + polling SSE this needs no queues.

### 4.3 Event log & response payload (normative)

- Games record **structured events** with **monotonically increasing ids** (per game):
  `{id, type, …}` where type ∈ `draws, stays, bust, second, flop7, freezes, flip3, gives, round, win, tie, reshuffle, next` (array capped at the last 12).
  Clients render new events from the structured objects; older plain-text log fields are
  for history only and must never be parsed.
- Public view excludes the deck; includes `deck_count`, per-player score, `targets`,
  `version`, round number `round`, `winner`, `round_over`, pending info, `last_round`
  results while a round break is shown.

### 4.4 Security headers & caching (normative)

- Every response carries locked-down security headers (e.g. `X-Content-Type-Options`,
  `Referrer-Policy`, a strict `Content-Security-Policy` suitable for a no-build static app,
  and frame-busting) — enforced by one test each.
- HTML and static assets: served so the browser **revalidates** (ETag/Last-Modified, no
  heuristic freshness) — so deploys show up without a hard reload, but with cheap 304s. Enforced by one test.

## 5. Security model

- Seats are authorised by **seat tokens**: `secrets.token_urlsafe(16)`, returned **once** in
  the create-game response. Stored **only as SHA-256 hex digests** in a `seats` table
  `(game_id, seat, token_hash)`; comparisons use `hmac.compare_digest` (constant time).
- Token transport to other devices via **invite links**: the URL fragment
  (`…?game=<id>#seat=<seat>&token=<token>`) — fragments are never sent to the server, so
  tokens cannot leak into server request logs. The frontend strips the fragment from the
  address bar/URL after reading it (`history.replaceState`).
- Never log or return tokens after the create response.
- No secrets, keys, or credentials in code or workflow files (CI runs a secret scanner).
- The public URL exposes only: the setup page, health, and games a caller knows (id) — a
  game id alone never grants influence over seats.
- Database: no external network exposure (SQLite file), least-privilege n/a; keep the
  runtime minimal (no pip/ssh in the container).

## 6. Frontend & UX (all normative, testable via browser regressions)

### 6.1 Structure

- `index.html` + `app.js` (logic), `i18n.js` (dictionary), `theme.js`, `style.css`;
  scripts at the end of `<body>`, i18n first (sets `lang` before first paint, respects a
  stored language, falls back to browser language, default English).
- Screens: **setup** panel (new game) ↔ **table** (running game/round break) with winner
  banner; invite dialog; rules dialog; leaving asks for confirmation on an unfinished game.

### 6.2 Design language

Neo-brutalist, bold and playful: thick black borders, hard offset shadows, dotted paper
background, accent colours yellow/pink/cyan/green/violet, `Arial Black` for headings,
monospace body — on all interactive accent surfaces black text/icons (light islands in
dark mode). Light/dark follows `prefers-color-scheme` by default (tri-state toggle auto →
light → dark, persisted, applied pre-paint). Bright accent backgrounds stay readable in
dark mode (documented rule for CSS).

### 6.3 Header / topbar

- Wordmark `FLOP7` (styled with a rotated accent badge), tagline, and **icon-only buttons**
  with translated `aria-label`s: ❓ rules, 🌐 language, 🔊/🔇 sound (with `aria-pressed`),
  ◐/☀/☾ theme (icon = current mode, aria-label names the mode). No visible button text.

### 6.4 Setup panel

- Names input (comma-separated, trimmed, non-empty), a bordered player-box grouping input
  **and** a non-editable `🤖 Bot` chip that appears/disappears with the bot checkbox (bot
  visible *inside* the box, never by editing names), checkboxes: hot-seat (default on),
  "Add a bot" (supports solo play), submit button, live participant preview text under the
  box (`Ann · Bob · 🤖 Bot — 3 players`), singular/plural forms, label adapting to bot set,
  errors in an `role=alert` element both languages.

### 6.5 Game table / dock

- Per player: name (🤖 prefix + ▶ marker for the current turn + "you" unless hot-seat
  controls everyone), total `Total: n / 200`, round score, numbers progress like
  `n / 7 unique numbers`, status badge (`active` implicit, `stayed`, `bust`,
  `thinking …` for bot, `round finished`), colour coding per status (bust red-ish, stayed
  cyan-ish — neutral across themes), newest card and drawn numbers/modifiers visible.
- **Dock pinned to the bottom** on every screen (thumb reach on phones), containing: round
  number + prompt line, the primary action buttons (Hit / Stay / Next round when applicable),
  remaining deck counter; there is a "waiting bar" of **constant height** (never jumps).
- Prompts: your turn / `X is thinking …` / round break with per-player results, spectator
  hint; **explicit "Next round" button** (and Space when it is the only enabled action,
  even while the round-summary is focused).
- Any player holding a seat token may start the next round.
- Stale-move UX: on 409 show the translated message and **reload the state** — never block
  or silently desync; the SSE stream catches up for all viewers.
- Winner banner (+15-cap splash scale with the winning margin), rematch button (new game
  with same names, same bot, **same seats/tokens stay valid for the creator's device**).
- Rules dialog: i18n, keyboard `?`; card explanation dialog (clicking a played card shows
  its effect — every action card explainable in one short sentence).

### 6.6 Live & connectivity

- Live via SSE `EventSource`; auto-reconnect; a "connection lost" chip appears **only**
  after ~4 s without reconnect (normal ~55 s stream rotation must not warn); stream is
  closed when leaving/starting a new game (no leaked connections/timers).
- Tab title signals "your turn" (e.g. prefix ▶) — desktop notification-level affordance.
- Sounds: WebAudio `beep`s per event class (draw, action, bust two-tone, round/win), off
  by default on first visit? — default **on**, persisted, instantly testable, no external
  assets. (Decision: sound toggle is global, not per game.)

### 6.7 Invitations & local storage

- After creating a game: one invite link per foreign seat — copy, QR (generated fully
  client-side via a vendored lib; the token never leaves the browser through a third
  party), and native share where supported.
- Seat tokens for "my" seats are persisted per game id; **not-yet-sent** invites survive a
  reload on the creating device; malformed/invalid fragments or corrupt storage must never
  block joining or playing (validated read + graceful degradation + visible hint that
  storage is unavailable).
- Storage keys are namespaced; a **blocked or corrupt storage** keeps the game usable
  (documented trade-off in code comment: session-only then). Tested by faking storage
  exceptions/corruption in browser tests.

### 6.8 Keyboard & accessibility

- Shortcuts (desktop): `Space`/`H` hit, `S` stay, `1..6` choose target, `?` rules — with
  guards (no shortcuts inside inputs/dialogs; space on focused buttons stays native).
- Accessibility basics enforced by a browser test: all buttons have accessible names
  (may be icon-only **only** with `aria-label`), images have alt text, dialogs are real
  `<dialog>`s, colour status info is not the only signal, the waiting bar and prompt text
  are viewport-visible on mobile.

### 6.9 Internationalisation

- Two dictionaries `de` + `en` with **exactly the same key set** (CI test), including every
  API error message of section 2.2 (CI test extracts error strings from source via AST /
  static scan and compares), every button/label/toast, rules text, and language names.
  No hardcoded UI strings in HTML (use `data-i18n*` attributes) or JS beyond dictionary
  lookups; a language switch re-translates everything rendered so far (including dynamic
  parts like the participant preview, summaries and opened dialogs).
  Default language: browser match, stored choice wins.

## 7. Data model & migrations

| Table | Columns |
|---|---|
| `games` | `id` PK, `state` (JSON text, full game incl. deck), `version` int ≥ 0 default 0, `winner` nullable, `created_at` |
| `seats` | PK (`game_id` FK, `seat`), `token_hash` (sha256 hex) |
| `moves` | `id` PK, `game_id` FK indexed, `seat`, `action` (`hit|stay|next|target`), `target` nullable, `created_at` — audit trail only, never read by gameplay |

- Schema changes **only** via new Alembic migrations with a working `downgrade()`; the app
  applies migrations on boot before serving (start fails loudly, not silently, on an
  inconsistent legacy DB without a migration baseline).
- Invariants enforced in code: version bumps exactly once per committed move; `state`
  is only written through the guarded UPDATE.

## 8. Testing & quality gates (all CI checks are required checks)

| Gate | Content |
|---|---|
| `test` (pytest) | Engine: pure rules incl. every 2.x case (deck composition/duplicates/second/flop7/modifier order/freeze variants/flip3 queue+loss cases/pending auto-resolution/wrong turn/tie/round summary survives reset/bot banking+targeting/reshuffle/unique event ids). API: status matrix 201/403/404/409/422, version/stale-write, one-token-per-seat & deck hidden, solo bot plays server-side, concurrent bot viewers commit exactly one decision, moves persisted, security headers, revalidation headers, migrations downgrade→upgrade round-trip, legacy-DB refusal |
| `container-test` | `docker build`; run the image (SQLite) + HTTP smoke script against it; Trivy scan **fail on fixable HIGH/CRITICAL** (ignore-file for acknowledged, non-fixable findings) |
| `browser-test` | Playwright/Chromium, **no screenshots or traces**; mobile (390×844) + desktop; regression set covering **every 6.x requirement** (see also acceptance list): hot-seat hit/stay/target, space-next-round (also with focused summary), winner banner scaled by margin, rematch keeps bot/names/seat, colour coding, deck counter, tab-title turn signal, solo bot game incl. explicit next round + thinking badge, bot chip inside player box, participant preview tracks names/bot/language, card explanation + progress, language/theme/rules round-trip, dark-mode neutral history, two devices live moves, dock touch targets, waiting bar constant height, accessibility basics, blocked/corrupt storage, malformed invites, legacy texts keep rendering |
| `lint` | ruff check + ruff format --check + shellcheck (scripts) + actionlint + zizmor (workflow security, offline) |
| secret scan | gitleaks over the whole history in PRs (replaces any third-party scanner) |

CI runs on every pull request; defaults: pinned action SHAs, pinned tool image digests,
`persist-credentials: false`, least-privilege `permissions`, macOS-free ubuntu runners,
**browser-test pinned to `ubuntu-24.04`** until Playwright's `--with-deps` supports newer
Ubuntu images (hard-won lesson; keep a comment in the workflow).

## 9. CI/CD pipeline

### 9.1 Repository rules

- Trunk-based: squash-merge only, linear history, no direct pushes to `main`.
- `gh pr merge <nr> --auto --squash` after opening a PR: merges when all required checks
  are green (auto-merge is opt-in per PR; the repo allows it).
- Review = a human reads the diff before merging (or deliberately enables auto-merge);
  green checks alone are not a review.

### 9.2 Deploy (on push to main, `paths-ignore: ["**.md"]`, plus `workflow_dispatch` and a daily schedule ~05:23 UTC so Dependabot bot merges — which do not fire the push event — deploy within 24 h)

One job, `concurrency: deploy`, timeout ~20 min:

1. `docker build` the image, tag = full commit SHA.
2. `scripts/test-image.sh`: container smoke + Trivy (same gate as CI).
3. Push the exact tag to GHCR with the job's `GITHUB_TOKEN` (`packages: write`).
4. Render API (key from secret `RENDER_API_KEY`): find-or-create the service
   (`name: flop7`, `plan: free`, `region: frankfurt`, `autoDeploy: false`,
   image = the pushed tag) — create: `POST /v1/services`; update: `PATCH` the
   `image_url`; then explicitly `POST /v1/services/{id}/deploys?trigger=true` so the
   service always pulls **exactly the tested tag**.
5. Poll `GET /v1/services/{id}` until `status == live` (bounded retries, fail loudly).
6. HTTP smoke script against the returned `serviceUrl` (public): health, frontend served,
   create game, one 403 (foreign token), one 200 stay, one 409 (repeated stale move),
   SSE stream emits the current version. No auto-rollback: failure = red run + fixed forward.
7. Pass values between steps via env/outputs (never templated into `run:` — zizmor rule).

Notes: the deploy workflow initially runs **manual-only (`workflow_dispatch`)**; the
trigger flip (`push: [main]` + schedule) is its own final go-live step. Private GHCR
packages need a Render registry credential (PAT `read:packages`); document it. Cap pushed
image versions (~2–3) by deleting old GHCR versions in the workflow or periodically —
free private package storage is ~500 MB.

### 9.3 Dependabot

- `github-actions` + `pip` (+ docker if used) groups; opens grouped PRs; a small
  workflow auto-merges **patch/minor** only (squash), majors stay manual.
- All pulled actions pinned to commit SHAs, container images to digests.

## 10. Repository & agent conventions (target `AGENTS.md` content)

- One task per session; read `AGENTS.md` + the task's acceptance criteria first; repo files
  are the source of truth; hand off only branch/PR/blocker (≤10 lines).
- Status reports ≤5 lines (change, PR link, verification). PR descriptions ≤12 lines.
- No streaming `gh run watch`; compact status scripts; logs only on failure; verify a
  fact once; poll 60–90 s; bundle small related fixes; one runnable check per non-trivial
  logic (a branch, loop, parser or money/security path leaves a test behind).
- Review = human diff read; every review comment is fixed or answered, then resolved.
- Deliberate shortcuts get a `# tech-debt:` comment naming the ceiling and the upgrade
  path (e.g. "one poll per viewer per second; move to a DB-notify mechanism at scale").
- No company data, secrets or credentials in the repo; CI verifies.
- Token/cost claims only from real harness numbers, never inferred.

## 11. Acceptance checklist (the rebuild is "done" when all boxes tick)

**Game & API**
- [ ] AC-01 exact deck, ordering, reshuffle and "no cards left" (2.1)
- [ ] AC-02 bust + one-shot `second` protection incl. discard (2.2)
- [ ] AC-03 modifier scoring order (numbers ×2, then +N) (2.2)
- [ ] AC-04 flop7 bonus +15 ends the round for everyone (2.2)
- [ ] AC-05 freeze/flip3/second semantics incl. self-freeze, queue-and-play/skip (2.3)
- [ ] AC-06 pending auto-resolution rules (0/1 targets, owner inactive discard) (2.3)
- [ ] AC-07 explicit `next`, round results kept until next round, rotating start seat (2.2)
- [ ] AC-08 win at ≥200 sole leader; ties play on (2.2)
- [ ] AC-09 bot strategy, 1.5 s persisted pause, one commit per decision, plays solo (2.4)
- [ ] AC-10 all 2.2 error texts verbatim and translated (2.2, 6.9)
- [ ] AC-11 version/stale-move 409; double-submit draws exactly one card (4.2)
- [ ] AC-12 tokens: one-time in create response, sha256+hmac, never logged (5)
- [ ] AC-13 public view hides the deck; deck_count/targets/scores/events present (2.5)
- [ ] AC-14 SSE ~55 s + retry:1000 + reconnect chip after 4 s (6.6, 4.1)

**Frontend**
- [ ] AC-15 icon-only topbar with translated aria-labels + aria-pressed sound (6.3)
- [ ] AC-16 player-box groups names + `🤖 Bot` chip; solo play possible (6.4)
- [ ] AC-17 live participant preview incl. language round-trip (6.4)
- [ ] AC-18 dock on every screen, waiting bar constant height, deck counter (6.5)
- [ ] AC-19 statuses/badges/colours theme-neutral; thinking/round-finished texts (6.5)
- [ ] AC-20 round break summary + explicit next incl. space shortcut (6.5)
- [ ] AC-21 409 UX: toast + state reload, never double draws (6.5)
- [ ] AC-22 winner banner scales with margin; rematch keeps seats/bot/names (6.5)
- [ ] AC-23 invite links: fragment tokens, copy/QR/share, strip from URL (5, 6.7)
- [ ] AC-24 storage: namespaced, validated, survivable when blocked/corrupt (6.7)
- [ ] AC-25 keyboard set + guard rules; dialog `?` (6.8)
- [ ] AC-26 accessibility test green (names, dialogs, viewport, non-colour signals) (6.8)
- [ ] AC-27 de/en parity incl. every API error; no hardcoded strings (6.9)

**Platform & delivery**
- [ ] AC-28 security headers + revalidation (no hard reload after deploy) (4.4)
- [ ] AC-29 migrations: boot-time apply, downgrade+upgrade round-trip, legacy refusal (7)
- [ ] AC-30 container: digest-pinned base, non-root, pip removed, hashed deps (3)
- [ ] AC-31 CI: all gates from section 8 green as required checks; squash/auto-merge per 9.1
- [ ] AC-32 deploy: exact tested tag to Render free, poll to live, public smoke (9.2)
- [ ] AC-33 Dependabot grouped + patch/minor automerge; SHAs/digests pinned (9.3)
- [ ] AC-34 README: honest trade-offs (ephemeral data, cold start, no auto-rollback), setup, runbook, architecture & deployment diagrams (drawn fresh)
- [ ] AC-35 `AGENTS.md` as in section 10; repo contains no secrets (scanner green)

## 12. Suggested build order

Eight phases with their own Definition of Done exist as separate prompt documents
(`PHASE-1.md` … `PHASE-8.md`): repo foundation → engine (TDD) → API+security+migrations →
frontend core → full test pyramid + gates → CI/CD to GHCR+Render (manual dispatch) →
go-live trigger flip + retention → polish: docs, diagrams, checklist review.

Finish every phase with: green tests, a tidy commit, a short report. Nothing else.
