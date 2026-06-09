# Remote Session LAN/TLS Gate

Status date: 2026-06-09

This gate covers the audit items tracked as P0-2/P0-3 for remote desktop, remote input, mobile token scope, and LAN TLS. It is intentionally a release gate and residual-risk ledger, not proof that real-device LAN/TLS has already passed.

Current automated support evidence for the 2026-06-09 development workspace includes `backend/tests/test_mobile_pairing.py` at `88 passed` and `backend/tests/test_remote_desktop.py` at `28 passed`. These numbers strengthen repository contract coverage only; the release pass rule below still requires real-device or documented emulator HTTPS/WSS evidence.

## Audit Disposition

| Audit claim | Disposition | Repository evidence | Remaining gap |
| --- | --- | --- | --- |
| Remote WebSockets may lack scoped auth. | Mostly not true for current backend. | `backend/tests/test_remote_desktop.py` now records `28 passed` and covers remote screen disabled-by-default, `remote:view` scope acceptance, approval-scope rejection, remote view/input cross-scope rejection, query-token rejection, device revoke close, token expiry close, and remote desktop disabled close. `backend/app/api/routes_remote.py` requires mobile WebSocket subprotocol tokens and scope-specific decode. | Real Android over LAN/WSS must still prove the same behavior outside TestClient. |
| Remote input grant revoke/expiry may not be enforced. | Mostly not true for automated backend/client contract coverage. | `backend/tests/test_remote_desktop.py` now records `28 passed` and covers connected and idle `/ws/remote/input` close after grant revoke, grant expiry, token expiry, cross-scope rejection, and remote desktop disable. `mobile/scripts/remote-input-grant-smoke.cjs` covers claim, subprotocol use, wrong token rejection, revoke, and expiry against a local behavior server. | A real mobile session must show visible read-only fallback after revoke/expiry and no further input events. |
| Mobile tokens may be over-scoped or reusable for wrong endpoints. | Mostly not true for current backend tests. | `backend/app/security/mobile_jwt.py` separates `mobile:approval`, `remote:view`, and `remote:input`; grant tokens require `source=remote_input_grant` and `grant_id`. `backend/tests/test_mobile_pairing.py` now records `88 passed` and covers remote-input-only token rejection for mobile approval WebSocket and mobile API resources. | Real-device evidence still needs token expiry/revoke UI behavior. |
| LAN non-TLS may be accepted as production evidence. | True as an evidence risk; product gate must block this. | `mobile/src/api/client.ts` rejects non-loopback HTTP LAN in `assertSafeBaseUrl`, `assertSafePairingSession`, and WebSocket construction. `mobile/scripts/mobile-token-smoke.cjs` covers blocked new pairing, stale stored session cleanup, token-bearing APIs, and mobile WebSocket construction for non-loopback HTTP LAN. Backend LAN API guard still keeps limited LAN pairing/mobile paths distinct from desktop APIs. | HTTP LAN may be recorded only as blocked-path evidence. A release/demo pass requires HTTPS/WSS plus explicit device trust evidence. |
| LAN TLS/WSS is documented as ready without device trust proof. | True unless a candidate evidence packet is attached. | `backend/tests/test_lan_transport_security.py`, `backend/tests/test_mobile_pairing.py`, and diagnostics tests cover TLS metadata for ready/misconfigured states. Docs already warn that metadata is not device trust. | Need real phone/emulator HTTPS/WSS connection, certificate source, fingerprint or CA path, and explicit trust path on that device. |
| Mobile UI may not show remote session state, remaining time, or disconnect prompts. | Partly false for source-level UI, but still lacks real-device QA evidence. | `mobile/src/screens/RemoteScreen.tsx` renders screen connection state, input connection state, transport notice, grant mode, grant remaining time, end-control button, retry button, and disconnect/error messages. `mobile/src/remoteInputGrant.ts` handles remaining time, expiry, revocation, and usable state. | Need screenshots/video from real Android proving the rendered text and state transitions survive real network, background/lock, revoke, and expiry. |

## Release Pass Rule

Do not mark P0-2/P0-3 as passed for a candidate unless both layers below are present in the QA packet.

`.\scripts\verify_mobile_lan_wss_preflight.ps1` may be attached before this gate as redacted prerequisite/config evidence only. A ready preflight means the advertised origin, TLS material, QR payload shape, and HTTPS/WSS wording are plausible for manual collection; it still leaves `manual_real_device_evidence_template.real_device_result=uncollected`, `claim_controls.real_device_pass_claim_allowed=false`, and `preflight_ready_is_pass=false`. Keep both generated outputs, `evidence-summary.redacted.json` and `real-device-evidence-checklist.redacted.md`, with the run notes. The real-device layer below must fill the camera QR, actual device HTTPS/WSS, certificate trust, grant revoke/expiry, and screenshot/log review checklist from phone/emulator artifacts before a pass can be claimed.

1. Automated regression layer:
   - `python -m pytest backend/tests/test_mobile_pairing.py backend/tests/test_lan_api_guard.py backend/tests/test_lan_transport_security.py backend/tests/test_remote_desktop.py -q`
   - `npm --prefix mobile run smoke:token`
   - `npm --prefix mobile run smoke:task-companion`
   - `npm --prefix mobile run smoke:remote-input-grant`
   - `npm --prefix desktop run smoke:remote-input-grant`

2. Real-device or documented emulator layer:
   - Android device or emulator identity: model/emulator profile, OS/API version, app build, backend commit, date, tester.
   - Network path: same-LAN topology, redacted backend host/IP label, port, firewall/router note, and whether the device is on the same subnet; raw host/IP values stay in local-only notes outside tracked source.
   - Transport: `https` and `wss` scheme for token-bearing mobile APIs, approval WebSocket, remote screen, and remote input.
   - Certificate/trust: certificate source, fingerprint or CA identity, and the explicit Android/emulator trust path used before the pass.
   - Remote screen state: connected, weak/offline/reconnect, desktop remote setting disabled, device revoke, token expiry, and visible disconnect/retry prompt.
   - Remote input state: read-only default, grant received, remaining time visible, input connected, mobile end-control, desktop revoke, grant expiry, and no input after revoke/expiry.
   - Lock/background: app background or device lock behavior recorded; no task content or token appears on lock screen notifications.
   - Evidence artifacts: screenshots/video/log excerpts with all tokens, pairing codes, raw hostnames/IP addresses, private paths, and device names redacted unless the artifact is explicitly local-only.

## Non-Evidence

The following are useful supporting checks but must not be counted as real-device LAN/TLS pass evidence:

- FastAPI `TestClient` WebSocket tests.
- Mobile behavior smoke using `mobile/scripts/behavior-smoke-helpers.cjs`.
- QR payload parsing, QR source generation, or pasted payload flow without a real scan claim.
- `real-device-evidence-checklist.redacted.md` while it still contains `uncollected` fields, except as operator checklist/preflight evidence.
- TLS metadata status such as `https_ready` when no Android/emulator trust path is attached.
- Non-loopback HTTP LAN attempts, except as blocked-path evidence.
- Loopback HTTP behavior, except as local development or smoke evidence.

## Blocking Conditions

Block the candidate or demo claim when any of these are true:

- A token-bearing mobile flow uses `http://` or `ws://` on a non-loopback LAN address and is described as passed.
- Any mobile or desktop token appears in a URL, screenshot, proxy trace, QA log, or release note.
- Remote input remains usable after grant revoke, grant expiry, token expiry, device revoke, or remote desktop disable.
- Mobile UI lacks a visible read-only/offline/expired/disconnected state during the tested failure condition.
- The release note says HTTPS/WSS, TLS, trust chain, or real Android pairing is complete without the real-device evidence packet above.
