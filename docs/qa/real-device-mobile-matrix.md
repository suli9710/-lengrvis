# Real-Device Mobile Matrix

Status date: 2026-06-08

This matrix defines the manual Android or emulator evidence required before a release/demo may claim real mobile pairing, LAN TLS, remote screen, or remote input. Existing local smoke tests and PairScreen source/config assertions support this matrix but do not satisfy it.

Current automated support evidence for the 2026-06-08 development workspace includes `backend/tests/test_mobile_pairing.py` at `87 passed` and `backend/tests/test_remote_desktop.py` at `26 passed`. Those results support mobile authorization, redaction, LAN TLS metadata, and remote WS backend contracts, but they do not satisfy any real phone/emulator HTTPS/WSS scenario below.

## Evidence Header

Each run should record:

| Field | Required value |
| --- | --- |
| Candidate | Commit SHA, build id, mobile app build, backend package/source. |
| Device | Real Android model or emulator profile, OS/API version, app install source. |
| Network | Same LAN/subnet note, backend host/IP, port, firewall/router/VPN state. |
| Transport | `https` API origin and `wss` WebSocket origin for approval, remote screen, and remote input. |
| Certificate trust | Certificate source, SHA-256 fingerprint or CA name, and explicit Android/emulator trust path. |
| Tester/date | Tester, date, location label, and redaction note for all artifacts. |

## Redacted Evidence Template

When `.\scripts\verify_mobile_lan_wss_preflight.ps1` is used, keep its `manual_real_device_evidence_template` from `evidence-summary.redacted.json` with the run notes. The template is a placeholder for manual collection only: `template_status` stays `manual_real_device_evidence_required`, `real_device_result` stays `uncollected`, and `must_not_be_recorded_as` stays `real-device pass evidence` until a tester attaches phone/emulator artifacts for the scoped scenarios.

| Template field | Required handling |
| --- | --- |
| `blocked_reason_redacted` | Keep every blocked preflight reason; do not delete or rewrite a blocked reason into a pass. |
| `device_identity_redacted` | Record a redacted device/emulator label, not a personal device name. |
| `https_origin_redacted`, `approval_wss_origin_redacted`, `remote_input_wss_origin_redacted` | Use redacted origins from the preflight or from reviewed artifacts; do not paste token-bearing URLs. |
| `certificate_trust_path` | Fill only after explicit Android/emulator trust evidence exists. |
| `camera_qr_path_evidence`, `actual_device_https_wss_evidence` | Leave as `uncollected` unless real camera/QR and device HTTPS/WSS evidence is attached. |
| `approval_artifact_review`, `remote_screen_artifact_review`, `remote_input_artifact_review`, `artifact_redaction_review` | Fill only after screenshots, videos, logs, or traces have been reviewed for the required redactions. |

## Required Scenarios

| ID | Scenario | Steps | Pass evidence | Current repo status |
| --- | --- | --- | --- | --- |
| RD-001 | Android pair over HTTPS/WSS | Start backend on LAN with TLS, generate pairing info, pair from a real Android device or emulator. If camera scanning is claimed, use a real camera/emulator scan; otherwise label the pasted payload fallback clearly. | Screenshot/video of pair flow, HTTPS/WSS origin, certificate trust path, and paired state. | Real-device HTTPS/WSS pairing is not evidenced by repo. Source/smoke evidence is limited to PairScreen's `expo-camera` QR scanner path, paste parser, native camera permission config, and desktop QR generation. |
| RD-002 | Same-LAN and firewall path | Verify the device reaches the backend over the LAN host/IP, not loopback or USB-only forwarding. | Network note showing same subnet/router path and successful HTTPS health or pairing reachability. | Not evidenced by repo. |
| RD-003 | Non-TLS LAN blocked path | Attempt `http://<lan-ip>:<port>` from mobile. | UI blocks pairing before token exchange, or logs show zero token-bearing requests; record as blocked-path only. | Covered by mobile smoke, still needs real-device confirmation if demo mentions LAN hardening. |
| RD-004 | Approval WebSocket over WSS | With paired device, create an approval on desktop and receive it on mobile. Approve and reject one benign request. | Mobile screen/video plus backend/audit log note; no token in URL artifacts and no nested model-action args, local paths, selectors, tokens, values, or support-only notes in phone-facing approval artifacts. | Backend and local smoke covered; latest `backend/tests/test_mobile_pairing.py` records `87 passed` across mobile approval redaction, token scope, device binding, companion task, and LAN TLS metadata paths. No real-device WSS evidence or phone artifact review. |
| RD-005 | Remote screen read-only over WSS | Open remote screen, verify frames render and the default mode is read-only. | Screenshot/video of visible remote frame, connection state, transport notice, and read-only state. | Backend TestClient and mobile source exist; latest `backend/tests/test_remote_desktop.py` records `26 passed`; no real-device evidence. |
| RD-006 | Remote input grant happy path | From desktop, grant remote input; mobile claims the grant and sends one click that still requires desktop-side approval. | Mobile shows authorized input, remaining time, and end-control; desktop shows approval/dry-run record for the input. | Backend/client smokes covered; no real-device evidence. |
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
- `screens/`: redacted screenshots or short clips.
- `logs/`: redacted backend/mobile/proxy snippets.
- `commands.txt`: automated commands and exit codes.
- `cert.txt`: certificate fingerprint, source, and Android/emulator trust path.
