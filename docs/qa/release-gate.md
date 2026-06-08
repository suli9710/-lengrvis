# Lengrvis Release Gate

Last reviewed: 2026-06-08

This release gate turns the end-to-end acceptance matrix into a repeatable decision checklist. It is intentionally split into fast preflight, demo-before-release readiness, artifact verification, and manual sign-off so development builds do not need release artifacts while release candidates still verify the package that will ship.

## 1. Preflight Gate

Run this before merging release-bound changes:

```powershell
npm run qa:gate
```

When dependency manifests, lockfiles, or backend requirements change, also run:

```powershell
npm run deps:verify
```

Equivalent expanded commands:

```powershell
python -m pytest backend/tests -q --maxfail=1
npm --prefix desktop run typecheck
npm --prefix mobile run typecheck
npm --prefix mobile run smoke:token
npm --prefix mobile run smoke:task-companion
npm --prefix mobile run smoke:remote-input-grant
npm --prefix desktop run smoke:preload-api
npm --prefix desktop run smoke
```

Pass criteria:

- Backend, desktop, and mobile verification commands exit 0.
- Desktop smoke commands exit 0, including document scope, remote input grant, desktop WebSocket token, IPC security, bundled backend env, and browser activity smoke.
- Desktop system diagnostics UI smoke exits 0 as part of `npm --prefix desktop run smoke`: the Vite/Chromium smoke must prove the version/update card stays local-only (`未配置在线更新通道`, `刷新本机状态`, local release notes), refreshing does not call online updater endpoints, diagnostics export copy is visible, and export requires an explicit user click. Log locations are part of the System Info/API surface and should be checked manually or by a future assertion when release notes mention log discovery.
- Backend task evidence privacy remains green: task recording is disabled unless explicitly opted in, public timeline/replay/task evidence surfaces return redacted summaries only, and diagnostics export reports task recording status without images, file names, or recording paths.
- Mobile approval payload privacy remains green: phone-facing approval list/detail/WebSocket events redact nested model-action args, local paths, selectors, tokens, values, and support-only notes.
- Remote WS client-error privacy remains green: targeted backend tests for all screen/input auth, close behavior, and generic error branches pass before any remote UX claim is made.
- Desktop token-bearing bridges remain loopback-only, and preload API requests reject non-plain or prototype-polluting data before IPC.
- Mobile smoke commands exit 0, including token subprotocol, task Companion list/start/follow-up/pause/resume/cancel, and remote-input grant lifecycle checks.
- Dependency lock verification passes when run with `npm run deps:verify`: backend direct requirements have pinned `==` entries in `backend/requirements-lock.txt`, and desktop/mobile npm lockfiles exist with matching root package name and version. This is a direct-dependency lock gate only; upgrade to uv or pip-tools for a full resolved Python lock when that workflow is adopted.
- Any skipped backend tests are expected platform skips and are recorded.

## 2. Demo-Before-Release Gate

Run this before any stakeholder demo, release-candidate walkthrough, or demo recording. This gate proves the candidate is safe enough to show even when release artifacts are not built yet:

```powershell
npm run qa:gate
```

Demo pass criteria:

- The preflight gate passes on the same candidate or has only documented, non-demo-blocking platform skips.
- The demo starts from a clean profile or clearly labeled test workspace with no private user files, real tokens, cookies, one-time codes, payment text, or customer data.
- The demo script covers first launch, one read-only natural-language task, one R2/R3 dry-run approval, one R4 blocked request, and one document QA answer with a citation label.
- Platform positioning evidence is captured: Settings model boundary profile, local model readiness or smoke result, one Skill Product Manifest sample, and one template-driven demo path. Source-level smokes may support preflight evidence, but release/demo claims need user-visible evidence. The Settings local-model Vite smoke includes 1366px desktop and 900px narrow desktop visual regression evidence, but it is not clean-machine local-model readiness, packaged Settings evidence, or release-candidate layout sign-off. Skill Product Manifest evidence must label manifest-declared permissions separately from inferred risk signals.
- Task recording is treated as opt-in demo evidence only: default demos should leave it disabled; if enabled, record the explicit opt-in, use disposable data, and do not share raw screenshots or recording files without separate review. Timeline/replay demos may show redacted summaries, counts, and labels, not raw tool payloads or image links.
- Desktop diagnostics evidence is captured if supportability or update status is mentioned: record whether the evidence is `python -m pytest backend\tests\test_system_diagnostics.py -q`, `npm --prefix desktop run smoke:system-diagnostics-ui`, packaged portable diagnostics smoke, or manual UI export. The current update action is local-only `refresh_local_status`; do not describe it as online update, auto-download, or auto-install.
- Mobile companion is included only if pairing, pending approvals, and approve/reject round trip were manually checked on the demo LAN or emulator setup, with real phone/emulator camera/QR evidence recorded when the demo claims scan-to-pair. If approval screenshots/logs are shown, verify nested args, local paths, selectors, tokens, values, and support-only notes are absent from phone-facing artifacts.
- Remote WS errors shown during a demo must be generic client copy, not raw backend exceptions. If a remote error state is part of the demo, record whether evidence is targeted backend tests or real phone/emulator WSS UX.
- LAN TLS readiness is recorded when a phone or emulator connects over LAN: record the HTTPS/WSS configuration plus the explicit certificate trust path used by that device. Non-loopback HTTP LAN is blocked for token-bearing mobile flows and may only be recorded as blocked-path evidence. The gate does not install certificates or prove system trust automatically; do not claim trust-chain completion unless it was manually verified on the target OS/device.
- Any P2/P3 rows skipped for the demo are recorded as residual risks, not implied passes.

## 3. Release Artifact Gate

Run the formal release check after Windows release artifacts have been built:

```powershell
npm run release:check
```

Use the structural-only quick check when you only need artifact presence, manifest, zip, and PE/header validation:

```powershell
npm run release:quick
```

Run the independent portable launcher/backend diagnostics smoke when Windows portable artifacts are present:

```powershell
npm run smoke:portable-first-screen
```

`release:check`, `release:gate`, `release:smoke`, and `release:quick` all include `release:safety`. Enable the strict state machine through `config.yaml` or the shell environment before running them:

```powershell
$env:LENGRVIS_STRICT_STATE_MACHINE = "true"
npm run release:check
```

If neither `LENGRVIS_STRICT_STATE_MACHINE=true` nor `privacy.strict_state_machine: true` is configured, `release:safety` is expected to fail before packaging verification starts. Treat that as the release gate doing its job, not as a runnable smoke failure.

Equivalent expanded command:

```powershell
npm run qa:gate
.\scripts\verify_release_safety.ps1
.\scripts\build_all.ps1 -VerifyOnly -RunExecutableSmoke -SmokeTimeoutSeconds 45
```

Pass criteria:

- The preflight gate passes on the same candidate.
- Release safety verification passes: `LENGRVIS_ALLOW_MOCK_FALLBACK` resolves to false, and `strict_state_machine` resolves to true through `LENGRVIS_STRICT_STATE_MACHINE=true` or `privacy.strict_state_machine: true` in `config.yaml`.
- `release:check` is the default formal release gate. It runs `qa:gate`, `scripts\verify_release_safety.ps1`, and `scripts\build_all.ps1 -VerifyOnly -RunExecutableSmoke -SmokeTimeoutSeconds 45`, so packaged backend executables must start and answer `/health`.
- `release:gate` and `release:smoke` are aliases for `release:check`, preserving the explicit gate/smoke command names without allowing a structural-only release pass.
- `release:quick` is the structural-only artifact check. Use it for fast artifact validation, not for release-candidate sign-off.
- The structural packaging verification performed by `scripts\verify_packaging.ps1` requires `dist\backend.exe`, `dist\Lengrvis-win-portable`, `dist\Lengrvis-win-portable.zip`, and `dist\Lengrvis-0.1.0-x64-self-extracting.exe` unless custom artifact paths are supplied directly to `scripts\build_all.ps1 -VerifyOnly`.
- Structural verification also checks that the portable directory and portable zip contain `Lengrvis.exe`, `resources\backend\backend.exe`, app resources, renderer dist, and package manifest.
- Public release artifacts must not include renderer/main/preload/shared source maps. `desktop` development watch builds may keep source maps through `tsconfig.node.json`, but release builds use `tsconfig.node.release.json`, Vite production builds keep `sourcemap: false`, and `scripts\verify_packaging.ps1` rejects `.map` files or `sourceMappingURL` references under `resources\app\dist` in both the portable directory and portable zip.
- `scripts\verify_packaging.ps1` validates PE headers and minimum sizes for `dist\backend.exe`, the portable launcher, the portable backend, and the self-extracting executable.
- Runnable packaging smoke passes when `release:check`, `release:gate`, `release:smoke`, or `scripts\build_all.ps1 -VerifyOnly -RunExecutableSmoke` is used: `dist\backend.exe` and the portable backend are started from isolated state/data directories and must answer `http://127.0.0.1:<port>/health` before the smoke timeout. Successful `--version` or `--help` exits are not sufficient for this gate.
- The portable launcher is not opened automatically during `release:check`; it must pass PE/header/size and packaged-resource preflight there. Use `npm run smoke:portable-first-screen` as separate GUI evidence.
- `scripts\portable_first_screen_smoke.ps1` launches `dist\Lengrvis-win-portable\Lengrvis.exe` with temporary `LENGRVIS_DATA_DIR`, `LENGRVIS_STATE_DIR`, `LENGRVIS_CONFIG_DIR`, an isolated loopback backend port, a one-time desktop API token, and a temporary loopback Electron/Chromium CDP port. It passes the first-screen smoke only when a portable window process appears, the packaged backend answers `/health`, and token-authenticated `GET /api/system/diagnostics` returns local-only Lengrvis diagnostics rooted in the temporary data/database directories without invoking diagnostics export or write endpoints.
- The same script attempts packaged renderer DOM automation through Playwright/CDP after the launcher/window/backend gate is satisfied. Only the explicit renderer DOM evidence line counts as packaged GUI-task automation: `[pass] portable renderer DOM read-only task evidence passed: ...`. That line means the script connected to the packaged renderer, clicked the read-only system-check entry, observed system information/read-only diagnostics copy, allowed only scoped known read-only GET calls such as health, task list, LLM health/cost status, system diagnostics, system info, processes, startup items, and app list, found no diagnostics export package in the temporary data dir, and observed zero chat messages, runs, or tasks in the isolated backend after the click. Any POST/PUT/PATCH/DELETE, unknown API mutation, diagnostics export, or settings/files/apps mutation during the read-only click fails the smoke.
- After the read-only entry evidence passes, the script separately attempts natural-language command dock evidence by submitting `帮我检查这台电脑`. Only `[pass] portable renderer DOM natural-language read-only task evidence passed: ...` counts as packaged natural-language command-dock evidence, and that pass requires a packaged renderer `/api/chat` or `/api/runs` POST plus backend read-only/system diagnostics task or run evidence. This is submission/task-evidence coverage, not release-candidate completion sign-off or completed task-result sign-off unless visible task progress/result evidence is also recorded. Visible safe-failure copy is still useful safety evidence when paired with zero side effects, but it is not accepted as natural-language task evidence without a packaged `/api/chat` or `/api/runs` submission. Any forbidden mutation or diagnostics export during this attempt fails the smoke.
- If CDP or the packaged renderer cannot be automated, the strict script exits 2 with `[unsupported]` for renderer DOM evidence. Use `-AllowBackendOnlyPass` only when intentionally collecting legacy launcher/window/backend diagnostics evidence; record that as unsupported GUI-task automation evidence, not as a GUI-task pass.
- If the portable directory, launcher, or packaged backend is missing, `scripts\portable_first_screen_smoke.ps1` prints `[blocked]` and exits 2. Record that as blocked artifact evidence, never as a pass.
- If a special offline Ollama release is being prepared, rerun verification with `scripts\build_all.ps1 -VerifyOnly -RequireBundledOllama -RunExecutableSmoke` and confirm the runtime, models, bundle manifest summaries, and backend runnable smoke match the packaged files.
- Failed executable smoke writes diagnostics under `.tmp\packaging-smoke`; missing artifacts should be rebuilt with `.\scripts\build_all.ps1` before rerunning the gate.

Evidence discipline:

- Record `release:check` as passing only when the whole command exits 0. If `qa:gate`, `release:safety`, structural checks, or runnable backend smoke pass before a later failure, record those as partial sub-gate evidence and keep the artifact gate failed.
- Dirty-worktree release checks may guide development, but a release-candidate handoff must include the commit or build id, platform, exact command, strict-state-machine source, and full exit status.
- Current workspace note, 2026-06-08: strict `npm run release:check` completed with exit 0 earlier in this workspace. After later product-hardening tests were added, the latest `npm run qa:gate` also completed with exit 0 and reported `1337 passed, 1 skipped` plus desktop/mobile typecheck, mobile behavior smoke, and desktop smoke. Targeted backend reruns in this round also returned diagnostics `8 passed`, remote WS `25 passed`, and mobile approval nested-args/local-path redaction `3 passed, 79 deselected`. The strict `release:check` run additionally covered `release:safety`, structural packaging checks, portable directory/zip source-map checks, and backend/portable backend `/health` smoke. A later `npm run smoke:portable-first-screen` run exited 0 with evidence in `.tmp\portable-first-screen-smoke\run-20260608-154045-41396-6013e259\portable.status.log`: the packaged renderer clicked "检查电脑状态", observed `/api/system/diagnostics` plus read-only diagnostics copy, allowed only scoped known read-only GET calls, and reported `tasks=0`, `runs=0`, `chat messages=0`, and `diagnostic-packages=0` after the read-only click. The same run then filled `帮我检查这台电脑`, observed a packaged renderer `POST /api/runs`, and recorded backend read-only/system diagnostics task evidence `task_99963aecac4841d2af25feb2f675c2ad` with `tasks=1`, `runs=1`, `chat messages=0`, and `diagnostic-packages=0`. Record this as packaged natural-language command-dock submission plus read-only/system diagnostics task evidence, not as clean-machine validation, real-device validation, full release-candidate sign-off, or completed task-result sign-off.

## 4. Manual P1 Sign-Off

Before tagging a release candidate, verify these user-visible flows against `docs/qa/e2e-acceptance-matrix.md`:

| Area | Required check |
| --- | --- |
| First launch | Fresh start opens the desktop shell and reaches backend health. |
| Agent task loop | A read-only natural-language task creates visible progress and completes or fails with actionable copy. |
| Task evidence and replay privacy | Verify task recording is off by default on the candidate profile. Timeline/replay/task evidence should show redacted summaries, counts, labels, and recording existence only; no screenshot URL, file name, recording id, raw tool args/result, hidden prompt, review reason text, or file body should be visible. If recording is enabled, record the explicit opt-in and use disposable data. |
| Approval loop | One reversible action produces dry-run approval; one forbidden token/credential request is blocked. |
| Document QA | A test document answer includes the correct source/citation label. |
| Local/hybrid model evidence | Settings shows quick/privacy/hybrid model boundary, recommended model, size, hardware status, speed estimate, and the privacy failure path that does not auto-fall back to cloud. For local/offline model claims, record clean-machine or packaged-profile install/start smoke, model/runtime/version, or the exact blocked reason; Vite-preview Settings DOM smoke alone is not sufficient. The current Settings local-model smoke provides Vite/mock visual regression at 1366px desktop and 900px narrow desktop widths; still record packaged or manual evidence before calling the release-candidate Settings UX signed off. |
| Skill sample | Import or display one non-private Skill/App integration sample and verify Product Manifest cards for file read/write, UI, network, messaging, delete, preview, and rollback/handoff. Declared permissions must be distinguished from inferred signals, which are UX hints rather than enforceable permission boundaries. Record DOM screenshot or manual import evidence; source-level assertions, mocked `/api/skills` DOM smoke, and zip/schema security validation are not real release-candidate import evidence. |
| Mobile companion | Pairing, pending approval list, approve/reject round trip, and approval payload redaction work on LAN or documented emulator setup. If the release/demo claims QR scanning, record a real phone/emulator camera path rather than only pasted payload or QR source-generation evidence. Backend LAN TLS metadata tests and mobile approval redaction tests may support configuration/privacy readiness, but do not replace a device trust-chain check or phone artifact review. |
| Remote WS error UX | For remote screen/input failures, verify client-visible errors are generic code/message copy and do not show raw exception text, selectors, hostnames, local paths, tokens, grant ids, pairing codes, or device names. Backend targeted tests returned `25 passed` in this round and cover invalid screen control, screen capture failure, unsupported input, policy/tool rejection, unexpected input exception redaction, auth/scope, query-token rejection, revoke, expiry, and disable close behavior. Real phone/WSS evidence is required before claiming release-quality mobile remote error UX. |
| LAN TLS readiness | For mobile/LAN runs, record the configured `https/wss` scheme, certificate source, and explicit device trust path. Non-loopback HTTP LAN should be recorded only as a blocked-path check and must not redeem mobile tokens. Do not imply automatic certificate installation or trust-chain validation. |
| Template demo path | One scripted template path from `docs/demo-script.md` runs against disposable data or is recorded as residual risk. |
| Portable artifact | `npm run smoke:portable-first-screen` reaches a visible portable window plus backend health and token-authenticated local-only diagnostics from temporary state/data. Only the explicit renderer DOM read-only line counts as packaged read-only entry automation. The explicit natural-language pass line may be recorded as packaged command-dock submission plus backend read-only/system diagnostics task evidence when it observes `/api/chat` or `/api/runs` and a related task/run; it is not clean-machine validation, full RC sign-off, or completed task-result sign-off by itself. Visible safe failure plus zero writes remains safety evidence, not agent task completion. If the strict script exits 2 with `[unsupported]`, or prints a natural-language `[unsupported]` line while the read-only entry passes, record the missing surface as unsupported and separately verify the release portable GUI action/result before claiming it. Do not infer natural-language task progress/result evidence from the automated backend probe or from `tasks=0` no-side-effect evidence. |
| Version, logs, and diagnostics export | In System Info, verify desktop/backend versions, backend status, `未配置在线更新通道`, `刷新本机状态`, local release notes, and log directories are visible. Refresh local status and confirm it does not present an online updater. Export one diagnostics package from the desktop UI on a disposable profile. The local UI may show the generated package path so the user can find it, but support/shareable export contents must redact usernames, organization folders, and full data/database/log absolute paths into labels plus scope/existence evidence. Do not call the package public-safe without a separate content review. |

Manual checks may be waived only when the release explicitly excludes the affected surface. Record the waiver owner, reason, expiry condition, and follow-up task.

## 5. Stop-Ship Conditions

Do not release if any of these are true:

- A P0 row in the acceptance matrix fails or is untested.
- R2/R3 actions bypass dry-run approval, or R4 actions are no longer blocked.
- Secrets, tokens, cookies, one-time codes, payment text, private file contents, or shareable full local paths appear in logs, URLs, audit exports, screenshots, support bundles, or release notes without an explicit local-only/internal-use label.
- Task recording or step screenshots are collected by default, or timeline/replay/task-list/agent-message/safety-review/progress/explain/support-export surfaces expose raw screenshot URLs, recording file names, recording ids, tool args/results, hidden prompts, review reasons, task metadata, or file contents.
- Mobile approval list/detail/WebSocket artifacts expose nested model-action args, local paths, selectors, tokens, values, support-only notes, or desktop-internal plan details.
- Mobile or desktop token transport moves from header/subprotocol storage into URL query strings.
- Remote screen/input WebSocket client errors expose raw backend exceptions, selectors, local paths, tokens, pairing/grant identifiers, device names, hostnames, stack traces, or other support-only details.
- Desktop token-bearing HTTP, WebSocket, notification, BrowserHost, or runtime-mode paths can be configured to send the desktop token to a non-loopback backend origin.
- Release artifacts are missing backend resources or package manifests.
- Public release artifacts contain renderer/main/preload/shared `.map` files or `sourceMappingURL` references under `resources\app\dist`.
- Runnable packaging smoke fails, times out, or only proves file presence without executable behavior.
- Release safety verification fails because mock fallback is enabled or strict state machine enforcement is not enabled for the release candidate.
- The candidate requires undocumented local environment state to launch.
- Demo or release material claims LAN TLS, HTTPS/WSS production readiness, or system certificate trust without recorded configuration and explicit device trust evidence.
- Demo or release material claims complete online automatic updates, auto-download/install updates, complete crash/update pipeline, public-safe diagnostics packages, or clean-machine diagnostics/RC sign-off when the only evidence is local-only refresh, Vite preview smoke, backend pytest, or packaged portable diagnostics smoke.
- Demo materials or release notes claim scan-to-pair, clean-machine local/offline model readiness, real Skill import evidence, natural-language packaged GUI task completion, or platform distribution sign-off when only source-level, Vite-preview, local stub, packaged renderer read-only entry, natural-language submission/task evidence, visible safe-failure, zero-write, or backend health evidence exists.
- Demo materials or release notes claim a P2/P3 capability that was not verified or explicitly waived for this candidate.
- Demo materials claim local/private, Skill/App integration, document citation, mobile companion, or template workflows without either recorded evidence or a named residual risk.

## 6. Result Template

Use this format in release notes or QA handoff:

```text
Release candidate:
Commit:
Date:
Platform:

Preflight gate:
- backend pytest:
- desktop typecheck:
- mobile typecheck:
- mobile token smoke:
- mobile task companion smoke:
- mobile remote-input grant smoke:
- desktop smoke:
- dependency lock verification:

Demo-before-release gate:
- clean profile/test workspace:
- Settings model boundary profile:
- Settings local-model visual regression:
- local model smoke/readiness:
- clean-machine local model evidence:
- desktop version/local refresh/logs:
- task recording default/opt-in state:
- task timeline/replay redaction:
- mobile approval payload redaction:
- read-only task:
- approval loop:
- blocked risky request:
- document QA citation:
- Skill Product Manifest sample:
- Skill Product Manifest DOM/screenshot:
- diagnostics export path redaction:
- diagnostics package public-safety review, if shared externally:
- template demo path:
- mobile companion, if included:
- mobile approval artifact review, if approval screenshots/logs are shared:
- remote WS generic error evidence (targeted backend tests or real WSS), if remote UX is included:
- real camera/QR pairing evidence, if claimed:
- LAN TLS readiness, if mobile/LAN included:

Artifact gate:
- release safety verification:
- release:quick / build_all -VerifyOnly, if run:
- release:check / build_all -VerifyOnly -RunExecutableSmoke:
- portable first-screen smoke:
- portable GUI read-only task:
- portable natural-language safe failure / task evidence:
- packaged source-map check:
- executable smoke logs:
- bundled Ollama verification, if applicable:

Manual sign-off:
- first launch:
- agent task loop:
- task evidence/replay privacy:
- approval loop:
- document QA:
- local/hybrid model evidence:
- Skill sample:
- mobile companion:
- mobile approval payload redaction:
- remote WS error UX:
- LAN TLS readiness:
- template demo path:
- portable artifact:

Waivers:
Residual risks:
```
