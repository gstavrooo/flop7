# Flop7 — Rebuild package (read me first)

This folder contains everything to rebuild Flop7 from scratch on a private machine:

| File | Purpose |
|---|---|
| `FLOP7-SPEC.md` | Full product & engineering specification (the single source of truth). |
| `PHASE-1.md` … `PHASE-8.md` | One kickoff prompt per build phase. Run them in order, one fresh agent session each, and check the Definition of Done before starting the next. |

## How to use

1. Create a **private** GitHub repository named `flop7` under your personal account.
2. For each phase: open a **fresh** agent session on your private machine, paste the phase prompt (it points to `FLOP7-SPEC.md`), let it work, verify its report against the phase DoD, commit.
3. Manual steps only you can do:
   - Phase 6: create the Render account (free tier, region Frankfurt) and the API key; set the repo secret `RENDER_API_KEY`. Private GHCR images additionally need a Render "Registry Credential" (GitHub PAT with `read:packages`).
   - Phase 7: flip the deploy triggers to go live.
4. Keep the repository private until the IP question with your employer is settled; publishing is a separate, later decision.

## Principles baked into the spec

- Target-architecture only: Render free tier + GHCR + SQLite. No cloud SQL, no Terraform, no legacy migration.
- Functional requirements, not copied code: every lesson from the original project is phrased as a testable requirement.
- Small, boring, fully automated: every merge gate and deploy step runs without human infrastructure work.
