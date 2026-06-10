# Lengrvis Release Gate

Last reviewed: 2026-06-09

This release gate turns the end-to-end acceptance matrix into a repeatable decision checklist. It is intentionally split into fast preflight, demo-before-release readiness, artifact verification, and manual sign-off so development builds do not need release artifacts while release candidates still verify the package that will ship.

Evidence vocabulary:

- Dev smoke/typecheck/unit evidence is current-worktree confidence only.
- Packaged evidence proves a built artifact path, but not clean-machine or release-candidate sign-off by itself.
- Clean-machine evidence requires a fresh machine/profile install or an explicitly blocked handoff with runtime/model/version fields.
- Real-device evidence requires the target phone/emulator/LAN/WSS/certificate-trust path and reviewed artifacts.
- RC sign-off requires the candidate commit/build id, exact commands, manual P1 checks, waivers, and residual risks in one handoff.

Beginner evidence map:

| Surface | Helper or preflight output | Strict evidence before a pass claim |
| --- | --- | --- |
| Android gate | `npm run android:release-gate -- -PreflightOnly` proves source/config readiness only; `npm run evidence:android-real-device-template` creates a fail-closed evidence JSON starting point only. | Installable QA APK plus reviewed Android/emulator evidence JSON; strict `android:release-gate` must see APK install, camera QR or documented emulator scan, HTTPS/WSS, certificate trust, approval WSS, remote screen, remote input, revoke/expiry, input approval checks, and artifact redaction. |
| Real-device/WSS | `npm run evidence:mobile-lan-wss` proves only no-phone prerequisite/config readiness. | Real phone/emulator artifacts for camera QR, approval WSS, remote screen WSS, remote input WSS, explicit Android/emulator certificate trust, revoke/expiry behavior, and screenshot/log review. |
| Local model | `npm run evidence:local-model-template` is a clean-machine handoff template. | Fresh machine/profile artifact/build/profile plus runtime/model/version/status and install/start/pull/task-smoke outcome, or a precise blocked reason. |
| Diagnostics | `npm run evidence:diagnostics-review` is an external-review checklist template. | Human review of the actual exported diagnostics package contents with package label, reviewed logs/path labels/task traces/model traces/device identifiers, reviewer, timestamp, decision, and blocked reason; keep `public_safe=false` unless a separate approval process explicitly changes it. |
| RC handoff | `npm run evidence:release` and `npm run evidence:rc-handoff` organize missing evidence and handoff fields. | Candidate commit/build/platform, packaged artifact labels, exact gate commands and full exits, strict-state source, manual P1 evidence, waiver/residual-risk review, and release-owner approval. |

## 1. Preflight Gate

Run this before merging release-bound changes:

```powershell
npm run qa:gate
```

When dependency manifests, lockfiles, or backend requirements change, also run:

```powershell
npm run deps:verify
npm run audit:deps
```

`qa:gate` already executes the golden-task regression set (`backend/tests/test_golden_tasks.py`, >=30 real tasks asserting plans, risk levels, approvals, file side effects, and tool outputs). To produce the standalone pass-rate report (95% threshold, archived under `.tmp/qa-evidence/golden-tasks/`), run:

```powershell
npm run golden:gate
```

Golden-task results are machine self-verified regression evidence only; they are not a human result-quality review, clean-machine pass, real-device pass, or RC sign-off. See `docs/qa/golden-tasks.md` for the dataset and the human review boundary.

Use the top-level npm evidence helpers below as the newcomer-friendly entrypoints. They wrap the PowerShell helpers and only produce evidence/template/preflight artifacts; they are not clean-machine passes, real-device passes, public-safe/signoff, RC signoff, release signoff, or completed task-result signoff. The raw PowerShell equivalents are listed for CI logs, parameterized handoffs, and reviewers who need to trace the exact helper script.

When release-bound changes touch mobile pairing, LAN transport, remote WSS, TLS certificates, QR payload generation, or docs that claim Android/WSS readiness, also run the no-phone prerequisite preflight:

```powershell
npm run evidence:mobile-lan-wss
```

Raw PowerShell equivalent:

```powershell
.\scripts\verify_mobile_lan_wss_preflight.ps1
```

Record the emitted `.tmp\mobile-lan-wss-preflight\...\evidence-summary.redacted.json` path as redacted prerequisite/config evidence only. This script validates backend host/public URL/cert environment, certificate host coverage for the advertised origin, QR payload shape, and HTTPS/WSS requirement wording without using a phone, emulator, camera, QR scanner, or real WSS connection. It also emits `manual_real_device_evidence_template` with `real_device_result=uncollected`, `must_not_be_recorded_as=real-device pass evidence`, `claim_controls.real_device_pass_claim_allowed=false`, and any `blocked_reason_redacted` entries so the missing manual evidence can be carried forward without overclaiming. Its `real_device_collection_checklist` must remain uncollected until redacted phone/emulator artifacts prove camera QR, actual HTTPS/WSS for approvals/remote screen/remote input, Android/emulator certificate trust, grant revoke/expiry, and screenshot/log review. It must not be recorded as a real-device pass. A blocked result for non-loopback HTTP/ws, loopback-only QR hosts, bind-only `0.0.0.0`, missing cert/key env, missing cert/key files, invalid cert/key material, or a certificate host mismatch is a gate failure until fixed or explicitly waived.

When release-bound changes claim an installable Android app, an Android QA APK, or Android remote-control signoff, also run the Android release gate. Use preflight while preparing the build config:

```powershell
npm run android:release-gate -- -PreflightOnly
npm --prefix mobile run preflight:android-release
```

Raw PowerShell equivalent:

```powershell
.\scripts\verify_android_release_gate.ps1 -PreflightOnly
```

Preflight validates `mobile/app.json`, `mobile/eas.json`, package scripts, local `eas-cli` devDependency, camera/notification permissions, `usesCleartextTraffic=false`, keyboard resize, the Android remote-control hardening plugin, and EAS preview/production build profiles. The hardening plugin must inject `network_security_config` with system/user trust anchors for explicitly installed local CA testing while keeping cleartext disabled, and must add Android `FLAG_SECURE` to protect remote-screen screenshots and recent-task snapshots. The preflight emits `.tmp\android-release-gate\...\android-release-gate.redacted.json` with `status=preflight_ready_not_release` when source configuration is ready, and marks APK/device gates as not evaluated. `npm --prefix mobile run build:android:preview` and `build:android:production` must run `preflight:android-release` before EAS build and use the repository-pinned EAS CLI instead of a global `eas` command. EAS project/account/credentials are external to source; if `expo.extra.eas.projectId` is not recorded, the candidate build log must still prove the redacted EAS project/build label used for the APK. This preflight is not an APK pass, install pass, WSS pass, Play submission/publication proof, or release signoff.

For a strict Android claim, run:

```powershell
npm run evidence:android-real-device-template -- -ArtifactLabel "<redacted apk label>" -ArtifactSha256 "<sha256 if known>" -DeviceLabel "<redacted device label>" -BackendBuildLabel "<redacted backend/build label>"
npm run android:release-gate -- -ArtifactPath "<qa apk path>" -RealDeviceEvidencePath "<reviewed android evidence json>"
```

The template helper creates a fail-closed starting JSON under `.tmp\android-real-device-evidence-template`; it is not pass evidence. The strict gate requires an installable `.apk` artifact and reviewed Android/emulator evidence JSON with `artifact_type=android-real-device-remote-control-evidence`, `real_device_result=passed`, true claim-control flags for APK install, camera QR, HTTPS/WSS, certificate trust, approval WSS, remote screen, remote input, revoke/expiry, and artifact redaction, plus passed checks for click, text, and key/PageDown input approval. AAB/store bundles may support store distribution, but they do not satisfy the installable APK gate by themselves. Without both the APK and real-device evidence, the script exits blocked and must not be recorded as Android release pass evidence.

The Android gate never submits or publishes to Play. `mobile/eas.json` `preview` builds produce the installable QA APK used by the strict gate; `production` builds produce an AAB for store distribution, but EAS submit/Play Console review/rollout needs separate credentials, logs, and release-owner approval before any submitted or published claim.

To assemble a redacted release evidence packet from current automatically checkable evidence without starting product flows, run:

```powershell
npm run evidence:release
```

Raw PowerShell equivalent:

```powershell
.\scripts\collect_release_evidence_packet.ps1
```

Record the emitted `.tmp\release-evidence-packet\...\release-evidence-packet.redacted.json` and `.tmp\release-evidence-packet\...\release-evidence-packet.redacted.md` files as a packet index, not as a pass. The packet summarizes the latest mobile LAN/WSS preflight redacted summary when present, the latest portable first-screen/read-only/natural-language status-log summary when present, the current Ollama/local-model contract count, latest local-model clean-machine handoff template status when present, latest natural-language result-quality review packet status when present, diagnostics `support_package_redaction.external_review` expectations with `public_safe=false`, and Settings local-model smoke artifact paths. It also emits `summary.release_ready=false`, `summary.claimable_release_signoff=false`, `summary.release_readiness_blocker_count=5`, and `release_readiness_blockers` entries for clean-machine local model evidence, mobile real-device LAN/WSS artifacts, natural-language result-quality sign-off, diagnostics external public-safety review, and release-candidate handoff. Treat those blocker entries as the beginner-facing missing-evidence dashboard, not as waivers. Portable status-log coverage is packaged window/backend/local-only diagnostics plus command-dock submission/task-evidence coverage only. It is not clean-machine local-model readiness, not true local model install/start/pull evidence, not real-device mobile evidence, not external diagnostics public-safety approval, not completed task-result sign-off, not natural-language result-quality sign-off, and not release-candidate sign-off. Product/API task explain surfaces carry completed-result evidence separately through `completion_evidence`: the portable smoke records explain `completion_evidence.level` and `result_verified`, but `submission`, `task_created`, and `visible_progress` levels are not completed-result evidence; only `completion_evidence.level=completed_result` with `result_verified=true` may be called completed-result evidence, and that is still not result quality review, RC sign-off, or release sign-off. `completion_evidence.signoff` remains false. Treat this helper as a handoff template and contract summary only: it cannot replace human review of the actual exported diagnostics contents, clean-machine validation, result-quality sign-off, or RC sign-off.

After generating the packet, open the `.redacted.md` first and work through `release_readiness_blockers` one by one: clean-machine local model, real-device LAN/WSS, natural-language result-quality review, actual diagnostics package content review, and RC handoff. Do not tag, publish, announce, or share diagnostics externally until the missing evidence is attached and the release owner has explicitly approved the separate human sign-off.

When a natural-language task/result is ready for human quality review, scaffold the redacted manual review fields with:

```powershell
npm run evidence:result-quality-review -- -TaskArtifactLabel "<task/run/status-log label>" -ResultArtifactLabel "<user-visible result/artifact label>" -UserVisibleResultReview "<review notes>" -SourceArtifactCheck "<source/artifact check>" -NextStepActionabilityCheck "<next-step/actionability check>" -Reviewer "<reviewer label>" -ReviewedAtUtc "<UTC timestamp>" -BlockedReason "none" # template only; not a pass
```

Raw PowerShell equivalent:

```powershell
.\scripts\collect_result_quality_review_packet.ps1 -TaskArtifactLabel "<task/run/status-log label>" -ResultArtifactLabel "<user-visible result/artifact label>" -UserVisibleResultReview "<review notes>" -SourceArtifactCheck "<source/artifact check>" -NextStepActionabilityCheck "<next-step/actionability check>" -Reviewer "<reviewer label>" -ReviewedAtUtc "<UTC timestamp>" -BlockedReason "none"
```

Record the emitted `.tmp\result-quality-review\...\result-quality-review.redacted.json` and `.tmp\result-quality-review\...\result-quality-review.redacted.md` files as a beginner-friendly review checklist packet only. The helper records task/result artifact labels, user-visible result review, source/artifact check, next-step/actionability check, reviewer, timestamp, blocked reason, and observed artifact labels if supplied. It is fail-closed: `summary.signoff=false`, `summary.claim_allowed=false`, `summary.result_quality_claim_blocked=true`, `summary.separate_human_signoff_required=true`, `claim_controls.completed_result_evidence=false`, `claim_controls.packet_is_rc_signoff=false`, and `claim_controls.packet_is_release_signoff=false` are fixed by the helper. Missing review fields or a real blocked reason produce `blocked_missing_fields`, `blocked_invalid_fields`, or `blocked_reason_recorded`, and the Markdown lists each missing/blocked item. Even when every field is recorded with `-BlockedReason "none"` and `summary.review_fields_complete=true`, this packet is still not completed-result evidence, not natural-language result-quality sign-off, not Task Workspace sign-off, not RC sign-off, and not release sign-off. A separate human sign-off artifact is required before any result-quality claim.

The packet also emits `rc_handoff_requirements.status=manual_rc_handoff_required`, with `release_candidate_signoff=false` and `packet_is_rc_signoff=false`. For a beginner handoff, treat that section as the missing-materials checklist: candidate commit or build id, platform and packaged artifact paths or redacted artifact labels, exact release gate commands and full exit status, strict-state-machine source, manual P1 checks, waivers with owner/reason/expiry/follow-up, and residual risks. Do not tag, publish, announce, or call an RC passed from `npm run evidence:release`; only after those fields are filled, full gate logs and manual P1 evidence are attached, waivers/residual risks are reviewed, and the release owner explicitly approves can it become RC sign-off.

To scaffold that separate handoff without turning it into approval, run:

```powershell
npm run evidence:rc-handoff -- -CandidateCommit "<commit SHA>" -BuildId "<build id>" -Platform "<platform>" -ArtifactLabel "<redacted artifact label>" -GateCommand "<exact command>" -GateExit "<exit code/status>" -StrictStateSource "<strict state source>" -ManualP1Check "<check/status/artifact label>" -Waiver "<none or owner/reason/expiry/follow-up>" -ResidualRisk "<risk/owner/follow-up>" # template only; not a pass
```

Raw PowerShell equivalent:

```powershell
.\scripts\collect_rc_handoff_template.ps1 -CandidateCommit "<commit SHA>" -BuildId "<build id>" -Platform "<platform>" -ArtifactLabel "<redacted artifact label>" -GateCommand "<exact command>" -GateExit "<exit code/status>" -StrictStateSource "<strict state source>" -ManualP1Check "<check/status/artifact label>" -Waiver "<none or owner/reason/expiry/follow-up>" -ResidualRisk "<risk/owner/follow-up>"
```

Record the emitted `.tmp\rc-handoff-template\...\rc-handoff-template.redacted.json` and `.tmp\rc-handoff-template\...\rc-handoff-template.redacted.md` files as a redacted RC handoff template only. If any required field is missing, the helper records `summary.status=manual_rc_handoff_required`, `release_candidate_signoff=false`, and `claim_allowed=false`. Even when every template field is recorded, the helper does not run gates, does not verify pass/fail, does not approve waivers or residual risks, and must not be treated as release-candidate pass, release sign-off, or permission to tag, publish, announce, or ship; the release owner must still review the evidence and sign off separately.

When local/offline model readiness is claimed or blocked on a candidate, scaffold the clean-machine evidence handoff fields with:

Use `npm run evidence:local-model-template` as a template-only helper, not clean-machine pass evidence, not true install/start/pull/task-smoke evidence, and not release-candidate sign-off; all pass/signoff fields remain false until separate manual evidence is recorded.

```powershell
npm run evidence:local-model-template -- -EvidenceMode clean-machine -ArtifactUnderTest "<installer/portable artifact label>" -BuildIdentifier "<build id/version>" -ProfileUnderTest "<clean profile label>" -Runtime "<runtime>" -RuntimeVersion "<version>" -Model "<model>" -ModelVersion "<version>" -InstallOutcome "<redacted install outcome>" -StartOutcome "<redacted start outcome>" -PullOutcome "<redacted pull outcome>" -TaskSmokeOutcome "<redacted task-smoke outcome>" -BlockedReason "<redacted blocked reason if the run cannot collect one or more fields>"
```

Raw PowerShell equivalent:

```powershell
.\scripts\collect_local_model_clean_machine_evidence_template.ps1 -EvidenceMode clean-machine -ArtifactUnderTest "<installer/portable artifact label>" -BuildIdentifier "<build id/version>" -ProfileUnderTest "<clean profile label>" -Runtime "<runtime>" -RuntimeVersion "<version>" -Model "<model>" -ModelVersion "<version>" -InstallOutcome "<redacted install outcome>" -StartOutcome "<redacted start outcome>" -PullOutcome "<redacted pull outcome>" -TaskSmokeOutcome "<redacted task-smoke outcome>" -BlockedReason "<redacted blocked reason if the run cannot collect one or more fields>"
```

Record the emitted `.tmp\local-model-clean-machine-evidence\...\local-model-clean-machine-evidence.redacted.json` and `.tmp\local-model-clean-machine-evidence\...\local-model-clean-machine-evidence.redacted.md` files as a redacted template only. The helper always includes `NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS`, records the artifact/build/profile, runtime/model/version/status, install/start/pull/task-smoke outcome or blocked reason fields needed for a manual handoff, and keeps clean-machine sign-off plus local model install/start/pull/task-smoke pass fields false. Use `-InstallBlockedReason`, `-StartBlockedReason`, `-PullBlockedReason`, or `-TaskSmokeBlockedReason` instead of the corresponding outcome when a specific step is blocked. It is not true local model install/start/pull/task-smoke evidence, not packaged Settings evidence, not clean-machine local-model readiness by itself, not template/dev smoke clean-machine pass evidence, and not release-candidate sign-off.

When a diagnostics package may be shared externally, scaffold the external-review checklist/status fields with:

```powershell
npm run evidence:diagnostics-review
```

Raw PowerShell equivalent:

```powershell
.\scripts\collect_diagnostics_external_review_packet.ps1
```

Record the emitted `.tmp\diagnostics-external-review\...\diagnostics-external-review.redacted.json` and `.tmp\diagnostics-external-review\...\diagnostics-external-review.redacted.md` files as a diagnostics review template only. The helper can organize redacted checklist fields and keep `public_safe=false`, `external_sharing_allowed=false`, and `claim_allowed=false`, but it does not review the actual exported package contents and is not public-safe/signoff, clean-machine validation, RC signoff, release signoff, or permission to publish the package. Even when the helper exits 0, `review_scope.automated_redaction_template=true`, `review_scope.actual_package_content_review_completed=false`, `summary.review_fields_complete=false`, `summary.external_sharing_blocked=true`, `summary.separate_human_content_review_required=true`, `claim_controls.public_safe_approval_created=false`, and the checklist must still carry the actual exported package path label, reviewed logs, path labels, task traces, model traces, device identifiers, reviewer/timestamp fields, and blocked reason before any separate human content-review artifact can be considered.

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
- Mobile LAN/WSS prerequisite preflight, when applicable, exits 0 only for an HTTPS/WSS-ready advertised origin with LAN TLS env, cert/key material, and certificate host coverage that can be validated without starting the backend. It writes a redacted evidence summary path and still requires separate real phone/emulator camera/QR, WSS, and certificate trust evidence before any Android/WSS pass claim.
- Diagnostics export tests, packet helpers, and evidence templates are contract/template evidence only. Before any diagnostics package is shared externally, a human must review the actual package contents, record the actual exported package path label plus logs/path labels/task traces/model traces/device identifiers, reviewer/timestamp, decision status, and blocked reason, and keep `public_safe=false`, `external_sharing_allowed=false`, and `claim_allowed=false` until that separate human artifact exists. That external content review is still not public-safe approval, clean-machine validation, RC sign-off, or release sign-off.
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
- If mobile LAN/WSS readiness is mentioned, include the latest `.\scripts\verify_mobile_lan_wss_preflight.ps1` result and redacted evidence summary path. Treat it as a prerequisite check for backend host/public URL/cert env, QR payload shape, and HTTPS/WSS wording; it does not replace a real phone/emulator camera/QR path, actual WSS connection, or explicit Android/emulator certificate trust evidence.
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
- After the read-only entry evidence passes, the script separately attempts natural-language command dock evidence by submitting `帮我检查这台电脑`. Only `[pass] portable renderer DOM natural-language read-only task evidence passed: ...` counts as packaged natural-language command-dock evidence, and that pass requires a packaged renderer `/api/chat` or `/api/runs` POST plus backend read-only/system diagnostics task or run evidence. This is submission/task-evidence coverage, not release-candidate completion sign-off or completed task-result sign-off by itself. The portable smoke records explain `completion_evidence.level` and `result_verified`; only `completion_evidence.level=completed_result` with `result_verified=true` may be called completed-result evidence. `submission`, `task_created`, or `visible_progress` completion evidence is still not completed-result evidence, and even completed-result evidence remains result evidence only, not result quality review, RC sign-off, or release sign-off; `completion_evidence.signoff` remains false. Visible safe-failure copy is still useful safety evidence when paired with zero side effects, but it is not accepted as natural-language task evidence without a packaged `/api/chat` or `/api/runs` submission. Any forbidden mutation or diagnostics export during this attempt fails the smoke.
- If CDP or the packaged renderer cannot be automated, the strict script exits 2 with `[unsupported]` for renderer DOM evidence. Use `-AllowBackendOnlyPass` only when intentionally collecting legacy launcher/window/backend diagnostics evidence; record that as unsupported GUI-task automation evidence, not as a GUI-task pass.
- If the portable directory, launcher, or packaged backend is missing, `scripts\portable_first_screen_smoke.ps1` prints `[blocked]` and exits 2. Record that as blocked artifact evidence, never as a pass.
- If a special offline Ollama release is being prepared, rerun verification with `scripts\build_all.ps1 -VerifyOnly -RequireBundledOllama -RunExecutableSmoke` and confirm the runtime, models, bundle manifest summaries, and backend runnable smoke match the packaged files.
- Failed executable smoke writes diagnostics under `.tmp\packaging-smoke`; missing artifacts should be rebuilt with `.\scripts\build_all.ps1` before rerunning the gate.

Evidence discipline:

- Record `release:check` as passing only when the whole command exits 0. If `qa:gate`, `release:safety`, structural checks, or runnable backend smoke pass before a later failure, record those as partial sub-gate evidence and keep the artifact gate failed.
- Dirty-worktree release checks may guide development, but a release-candidate handoff must include the commit or build id, platform, exact command, strict-state-machine source, and full exit status.
- Current workspace note, 2026-06-08/2026-06-09: strict `npm run release:check` completed with exit 0 earlier in this workspace. After later product-hardening tests were added, an earlier `npm run qa:gate` also completed with exit 0 and reported `1337 passed, 1 skipped` plus desktop/mobile typecheck, mobile behavior smoke, and desktop smoke. Latest targeted development integration in this dirty workspace now records desktop typecheck, mobile typecheck, `npm --prefix desktop run smoke:mobile-pairing-qr`, `npm --prefix desktop run smoke:remote-input-grant`, `npm --prefix mobile run smoke:token`, `npm --prefix mobile run smoke:task-companion`, `npm --prefix mobile run smoke:remote-input-grant`, and backend mobile+remote targeted combined run `132 passed`; those cover mobile approval redaction, token scope, device binding, companion task boundaries, LAN TLS metadata, remote screen/input auth and redacted error branches, revoke/expiry/disable behavior, text/key remote-input approval contracts, and active remote-input approval matching through phone-facing HMAC `binding_ref`/redacted active-grant labels rather than raw ids in shareable evidence. Scheduler/preflight targeted checks are support-only development notes unless their exact command and run log are attached; do not cite an unbound `9 passed` count from this gate. The prior P1/P2 review findings plus public task text bare-filename/hidden-prompt leakage, realtime raw malformed-message sampling, mobile QR transport metadata bypass, and remote-input active-grant mismatch findings are closed at contract/source-smoke level. `git diff --check` exited 0 and emitted only LF-to-CRLF working-copy conversion warnings. Treat these as current dirty-worktree development/formatting evidence, not release-candidate sign-off. The strict `release:check` run additionally covered `release:safety`, structural packaging checks, portable directory/zip source-map checks, and backend/portable backend `/health` smoke. A later `npm run smoke:portable-first-screen` run exited 0 with evidence in `.tmp\portable-first-screen-smoke\run-20260608-154045-41396-6013e259\portable.status.log`: the packaged renderer clicked "检查电脑状态", observed `/api/system/diagnostics` plus read-only diagnostics copy, allowed only scoped known read-only GET calls, and reported `tasks=0`, `runs=0`, `chat messages=0`, and `diagnostic-packages=0` after the read-only click. The same run then filled `帮我检查这台电脑`, observed a packaged renderer `POST /api/runs`, and recorded backend read-only/system diagnostics task evidence `task_99963aecac4841d2af25feb2f675c2ad` with `tasks=1`, `runs=1`, `chat messages=0`, and `diagnostic-packages=0`. Record this as packaged natural-language command-dock submission plus read-only/system diagnostics task evidence, not as clean-machine validation, real-device validation, full release-candidate sign-off, completed task-result sign-off, true local model install/start/pull evidence, or external diagnostics public-safety review. The portable smoke records explain `completion_evidence.level` and `result_verified`; only `completed_result` with `result_verified=true` may be labeled completed-result evidence, and that still is not result quality review, Task Workspace sign-off, RC sign-off, or release sign-off. Diagnostics helpers, source/client smokes, typechecks, and automated tests only prove the export/redaction contract, client/backend contracts, and handoff fields; they do not review exported package contents and cannot replace real-device LAN/WSS artifacts or turn a later human content review into public-safe approval/sign-off.

## 4. Manual P1 Sign-Off

Before tagging a release candidate, verify these user-visible flows against `docs/qa/e2e-acceptance-matrix.md`:

| Area | Required check |
| --- | --- |
| First launch | Fresh start opens the desktop shell and reaches backend health. |
| Agent task loop | A read-only natural-language task creates visible progress and completes or fails with actionable copy. For Task Workspace/result-quality claims, also verify the visible result is user-readable, tied to the task, has no write side effects, and provides a sensible next step or artifact; packaged `/api/runs` submission plus backend task evidence alone is not enough. |
| Task evidence and replay privacy | Verify task recording is off by default on the candidate profile. Timeline/replay/task evidence should show redacted summaries, counts, labels, and recording existence only; no screenshot URL, file name, recording id, raw tool args/result, hidden prompt, review reason text, or file body should be visible. If recording is enabled, record the explicit opt-in and use disposable data. |
| Approval loop | One reversible action produces dry-run approval; one forbidden token/credential request is blocked. |
| Document QA | A test document answer includes the correct source/citation label. |
| Local/hybrid model evidence | Settings shows quick/privacy/hybrid model boundary, recommended model, size, hardware status, speed estimate, and the privacy failure path that does not auto-fall back to cloud. For local/offline model claims, record clean-machine or packaged-profile artifact/build/profile, install/start/pull/task-smoke outcome, model/runtime/version/status, or the exact blocked reason; `.\scripts\collect_local_model_clean_machine_evidence_template.ps1` may be used to capture the redacted artifact/build/profile, runtime/model/version/status, and install/start/pull/task-smoke outcome or blocked reason fields, but its `NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS` output is only a handoff template. Vite-preview Settings DOM smoke and Ollama backend contract tests alone are not sufficient. The current Settings local-model smoke provides Vite/mock visual regression at 1366px desktop and 900px narrow desktop widths, and Ollama backend tests record `53 passed`; still record packaged or manual evidence before calling the release-candidate Settings UX or true local model install/start/pull/task-smoke signed off. |
| Skill sample | Import or display one non-private Skill/App integration sample and verify Product Manifest cards for file read/write, UI, network, messaging, delete, preview, and rollback/handoff. Declared permissions must be distinguished from inferred signals, which are UX hints rather than enforceable permission boundaries. Record DOM screenshot or manual import evidence; source-level assertions, mocked `/api/skills` DOM smoke, and zip/schema security validation are not real release-candidate import evidence. |
| Mobile companion | Pairing, pending approval list, approve/reject round trip, approval payload redaction, and remote-input approval active-grant matching work on LAN or documented emulator setup. If the release/demo claims QR scanning, record a real phone/emulator camera path rather than only pasted payload or QR source-generation evidence. Backend LAN TLS metadata tests and current backend mobile+remote targeted combined `132 passed` may support configuration/privacy readiness, but do not replace a device trust-chain check, real phone/emulator WSS path, or phone artifact review. |
| Remote WS error UX | For remote screen/input failures, verify client-visible errors are generic code/message copy and do not show raw exception text, selectors, hostnames, local paths, tokens, grant ids, pairing codes, or device names. Current backend mobile+remote targeted combined run returned `132 passed` and covers invalid screen control, screen capture failure, unsupported input, policy/tool rejection, unexpected input exception redaction, auth/scope, remote view/input cross-scope rejection, query-token rejection, revoke, expiry, disable close behavior, and text/key remote-input approval contracts. Mobile/desktop remote-input smokes also cover fail-closed active-grant mismatch and the case where a URL is `https://` but backend metadata says TLS is disabled or the websocket scheme is not WSS. Real phone/WSS evidence is required before claiming release-quality mobile remote error UX. |
| Mobile LAN/WSS prerequisite preflight | Run `.\scripts\verify_mobile_lan_wss_preflight.ps1` when mobile LAN/WSS readiness is claimed. Record its redacted evidence summary path and exit status. This validates advertised backend origin, LAN TLS env/cert/key, QR payload shape, and HTTPS/WSS wording without a phone; it is not real-device pass evidence. |
| LAN TLS readiness | For mobile/LAN runs, record the configured `https/wss` scheme, certificate source, and explicit device trust path. Non-loopback HTTP LAN should be recorded only as a blocked-path check and must not redeem mobile tokens. Do not imply automatic certificate installation or trust-chain validation. |
| Template demo path | One scripted template path from `docs/demo-script.md` runs against disposable data or is recorded as residual risk. |
| Portable artifact | `npm run smoke:portable-first-screen` reaches a visible portable window plus backend health and token-authenticated local-only diagnostics from temporary state/data. Only the explicit renderer DOM read-only line counts as packaged read-only entry automation. The explicit natural-language pass line may be recorded as packaged command-dock submission plus backend read-only/system diagnostics task evidence when it observes `/api/chat` or `/api/runs` and a related task/run; it is not clean-machine validation, full RC sign-off, or completed task-result sign-off by itself. The smoke also records explain `completion_evidence.level` and `result_verified`; call it completed-result evidence only when `level=completed_result` and `result_verified=true`, and still do not treat that as result quality review or RC sign-off. Visible safe failure plus zero writes remains safety evidence, not agent task completion. If the strict script exits 2 with `[unsupported]`, or prints a natural-language `[unsupported]` line while the read-only entry passes, record the missing surface as unsupported and separately verify the release portable GUI action/result before claiming it. Do not infer natural-language task progress/result evidence from the automated backend probe or from `tasks=0` no-side-effect evidence. |
| Version, logs, and diagnostics export | In System Info, verify desktop/backend versions, backend status, `未配置在线更新通道`, `刷新本机状态`, local release notes, and log directories are visible. Refresh local status and confirm it does not present an online updater. Export one diagnostics package from the desktop UI on a disposable profile. The local UI may show the generated package path so the user can find it, but support/shareable export contents must redact usernames, organization folders, and full data/database/log absolute paths into labels plus scope/existence evidence. Before any external sharing, verify `support_package_redaction.external_review` is present, keep `public_safe` false, and record the checklist/status review outcome for the actual package contents. That human content review is required for external sharing but still is not `public-safe`, clean-machine validation, RC sign-off, or release sign-off; automated diagnostics tests and helper packets only supply contract/template evidence. |

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
- Demo or release material claims complete online automatic updates, auto-download/install updates, complete crash/update pipeline, public-safe diagnostics packages, or clean-machine diagnostics/RC sign-off when the only evidence is local-only refresh, Vite preview smoke, backend pytest, packaged portable diagnostics smoke, diagnostics export tests, evidence-packet helpers, or an external diagnostics content-review checklist.
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
- git diff --check:
- dependency lock verification:
- release evidence packet summary:
- release evidence packet RC handoff requirements (`manual_rc_handoff_required` is not sign-off):

Demo-before-release gate:
- clean profile/test workspace:
- Settings model boundary profile:
- Settings local-model visual regression:
- local model smoke/readiness:
- local model clean-machine evidence template:
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
- diagnostics package external content review, if shared externally (not public-safe/sign-off):
- diagnostics package external-review checklist/status (`public_safe=false`):
- template demo path:
- mobile companion, if included:
- mobile approval artifact review, if approval screenshots/logs are shared:
- remote WS generic error evidence (targeted backend tests or real WSS), if remote UX is included:
- mobile LAN/WSS prerequisite preflight:
- mobile real-device redacted template:
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
- natural-language result quality / Task Workspace:
- task evidence/replay privacy:
- approval loop:
- document QA:
- local/hybrid model evidence:
- Skill sample:
- mobile companion:
- mobile approval payload redaction:
- remote WS error UX:
- mobile LAN/WSS prerequisite preflight:
- LAN TLS readiness:
- template demo path:
- portable artifact:

Waivers:
Residual risks:
```
