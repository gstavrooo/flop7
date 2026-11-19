# Repository conventions

These conventions are normative for every agent session in this repository. They follow
sections 9.1 and 10 of `FLOP7-SPEC.md`.

## Session discipline

- Work on one task per session. Read `AGENTS.md` and the task's acceptance criteria first.
- Repo files are the source of truth; when in doubt, re-read them instead of assuming.
- Hand off only branch/PR/blocker, in at most 10 lines.
- Status reports are at most 5 lines: change, PR link, verification.
- PR descriptions are at most 12 lines.

## Workflow

- Trunk-based: squash-merge only, linear history, no direct pushes to `main`.
- After opening a PR, use `gh pr merge <nr> --auto --squash` when opting into auto-merge. This private
  free-tier repo cannot enable GitHub's "Allow auto-merge" setting or required status checks, so the
  command merges as soon as all CI gates on the PR are green.
- Review means a human reads the diff before merging — or deliberately enables auto-merge.
  Green checks alone are not a review.
- Fix or answer every review comment, then resolve it.
- Verify a fact once. Do not stream `gh run watch`; use compact status scripts, poll every
  60–90 s, and show logs only on failure.
- Bundle small related fixes into one commit.
- Every non-trivial logic — a branch, loop, parser, or money/security path — leaves a runnable
  check behind.

## Code and docs

- Deliberate shortcuts carry a `# tech-debt:` comment naming the ceiling and the upgrade path,
  e.g. `# tech-debt: one poll per viewer per second; move to a DB-notify mechanism at scale.`
- No company data, secrets or credentials in the repo; CI verifies this.
- Token/cost claims come only from real harness numbers, never from inference.
- Keep documentation honest: describe what exists now, not what is planned as if it were shipped.
