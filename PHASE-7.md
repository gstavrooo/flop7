# Phase 7 — Go-live: deploy on merge, schedule, retention

Read `FLOP7-SPEC.md` section 9 and AGENTS.md. This session covers ONLY Phase 7. Prerequisite: Phase 6's manual pipeline is green and the public URL serves a fresh game.

Do:
1. Extend the deploy workflow triggers: `on: push: branches [main], paths-ignore ["**.md"]` plus `workflow_dispatch` plus `schedule: cron "23 5 * * *"` (daily catch-up for Dependabot bot merges — they raise no push event).
2. Verify squash-only linear history + required checks (all section 8 gates) + repo setting "allow auto-merge"; document the per-PR opt-in (`--auto --squash`) in AGENTS.md/README (already normative per spec 9.1).
3. Merge this phase's PR to `main` and observe the deploy run on push: build → test → GHCR → Render exact-tag deploy → live → public smoke. Everything must be green without human infrastructure steps.
4. Image retention: add a final pipeline step (or a small scheduled workflow) deleting GHCR image versions beyond the newest 3 (`gh api` / GitHub REST), so private-package storage stays ~within the free 500 MB.

Definition of Done:
- A push to `main` triggered a fully green deploy run (link it).
- A test Dependabot PR (or simulated one) auto-merged and its change deployed with the next scheduled run (verify the schedule exists; the 24 h catch-up trade-off is documented in README).
- Report ≤5 lines: deploy run link, retention approach, any caveat for the daily schedule.
