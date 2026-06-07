# Mavris QA Consolidated Report

Date: 2026-06-05

## Verdict

Not ready to ship yet. Broad user-style QA coverage is in place, but release should be blocked by backend test failures, core chat/offline behavior, missing release artifacts, and mobile packaging health issues.

## Coverage Completed

- Backend gate: `scripts/run_tests.ps1` reached pytest and failed with 32 failures out of 1219 collected tests. Reported result: 1186 passed, 1 skipped, 32 failed.
- Desktop gates: typecheck, build, and smoke passed.
- Mobile gates: typecheck, `smoke:token`, `smoke:remote-input-grant`, and Android Metro export passed.
- Browser UI: 18 nav pages tested on desktop and mobile. All rendered, clicked, and scanned without horizontal overflow, bad tokens, failed requests, or console errors.
- Hidden UI views: `browser`, `memories`, `safety`, `agentOps`, `agents` tested on desktop and mobile at the standard Vite port. All rendered without layout or network failures.
- API GET probe: 66 safe GET endpoints tested. 59 returned 2xx, 0 returned 5xx, expected 401/422 cases behaved as guarded paths.
- Safe POST/dry-run API probe: 17 cases tested. 15 returned 2xx, 2 expected non-2xx cases returned 401/404.
- WebSocket QA: desktop notification/task/run streams, mobile pairing/mobile streams, and `/api/ws/browser-host` covered.
- Security negative QA: 30 HTTP/WebSocket auth-boundary cases passed in an isolated temp data directory.
- Startup QA: `scripts/start_app.ps1 -SkipInstall -CheckOnly -Desktop` passed. `Start-Mavris.cmd` launched backend, frontend, and Electron successfully.
- Packaging smoke: build verification gate smoke, bundled Ollama verification smoke, and portable path-safety smoke passed.
- Dependency/audit checks: desktop production audit passed with 0 vulnerabilities; desktop full audit reports 11 high severity dev dependency issues; mobile audit was clean during install.

## Blocking Findings

### P0 Backend Automated Gate Fails

`scripts/run_tests.ps1` fails during backend pytest with 32 failing tests. Failure clusters include approval binding, execution engines, orchestrator action routing, parallel steps, perception integration, runtime safety supervision, session context, task recordings, and tool search.

Impact: the main release test gate is red before desktop/mobile gates can run through the top-level script.

### P0 Core Chat Flow Can Become Unusable

The frontend workspace refresh depends on `/api/tasks`. In cold-path QA, `/api/tasks` took about 37.5s for a 59KB response while the frontend request timeout is 30s. The UI then shows service offline text and the home send button stays disabled even after entering text.

Impact: a normal user can land on the home screen and be unable to send a message even though `/api/chat` works with the desktop token.

### P0 Current Release Artifacts Are Missing

`scripts/build_all.ps1 -VerifyOnly` fails because the expected release outputs are absent, including `dist`, `dist/backend.exe`, portable app directory, portable backend, renderer resources, portable zip, and self-extracting exe.

Impact: current workspace cannot pass release artifact verification without a full packaging build.

## High Priority Findings

### P1 Slow or Timing-Out Endpoints

- `/api/tasks`: timed out in the 12s API probe and caused the frontend offline failure in the longer UI flow.
- `/api/files/duplicates`: timed out in the 12s API probe.
- `/api/ui-automation/windows`: returned 200 but took about 6.8s.
- `/api/settings/local-llm/health`: returned 200 but took about 5.1s.

### P1 Desktop Startup Logs Browser Host Runtime Error

Normal `Start-Mavris.cmd` startup opened Electron, but desktop stderr logged:

`ReferenceError: WebSocket is not defined`

Trigger path: `BrowserHostWebSocketBridge.send` during `mavris:browser-host:hide`.

Impact: the desktop shell starts, but browser-host behavior is throwing in the main process during normal launch.

### P1 Mobile Expo Doctor Fails 6 Checks

`npx expo-doctor` in `mobile` reports 15/21 checks passed and 6 failed:

- `.expo/` is not ignored by Git.
- Doctor did not detect a lock file, although `mobile/package-lock.json` exists.
- `mobile/app.json` schema rejects `android.usesCleartextTraffic`.
- `mobile/metro.config.js` overrides `resolver.nodeModulesPaths` without all Expo defaults.
- `lucide-react-native` requires missing peer dependency `react-native-svg`.
- Expo SDK dependency mismatches: `@react-native-async-storage/async-storage`, `expo`, `expo-notifications`, and `react`.

Android bundle export still passed, but these are release-readiness risks.

### P1 Audit Chain Verification Reports Hash Mismatch

`/api/audit/verify` and `/api/audit/verify-chain` return HTTP 200 but body reports `ok:false` with `prev_hash_mismatch`.

Impact: the audit endpoint is reachable, but integrity verification is failing.

### P1 Database Lock Errors During Runtime Event Storage

Backend QA logs include `sqlite3.OperationalError: database is locked` while storing perception observations and publishing environment events to AgentBus.

Impact: runtime observations/events can be dropped under concurrent activity.

## Medium Priority Findings

### P2 Browser Host WebSocket Alias Inconsistency

`/api/ws/browser-host` connects and responds, while bare `/ws/browser-host` rejects with 403. This is different from several other desktop WebSocket routes that work under both bare and `/api` prefixes.

### P2 Local Provider Self-Probe Generates 401s

Safe POST QA showed `settings/test-llm-provider` returns `ok:false` because it points to `http://127.0.0.1:8000/v1/chat/completions` and gets 401. Runtime logs also show many `/v1/embeddings` 401s.

Impact: local/provider health surfaces can look broken or noisy when configured against the app's own protected API.

### P2 WebSocket Query Token Attempts Are Access-Logged

Security QA confirms query-token WebSockets are rejected, which is good. However, Uvicorn access logs include the full rejected request URL before redaction. Generated QA logs were manually scrubbed.

Impact: if a buggy client sends tokens in query strings, rejected tokens may still be written to access logs.

## Passing Evidence

- UI nav report: `.tmp/qa-ui-nav-report.json`
- Hidden UI report: `.tmp/qa-hidden-ui-5173-report.json`
- API GET report: `.tmp/qa-api-get-report.json`
- Safe POST report: `.tmp/qa-safe-post-report.json`
- WebSocket report: `.tmp/qa-websocket-report.json`
- Security negative report: `.tmp/qa-security-negative-report.json`
- UI screenshots: `.tmp/qa-screens/`
- Hidden view screenshots: `.tmp/qa-hidden-5173-screens/`
- Mobile Android export: `.tmp/qa-mobile-export/`

## Suggested Fix Order

1. Fix `/api/tasks` latency and the frontend offline/send-disabled recovery path.
2. Bring backend pytest back to green.
3. Fix desktop BrowserHost main-process `WebSocket is not defined`.
4. Resolve mobile Expo Doctor release blockers.
5. Fix audit chain verification or repair/reset invalid local audit state.
6. Address database lock handling for perception/event writes.
7. Run full packaging build, then rerun `scripts/build_all.ps1 -VerifyOnly`.
