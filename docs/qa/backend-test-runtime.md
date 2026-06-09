# Backend Test Runtime Gate

This gate keeps short runner budgets honest without weakening full backend sign-off.

## Commands

- Short runner, intended for 5-minute tool or CI budgets:
  `python -m pytest backend/tests -q --maxfail=1 -m "not slow"`
- Slow diagnostics and timeout-sensitive tests:
  `python -m pytest backend/tests -q -m slow`
- Full backend sign-off, required before release claims:
  `python -m pytest backend/tests -q --maxfail=1`

## Current Split

The `slow` marker is reserved for tests that intentionally wait on subprocess,
thread, cancellation, timeout behavior, or full local diagnostic/evaluation
flows. These tests are still part of full sign-off; the marker only gives short
runners a documented way to avoid false timeouts.

Do not mark a failing or flaky test as `slow` unless the runtime is inherent to
the behavior under test and the slow command is kept green.

## Current Workspace Note

As of the 2026-06-08 development handoff, a focused core backend combo recorded
`183 passed`. Treat that as historical targeted dirty-worktree evidence, not as
the latest mobile/remote-input gate and not as a replacement for full backend
sign-off or a release-candidate `qa:gate`/`release:check` run.

As of the latest 2026-06-09 mobile/remote-input integration, the focused
backend mobile+remote combined run recorded `132 passed`. Scheduler/preflight
targeted checks are support-only development notes unless their exact command
and run log are attached; do not cite an unbound `9 passed` count from this
file. The `132 passed` count supports the mobile/remote contract evidence
tracked in the QA docs, but it still does not prove real-device LAN/WSS,
certificate trust, clean-machine RC readiness, or release sign-off.
