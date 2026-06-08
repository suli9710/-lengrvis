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
