# Agent Review Issues

Repository: `C:\Users\Suli\Desktop\mavris`
Started: 2026-06-08
Cadence: Re-check every 10 minutes. Remove an issue when the current worktree proves it is resolved.

## Agent Runs

| Agent | Scope | Status |
| --- | --- | --- |
| review-backend-core-security | `backend/app`, backend entrypoints, privacy/security-sensitive paths | completed: `019ea638-b69a-7f73-84c7-3c4d004568c7`; 1 confirmed issue |
| review-desktop-electron-ui | `desktop/src`, desktop Electron/preload/renderer boundaries | completed: `019ea638-caa7-7621-9a57-a23fffca9d99`; 4 confirmed issues |
| review-mobile-shared-api | `mobile`, pairing/remote input flows, shared contracts | completed: `019ea638-deb2-7633-92cb-7f947d942ab1`; 4 confirmed issues |
| review-packaging-release-docs | root scripts/config/docs/release gates excluding vendored code | completed: `019ea638-f2c7-7c82-a438-2b5c15e6387a`; 6 confirmed issues |
| test-backend-suite | backend pytest and Python test entrypoints | completed: `019ea639-06ca-7681-bbfe-2afa3e739fd2`; full backend suite passed, 1357 passed and 1 skipped in 336.14s |
| test-frontend-mobile-suite | desktop/mobile typecheck and JS smoke commands | completed: `019ea639-1ad4-7461-a6c3-7d6453a80244`; desktop typecheck, mobile typecheck, and mobile token/task-companion/remote-input smokes passed |

## Open Issues

### TEST-BACKEND-001 - P3 - Backend suite exceeds common 5-minute timeout

- Location: `C:\Users\Suli\Desktop\mavris\backend\tests`
- Problem: The full backend test suite can pass but exceed a 5-minute runner timeout.
- Evidence: The backend test agent's first `python -m pytest backend/tests --maxfail=20` run timed out around 303 seconds, while the follow-up full run completed successfully with `1357 passed, 1 skipped in 336.14s`. Latest re-check: `python -m pytest backend/tests -q --maxfail=1 -m "not slow"` was still running after 326.4 seconds and had only reached 95% before the review-owned process was stopped; the earlier slow subset check passed with 7 passed and 1363 deselected in 4.38s.
- Impact: Default 5-minute CI/tool runners can falsely report backend failure despite a green suite.
- Resolution check: The backend suite completes under the intended CI/tool timeout, or slow diagnostics/integration tests are split or marked with a documented longer command.

## Re-check Log

- 2026-06-08 initial file created; six agents pending.
- 2026-06-08 frontend/mobile test agent completed with no confirmed issues.
- 2026-06-08 local targeted backend regression check passed: `python -m pytest backend/tests/test_rollback.py backend/tests/test_path_security.py backend/tests/test_app_skill_packages.py backend/tests/test_start_app_script.py -q` reported 50 passed, 1 skipped.
- 2026-06-08 desktop review agent completed; four confirmed unresolved issues added after local code verification.
- 2026-06-08 backend review agent completed; `dev.shell_readonly` Windows built-in failure reproduced locally and added.
- 2026-06-08 mobile/shared API review agent completed; four pairing/remote-input findings verified against mobile and backend code/tests and added.
- 2026-06-08 backend test agent completed; full backend suite passed with 1357 passed, 1 skipped in 336.14s; timeout-risk issue added.
- 2026-06-08 release/packaging review agent completed; six script/release-gate issues verified and added.
- 2026-06-08 first re-check completed; all 16 open issues remain reproducible/current, so no issues were removed.
- 2026-06-08 16:11 +08:00 re-check removed DESKTOP-001, DESKTOP-002, and DESKTOP-003 after confirming synchronous prompt application, settings-save exception rollback, and `getFileIcon` reveal/document grant enforcement; `npm --prefix desktop run typecheck` and `npm --prefix desktop run smoke:ipc` passed. 13 open issues remain.
- 2026-06-08 16:14 +08:00 re-check removed DESKTOP-004 after `desktop\package.json` declared `esbuild` and `npm --prefix desktop ls esbuild --depth=0` showed `esbuild@0.25.12`; BACKEND-001 was retained but narrowed to the remaining real `dir` failure in the default Windows environment. 12 open issues remain.
- 2026-06-08 16:33 +08:00 re-check removed BACKEND-001 and MOBILE-001 through MOBILE-004 after current-worktree verification: real Windows `dev.shell_readonly` `dir`, `echo hello`, and `type package.json` returned structured success; targeted backend developer/LAN TLS/mobile approval tests reported 56 passed; `npm --prefix mobile run typecheck`, `smoke:token`, `smoke:task-companion`, and `smoke:remote-input-grant` passed; `npm --prefix desktop run typecheck` and `smoke:ipc` passed. Added TEST-BACKEND-002 because `backend/tests/test_guardian_backend.py::test_guardian_mobile_device_list_only_returns_calling_device` and the `not slow` backend runner now fail on missing pair-code TLS setup. 8 open issues remain.
- 2026-06-08 16:42 +08:00 re-check removed TEST-BACKEND-002 and RELEASE-001 through RELEASE-006. Evidence: guardian pairing targeted test passed; `scripts\build_all.ps1` was verified in an isolated stub workspace to exit 47 after a failing `run_tests.ps1` without creating the backend build marker; `scripts\build_all_verify_gate_smoke.ps1`, `scripts\prepare_ollama_release_smoke.ps1`, `scripts\verify_packaging_ollama_smoke.ps1`, and `scripts\create_self_extracting_exe_smoke.ps1` passed; backend packaging dependency tests passed; setup_dev now installs mobile dependencies; packaging verifier copy now says structural PE/header/resource preflight without GUI launch. Retained TEST-BACKEND-001 because `python -m pytest backend/tests -q --maxfail=1 -m "not slow"` still timed out after about 308 seconds, though the slow subset passed. 1 open issue remains.
- 2026-06-08 16:50 +08:00 re-check retained TEST-BACKEND-001. A background `python -m pytest backend/tests -q --maxfail=1 -m "not slow"` run was still active at 326.4 seconds, with stdout showing progress through 95% and no stderr; the review-owned process was stopped after exceeding the 5-minute budget. 1 open issue remains.
- 2026-06-08 16:50 +08:00 cadence updated from every 5 minutes to every 10 minutes after the active goal objective changed; the thread heartbeat automation was updated to `FREQ=MINUTELY;INTERVAL=10`.
