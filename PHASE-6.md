# Phase 6 — Registry & hosting pipeline (GHCR + Render, manual dispatch)

Read `FLOP7-SPEC.md` sections 3, 9.2, 9.3 and AGENTS.md. This session covers ONLY Phase 6: deploy pipeline **manual-only**. Do not flip push triggers (that is Phase 7).

Build:
1. `Dockerfile` per spec section 3 (current-stable official python slim base pinned **by digest**; non-root; hashed `pip install --require-hashes`; pip removed afterwards; `PORT` env default 8080; SQLite + `MIGRATE_ON_START=1` defaults baked in).
2. `.github/workflows/deploy.yml` (or `deploy-render.yml`; pick `deploy.yml` — there is no legacy here) per spec 9.2 steps 1–7: build → test-image → push GHCR (`permissions: packages: write`, tag = commit SHA) → Render API create-or-patch + explicit deploy trigger of the exact tag → poll to `live` → smoke the public URL; **only `on: workflow_dispatch`**; concurrency group; pass the URL via outputs/env, never templated into `run:`.
3. `.github/dependabot.yml` (groups: pip + github-actions (+ docker digest updates if configured)) and the small auto-merge workflow for **patch/minor only** (squash), per spec 9.3.
4. User manual steps (ask the user to do them now, verify presence of the secret before the first run): Render account (free, Frankfurt), API key → repo secret `RENDER_API_KEY`; private-image option: Render registry credential with PAT `read:packages` (recommended while the repo is private).
5. Run the workflow manually on `main`; verify the public URL end-to-end (always fresh instance, ephemeral DB), including the smoke steps and a real browser round.

Definition of Done:
- One green manual run of the full pipeline ended with the public URL's smoke test passing; service region Frankfurt, plan free.
- The README documents the pipeline, the Render setup prerequisites, the image retention approach (~2–3 versions kept: implement or document a small cleanup step).
- Report ≤5 lines: service URL, run link, image size, deviations from the spec.
