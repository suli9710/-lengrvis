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

The checker also prints a reviewed-boundary summary when it passes:

- scanned source files;
- total marked broad boundaries;
- marked broad boundaries by area: backend, desktop, mobile, and other.

Use this summary as a maintenance-complexity trend, not as a quality score. A
new broad boundary should either replace a less safe failure mode, sit at a real
runtime/process/integration edge, or come with a follow-up to narrow the catch
once the concrete failure classes are known.

Record the summary alongside source-size output when reviewing large-file or
module-complexity work. The source-size report includes total source lines, p95
file size, largest file, and per-area line totals, so a useful paired trend is:
large files shrink or stabilize while broad boundaries move toward real
runtime/process/integration edges.
