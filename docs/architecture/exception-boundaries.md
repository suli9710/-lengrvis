# Exception Boundaries

`broad-exception-boundary` marks a deliberate recovery boundary where arbitrary
runtime failures are converted into structured status, audit, fallback, or
task-failure records.

Use this marker only when narrowing the caught exception would make the product
less safe or less diagnosable, such as process lifecycle drains, plugin/runtime
adapters, optional native dependencies, user-provided integrations, and UI
error-display paths.

Rules:

1. Prefer specific exception classes when the failure modes are known.
2. Re-raise cancellation/interruption exceptions before a broad boundary.
3. Do not silently swallow errors; record, return, or surface a safe status.
4. JavaScript `catch` clauses are broad by language design, so they still need
   the marker when they catch `error`, `err`, or `e`.
5. `python scripts/check_exception_boundaries.py` fails when a broad boundary is
   unmarked.
