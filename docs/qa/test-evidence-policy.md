# Test Evidence Policy

This document prevents stale test-count drift across product, release, and review docs.

## Rules

- Do not describe a hard-coded test count as "latest" unless the entry also includes the exact command, date, commit or workspace label, and log or CI artifact.
- Prefer release evidence files over prose counts. The current release source of truth is `docs/release/current-release-evidence.md`.
- Historical review docs may keep old counts only when clearly labeled as historical and scoped to that review run.
- Dirty-worktree counts are development evidence only. They are not release-candidate sign-off, clean-machine validation, real-device evidence, or external review.
- Targeted counts such as `132 passed` or `53 passed` must be named as targeted suites and must not be added together or compared with full-suite counts.

## Recommended Wording

Use:

> Historical local run, command-bound evidence: `<command>` on `<date>` recorded `<result>`. This is not current release evidence.

Use for current release state:

> See `docs/release/current-release-evidence.md`; do not infer current pass counts from historical productization notes.
