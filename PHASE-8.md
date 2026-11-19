# Phase 8 — Polish: acceptance review, diagrams, README, portfolio readiness

Read `FLOP7-SPEC.md` section 11 (the acceptance checklist) and AGENTS.md. This session covers ONLY Phase 8: verifying the finished project against the spec and completing the documentation. Fix findings directly (small, tested changes only) — no new features.

Do:
1. Walk the entire acceptance list AC-01..AC-35. For each item: tick it only if a test, a workflow run or a direct check proves it. Log every miss, fix it, re-run the proving test. Do not soften the spec.
2. Documentation in `README.md`: complete and honest — product, rules summary, architecture diagram, deployment pipeline diagram (draw both fresh as simple SVGs or graphs, e.g. mermaid; spec AC-34), local dev & test instructions, hosting trade-offs (ephemeral data, cold starts, manual first deploy, retention), security model section, and a "what I would change at scale" section (poll-per-viewer SSE → DB notify, durable storage options).
3. Repo hygiene: run the secret scanner over full history; remove dead files/branches; rewrite the "Trade-offs" section to match today's reality; make sure AGENTS.md still describes actual practice.
4. Portfolio readiness (repo is still **private**): tag a release `v1.0.0` with concise release notes; add an LICENSE file only after the owner decided about publishing (ask; default: none yet).

Definition of Done:
- All 35 acceptance items ticked with evidence in the report (one line each, max 35 lines).
- Two diagrams in README render correctly; secret scan green on full history; release tagged.
- Report: the ticked checklist + remaining risks (if any) + a 5-line "demo script" (how to show the app in 60 seconds when someone asks).
