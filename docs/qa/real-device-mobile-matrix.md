# Real-Device Mobile Matrix

Status date: 2026-06-09

This matrix defines the manual Android or emulator evidence required before a release/demo may claim real mobile pairing, LAN TLS, remote screen, or remote input. Existing local smoke tests and PairScreen source/config assertions support this matrix but do not satisfy it.

Current automated support evidence for the 2026-06-09 development workspace includes the combined `python -m pytest backend/tests/test_mobile_pairing.py backend/tests/test_remote_desktop.py -q` run at `132 passed`, plus passing mobile/desktop targeted smokes/typechecks in the latest integration. Scheduler/preflight targeted checks are support-only development notes unless their exact command and run log are attached; do not cite an unbound `9 passed` count from this matrix. Those results support mobile authorization, redaction, LAN TLS metadata, remote WS backend contracts, remote-input text/key event contracts, and active-grant remote-input approval guards, but they do not satisfy any real phone/emulator HTTPS/WSS scenario below.

How this connects to the Android gate:

- `npm run evidence:mobile-lan-wss` and `npm run android:release-gate -- -PreflightOnly` are setup checks only; they are not camera QR, APK install, HTTPS/WSS, certificate trust, or remote-input pass evidence.
- `npm run evidence:android-real-device-template` is only a fail-closed starting point for the reviewed JSON; leave pass/claim-control fields false until the artifacts in this matrix exist.
- The generated `android-real-device-evidence.redacted.template.json` is not the strict-gate input until a reviewer fills a separate reviewed JSON with `real_device_result=passed`, `review.status=reviewed_passed`, redacted `device`, `transport`, `certificate`, and `evidence_artifacts_redacted` fields, and every required check below is backed by phone/emulator artifacts.
- Strict `npm run android:release-gate -- -ArtifactPath "<qa apk path>" -RealDeviceEvidencePath "<reviewed android evidence json>"` may be recorded as Android gate evidence only after the reviewed JSON is backed by this matrix's phone/emulator artifacts.
- New operators should use the generated `real-device-evidence-checklist.redacted.md` as the checklist, keep raw LAN/device values in local-only notes, and attach only redacted screenshots, clips, logs, and the reviewed Android evidence JSON to the QA packet.

## Evidence Header

Each run should record:

| Field | Required value |
| --- | --- |
| Candidate | Commit SHA, build id, mobile app build, backend package/source. |
| Device | Real Android model or emulator profile, OS/API version, app install source. |
| Network | Same LAN/subnet note, redacted backend host/IP label, port, firewall/router/VPN state. Keep raw host/IP values only in a local-only evidence note outside tracked source. |
| Transport | `https` API origin and `wss` WebSocket origin for approval, remote screen, and remote input. |
| Certificate trust | Certificate source, SHA-256 fingerprint or CA name, and explicit Android/emulator trust path. |
| Tester/date | Tester, date, location label, and redaction note for all artifacts. |

## Redacted Evidence Template

When `.\scripts\verify_mobile_lan_wss_preflight.ps1` is used, keep both redacted outputs with the run notes: `evidence-summary.redacted.json` and `real-device-evidence-checklist.redacted.md`. The JSON template and Markdown checklist are placeholders for manual collection only: `template_status` stays `manual_real_device_evidence_required`, `real_device_result` stays `uncollected`, `claim_controls.real_device_pass_claim_allowed` stays `false`, and `must_not_be_recorded_as` stays `real-device pass evidence` until a tester attaches phone/emulator artifacts for the scoped scenarios.

The preflight can say the LAN/TLS prerequisites are ready, but it still runs without a phone, emulator, camera, QR scanner, or real WebSocket. Treat `real_device_evidence_status=uncollected_fail_closed`, `real_device_evidence_collected=false`, and `no_phone_preflight_claim=not_real_device_pass` as hard claim controls until the separate artifact bundle is reviewed.

| Template field | Required handling |
| --- | --- |
| `blocked_reason_redacted` | Keep every blocked preflight reason; do not delete or rewrite a blocked reason into a pass. |
| `claim_controls` | Keep `real_device_pass_claim_allowed=false`; only a separate reviewed phone/emulator evidence bundle can support a pass claim. |
| `artifact_collection_rules` | Follow the Markdown checklist: share only redacted artifacts, never token-bearing URLs, and keep raw LAN IPs/hosts/device names in local-only notes outside tracked source. |
| `operator_collection_order` | Keep every step unchecked until real Android/emulator artifacts exist; the preflight output alone does not complete any step. |
| `device_identity_redacted` | Record a redacted device/emulator label, not a personal device name. |
| `device.kind`, `device.profile_label_redacted` | Fill `android_phone` or `android_emulator` plus a redacted profile label before strict Android gate review. |
| `app.artifact_label_redacted`, `app.artifact_sha256` | Match the installable APK under test; the SHA-256 must match the APK passed to the strict gate. |
| `https_origin_redacted`, `approval_wss_origin_redacted`, `remote_screen_wss_origin_redacted`, `remote_input_wss_origin_redacted` | Use redacted origins from the preflight or from reviewed artifacts; do not paste token-bearing URLs. |
| `transport.https_origin_redacted`, `transport.approval_wss_origin_redacted`, `transport.remote_screen_wss_origin_redacted`, `transport.remote_input_wss_origin_redacted` | Fill the strict-gate transport fields with redacted `https://[redacted-host]` and `wss://[redacted-host]/...` labels only after device-originated evidence exists. |
| `certificate_trust_path` | Fill only after explicit Android/emulator trust evidence exists. |
| `certificate.trust_path_label_redacted`, `certificate.fingerprint_label_redacted` | Record the Android/emulator trust path, CA, or fingerprint label without raw hostnames, private paths, cert secrets, or key material. |
| `review.status`, `review.reviewer_label`, `review.reviewed_at_utc`, `evidence_artifacts_redacted` | Fill only after a human reviews the actual screenshots, clips, logs, and notes; these are required before the strict gate can treat the reviewed JSON as passed. |
| `camera_qr_path_evidence`, `actual_device_https_wss_evidence` | Leave as `uncollected` unless real camera/QR and device HTTPS/WSS evidence is attached. |
| `approval_wss_evidence`, `remote_screen_wss_evidence`, `remote_input_wss_evidence` | Fill separately. Approval WSS evidence does not prove remote screen or input, and remote screen evidence does not prove input. |
| `certificate_trust_evidence` | Leave as `uncollected` until the exact Android/emulator profile's certificate trust path is documented. |
| `remote_input_grant_revoke_evidence`, `remote_input_grant_expiry_evidence` | Leave as `uncollected` unless the real device/emulator shows read-only fallback/no further input after revoke and expiry. |
| `grant_revoke_expiry_artifact_review` | Fill only after revoke/expiry screenshots, videos, and logs have been checked for redaction. |
| `approval_artifact_review`, `remote_screen_artifact_review`, `remote_input_artifact_review`, `artifact_redaction_review` | Fill only after screenshots, videos, logs, or traces have been reviewed for the required redactions. |
| `real_device_collection_checklist.camera_qr` | Attach redacted real camera/emulator scan proof when scan-to-pair is claimed; source QR generation, parser smoke, pasted payload, or preflight output is not enough. |
| `real_device_collection_checklist.actual_https_wss` | Attach actual device-originated HTTPS plus approval, remote screen, and remote input WSS evidence for the scoped claim. |
| `real_device_collection_checklist.approval_wss` | Attach a device-visible approval received from `/ws/mobile/approvals` over WSS plus approve/reject outcomes and redacted backend/audit confirmation. |
| `real_device_collection_checklist.remote_screen_wss` | Attach a visible remote frame, connection state, transport notice, and read-only state from `/ws/remote/screen` over WSS. |
| `real_device_collection_checklist.remote_input_wss` | Attach grant-scoped `/ws/remote/input` over WSS, remaining time, desktop approval or dry-run record, and disabled/read-only state before and after the grant. |
| `real_device_collection_checklist.certificate_trust` | Attach certificate source/fingerprint or CA and the explicit Android/emulator trust path; cert/key parsing is not device trust. |
| `real_device_collection_checklist.remote_input_grant_revoke_expiry` | Attach mobile end-control revoke, desktop/device revoke, grant expiry, and token/device revoke behavior when remote input is in scope. |
| `real_device_collection_checklist.screenshot_log_review` | Attach the artifact review note before screenshots/logs are shared or called pass evidence. |

## Beginner Real-Device Collection Path

Use this path after the preflight has produced `evidence-summary.redacted.json` and `real-device-evidence-checklist.redacted.md`. Leave every generated checklist item unchecked and every evidence field `uncollected` until that exact action has been performed on the target phone or emulator.

1. Record the candidate, build, backend, device/emulator profile, OS/API version, network note, and tester/date in a local run note.
2. Put the phone/emulator and desktop backend on the same LAN path. Raw IPs, hostnames, and device names stay in a local-only note; the shareable packet uses redacted labels.
3. Install or configure certificate trust on that exact Android/emulator profile, or document the expected trust failure before trust is added.
4. Pair with the camera QR path when scan-to-pair is claimed. If the operator pastes a payload or enters a code manually, label that artifact as fallback evidence only.
5. Create a benign approval and prove `/ws/mobile/approvals` connected over WSS from the device. Capture both approve and reject outcomes.
6. Open remote screen and prove `/ws/remote/screen` connected over WSS, frames render, and the default state is read-only.
7. If remote input is in scope, grant input from desktop, prove `/ws/remote/input` connected over WSS with remaining time, and record a benign input approval/dry-run whose public `binding_ref` or redacted active-grant label matches the current mobile session. Keep raw `deviceId`/`grantId` correlation in local-only reproduction notes.
8. Revoke remote input from mobile, revoke from desktop or the device list, and observe expiry with a short grant. Evidence must show the UI returns to read-only/no-input and stale input cannot reconnect.
9. Review screenshots, videos, backend logs, mobile logs, and proxy traces for mobile tokens, grant tokens, pairing codes, raw host/IPs, device names, private paths, nested model-action args, selectors, support-only notes, and task secrets before sharing.

## Shareable Artifact Rules

Use the generated `real-device-evidence-checklist.redacted.md` as the novice operator checklist for each candidate. It is safe to attach to release notes only while it still says `real_device_result=uncollected`, `real_device_pass_claim_allowed=false`, and `preflight_ready_is_pass=false`.

Shareable evidence may include redacted screenshots, short clips, run notes, and log excerpts. Replace mobile tokens, grant tokens, pairing codes, raw hostnames/IP addresses, device names, private local paths, nested model-action args, selectors, support-only notes, and task secrets before sharing.

Do not paste token-bearing URLs, Authorization headers, raw QR payloads, raw LAN IPs/hostnames, private device names, or unredacted proxy traces into the shared packet. If a tester needs those values to reproduce the run, keep them in a separate local-only note outside tracked source and reference only a redacted label from the evidence bundle.

## Required Scenarios

| ID | Scenario | Steps | Pass evidence | Current repo status |
| --- | --- | --- | --- | --- |
| RD-001 | Android pair over HTTPS/WSS | Start backend on LAN with TLS, generate pairing info, pair from a real Android device or emulator. If camera scanning is claimed, use a real camera/emulator scan; otherwise label the pasted payload fallback clearly. | Screenshot/video of pair flow, HTTPS/WSS origin, certificate trust path, and paired state. | Real-device HTTPS/WSS pairing is not evidenced by repo. Source/smoke evidence is limited to PairScreen's `expo-camera` QR scanner path, paste parser, native camera permission config, and desktop QR generation. |
| RD-002 | Same-LAN and firewall path | Verify the device reaches the backend over the LAN host/IP, not loopback or USB-only forwarding. | Network note showing same subnet/router path and successful HTTPS health or pairing reachability. | Not evidenced by repo. |
| RD-003 | Non-TLS LAN blocked path | Attempt `http://<lan-ip>:<port>` from mobile. | UI blocks pairing before token exchange, or logs show zero token-bearing requests; record as blocked-path only. | Covered by mobile smoke, still needs real-device confirmation if demo mentions LAN hardening. |
| RD-004 | Approval WebSocket over WSS | With paired device, create an approval on desktop and receive it on mobile. Approve and reject one benign request. | Mobile screen/video plus backend/audit log note; no token in URL artifacts and no nested model-action args, local paths, selectors, tokens, values, or support-only notes in phone-facing approval artifacts. | Backend and local smoke covered; latest backend mobile+remote targeted combined run records `132 passed` across mobile approval redaction, token scope, device binding, companion task, LAN TLS metadata, and remote WS paths. No real-device WSS evidence or phone artifact review. |
| RD-005 | Remote screen read-only over WSS | Open remote screen, verify frames render and the default mode is read-only. | Screenshot/video of visible remote frame, connection state, transport notice, and read-only state. | Backend TestClient, mobile source, and latest targeted smokes covered; no real-device evidence. |
| RD-006 | Remote input grant happy path | From desktop, grant remote input; mobile claims the grant and sends benign click, text, and key/PageDown input that still requires desktop-side approval. The approval must be for the active mobile grant, not a stale or different device/grant. Shareable artifacts should use the phone-facing HMAC `binding_ref` or a redacted active-grant label; raw `deviceId`/`grantId` stay in local-only reproduction notes. | Mobile shows authorized input, remaining time, text/key controls, zoom/pan state, and end-control; desktop shows approval/dry-run record for each input; evidence notes confirm the public approval `binding_ref` matched the current mobile grant, with raw id correlation kept local-only if needed. | Backend/client smokes cover active-grant mismatch blocking and matching-grant acceptance; backend WebSocket tests cover text/key event approval contracts; no real-device evidence. |
| RD-007 | Grant revoke from mobile | Tap end-control on mobile during active grant. | Mobile returns to read-only/disabled input; `/ws/remote/input` closes or stops accepting events. | Backend/client smokes covered; no real-device evidence. |
| RD-008 | Grant revoke from desktop | Revoke the active grant or revoke the device from desktop while mobile is connected. | Mobile shows revoked/offline/disconnected state and cannot send input. | Backend TestClient covered; no real-device evidence. |
| RD-009 | Grant expiry | Use a shortened grant or wait for TTL expiration. | Mobile remaining-time display reaches expired/disabled and input cannot reconnect. | Backend/client smokes covered; no real-device evidence. |
| RD-010 | Mobile token expiry | Use a short-lived paired token or controlled test build. | Approval/remote screen/input WebSockets close; mobile clears session or shows reconnect/re-pair prompt. | Backend tests covered; no real-device evidence. |
| RD-011 | Remote desktop disabled | Disable remote desktop on desktop while mobile screen/input is connected. | Mobile remote screen and input disconnect with visible retry/enable prompt. | Backend tests covered; no real-device evidence. |
| RD-012 | Weak network/reconnect | Toggle Wi-Fi, move to weak signal, or use emulator network shaping during approval and remote screen. | Mobile shows offline/reconnecting state and recovers without leaking tokens or accepting stale input. | Not evidenced by repo. |
| RD-013 | Lock screen/background | Background the app and lock the device during an approval and during remote screen/input. | Remote screen/input pauses or disconnects safely; notifications do not expose task body, token, pairing code, or private file content. | Notification redaction and source behavior exist; no real-device lock evidence. |
| RD-014 | TLS trust failure | Use HTTPS with a cert not trusted by the device. | Pairing/connection fails or prompts with certificate/fingerprint guidance; no token-bearing flow succeeds before trust. | Metadata/client source covered; no real-device evidence. |
| RD-015 | TLS trusted path | Install/trust the cert or use a device-trusted cert, then repeat pairing, approvals, remote screen, and remote input. | Full HTTPS/WSS flow succeeds with trust path recorded. | Not evidenced by repo. |
| RD-016 | Artifact redaction | Inspect screenshots, videos, backend logs, app logs, and proxy traces from the run. | No raw mobile token, grant token, pairing code, private path, nested model-action args, selector, support-only note, device name, or task secret appears in shareable artifacts. | Automated redaction tests exist for mobile approval payloads, remote WS generic errors, diagnostics export, and some notification/task payloads; run-specific artifact review still required. |

## Sign-Off Rules

- `Pass`: all required scenarios for the claimed release/demo scope have attached evidence and automated regressions pass.
- `Conditional pass`: a scenario is intentionally out of scope and the release/demo copy names it as unsupported or residual risk.
- `Fail`: token-bearing non-loopback LAN HTTP succeeds as a pass path, input persists after revoke/expiry, TLS trust is claimed without device proof, or artifacts contain secrets.

## Suggested Evidence Bundle Layout

Use a candidate-specific folder outside tracked source, for example `.tmp/qa-evidence/mobile-lan-tls/<date>-<candidate>/`, with:

- `run-notes.md`: evidence header plus scenario outcomes.
- `real-device-evidence-checklist.redacted.md`: generated checklist from the preflight, kept as preflight/config evidence until filled by reviewed phone/emulator artifacts.
- `evidence-summary.redacted.json`: generated preflight JSON with `manual_real_device_evidence_template`.
- `android-real-device-evidence.redacted.json`: reviewed strict-gate input using `artifact_type=android-real-device-remote-control-evidence`; it should contain only redacted labels plus passed/blocked fields, not raw tokens, raw host/IPs, raw device ids, or raw grant ids.
- `android-real-device-evidence.redacted.template.json`: optional fail-closed output from `npm run evidence:android-real-device-template`; keep it as a starting template only, not pass evidence.
- `android-release-gate.redacted.json`: output from `.\scripts\verify_android_release_gate.ps1 -ArtifactPath "<qa apk path>" -RealDeviceEvidencePath "<reviewed android evidence json>"`.
- `screens/`: redacted screenshots or short clips.
- `logs/`: redacted backend/mobile/proxy snippets.
- `commands.txt`: automated commands and exit codes.
- `cert.txt`: certificate fingerprint, source, and Android/emulator trust path.

The strict Android evidence JSON must include `claim_controls.real_device_pass_claim_allowed=true` only after the APK was installed on the target device/profile, camera QR or documented emulator scan completed, approval/remote screen/remote input WSS paths connected from that device, certificate trust was explicit on that device, click/text/key/PageDown approvals were observed, mobile end-control/desktop revoke/grant expiry all returned to read-only, and all artifacts were reviewed for tokens, pairing codes, raw hosts, raw device ids, raw grant ids, and private paths.

Use `npm run evidence:android-real-device-template` to create a fail-closed starting point for `android-real-device-evidence.redacted.json`; do not change `real_device_result` or claim-control flags to passed/true until the reviewed artifacts exist.
