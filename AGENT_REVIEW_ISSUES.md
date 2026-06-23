# Agent Review Issues

Repository: `suli9710/-lengrvis` (local checkout path intentionally omitted)
Started: 2026-06-11 (8-agent cross review)
Cadence: Single review round with dual-coverage matrix.

## Phase 1 Regression Baseline

Historical local run for this review only. Do not treat these counts as current release evidence; see `docs/qa/test-evidence-policy.md` and `docs/release/current-release-evidence.md`.

| Gate | Result |
| --- | --- |
| `python -m pytest backend/tests -q` | Historical 2026-06-11 local run; exact count is preserved in `.tmp/full-test-8agent-regression.log` |
| `npm --prefix desktop run typecheck` | PASS |
| `npm --prefix mobile run typecheck` + smokes | PASS |
| `npm --prefix desktop run smoke:desktop-token` | PASS |

Log: `.tmp/full-test-8agent-regression.log`

## Agent Runs

| Agent | Scope | Status |
| --- | --- | --- |
| agent-1-bugbot-indexer | D1 Indexer primary, D2 cross | completed; 2 Medium findings |
| agent-2-bugbot-db | D2 DB primary, D1 cross | completed; 2 Medium findings |
| agent-3-bugbot-orch | D3 Orchestration primary, D6+D7 cross | completed; 2 Medium findings |
| agent-4-security-core | D4 SecurityCore primary, D1+D8 cross | completed; 0 Medium+ |
| agent-5-security-mobile | D5 MobileRemote primary, D4+D6 cross | completed; 0 Medium+ |
| agent-6-bugbot-desktop-main | D6 DesktopMain primary, D3 cross | completed; 2 High findings |
| agent-7-standards-desktop-ui | D7 DesktopUI primary, D6 cross | completed; 3 Low findings |
| agent-8-standards-tests-docs | D8 TestsDocs + High claim validation | completed; doc drift + test gaps |

## Open Issues

### DESKTOP-001 — Duplicate before-quit cleanup race

- **Severity**: High
- **Location**: `desktop/src/main/main.ts:363-384`
- **Finding**: Second `app.quit()` during async cleanup could start parallel backend/browser teardown.
- **Reviewers**: Agent6 (primary), Agent7 (cross)
- **Status**: **Fixed** — `backendCleanupInProgress` guard added.

### ORCH-001 — Parallel OS step tasks not cancelled on run cancel

- **Severity**: High
- **Location**: `backend/app/orchestration/os_execution_engine.py:490-517`
- **Finding**: Cancelling engine loop did not cancel child `os-step-*` tasks; tools could continue after `CANCELLED`.
- **Reviewers**: Agent6 (primary), Agent3 (cross)
- **Status**: **Fixed** — `CancelledError` handler cancels and gathers child step tasks.

### INDEXER-001 — Short CJK FTS queries returned empty

- **Severity**: Medium
- **Location**: `backend/app/indexer/fts_index.py:427-446`
- **Finding**: Trigram MATCH with zero rows did not fall back to LIKE for 2-char Chinese queries.
- **Reviewers**: Agent1 (primary), Agent2 (cross)
- **Status**: **Fixed** — LIKE fallback when MATCH returns empty and query length &lt; 3.

### DB-001 — FTS trigram migration could drop FTS permanently

- **Severity**: Medium
- **Location**: `backend/app/core/db.py:762-783`
- **Finding**: DROP before CREATE with swallowed `OperationalError` could leave no FTS table.
- **Reviewers**: Agent1 (primary), Agent2 (cross)
- **Status**: **Fixed** — probe trigram support before migration; log warning on failure.

### DB-002 — FTS rebuild crashes init_db on corrupt JSON row

- **Severity**: Medium
- **Location**: `backend/app/core/db.py:828-834`
- **Finding**: `json.loads` on corrupt `indexed_files.data` raised through hot `init_db` paths.
- **Reviewers**: Agent2 (primary), Agent1 (cross)
- **Status**: **Fixed** — skip corrupt rows with warning.

### DB-003 — Settings cache stale after privacy erase

- **Severity**: Medium
- **Location**: `backend/app/llm/registry.py:49-69`, `db.erase_local_user_data`
- **Finding**: `include_settings=True` erase did not invalidate settings TTL cache.
- **Reviewers**: Agent2 (primary), Agent3 (cross)
- **Status**: **Fixed** — `invalidate_settings_cache()` after settings erase.

### ORCH-002 — Timeline reads omit event backfill

- **Severity**: Medium
- **Location**: `backend/app/services/run_service.py:118-123`
- **Finding**: `get_timeline` no longer reconciles; UI can show stale partial events until write-path reconcile.
- **Reviewers**: Agent3 (primary), Agent7 (cross)
- **Status**: **Open (accepted)** — intentional per 2-H6; document UX trade-off.

### ORCH-003 — Future.cancel cannot stop resume engine thread

- **Severity**: Medium
- **Location**: `backend/app/services/run_service.py:264-278`
- **Finding**: Sync resume routes use `concurrent.futures.Future`; `cancel()` does not interrupt thread.
- **Reviewers**: Agent3 (primary), Agent6 (cross)
- **Status**: **Open (follow-up)** — rare path; needs cooperative cancel in engine thread.

### UI-001 — Poll timer not torn down when no active task

- **Severity**: Low
- **Location**: `desktop/src/renderer/App.tsx:860-869`
- **Reviewers**: Agent7 (primary)
- **Status**: Open (follow-up)

### DOCS-001 — Stale vision-bypass narrative in review doc

- **Severity**: Medium (doc accuracy)
- **Location**: `docs/code-review-2026-06-11.md` L59
- **Reviewers**: Agent8 (primary)
- **Status**: **Fixed** — conclusion updated to reflect 3-H1 resolution.

## Re-check Log

- 2026-06-11 Phase 1 regression all green in `.tmp/full-test-8agent-regression.log` (historical local review evidence).
- 2026-06-11 Phase 2 eight agents completed in parallel; dual-coverage matrix satisfied for D1–D8.
- 2026-06-11 Phase 3 triage: 2 High + 4 Medium fixed; 2 Medium + 1 Low deferred as follow-up.
- 2026-06-11 Phase 4 fixes applied: main.ts quit guard, os_execution_engine step cancel, fts_index CJK LIKE fallback, db settings cache invalidation on set/erase, doc L59 drift.
- 2026-06-11 Post-fix targeted tests: settings_cache 6 passed, fts_trigram 1 passed/2 skipped (batch-3 db WIP), execution_engines passed, desktop typecheck PASS.
- Note: `test_runs_api_system_diagnostics_stays_os_local_only` fails in isolation (pre-existing progress assertion); it passed in the historical Phase 1 full-suite run.
