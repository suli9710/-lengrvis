# Mavris Release Gate

Last reviewed: 2026-06-07

This release gate turns the end-to-end acceptance matrix into a repeatable decision checklist. It is intentionally split into fast preflight, demo-before-release readiness, artifact verification, and manual sign-off so development builds do not need release artifacts while release candidates still verify the package that will ship.

## 1. Preflight Gate

Run this before merging release-bound changes:

```powershell
npm run qa:gate
```

Equivalent expanded commands:

```powershell
python -m pytest backend/tests -q --maxfail=1
npm --prefix desktop run typecheck
npm --prefix mobile run typecheck
npm --prefix mobile run smoke:token
npm --prefix desktop run smoke
```

Pass criteria:

- Backend, desktop, and mobile verification commands exit 0.
- Desktop smoke commands exit 0, including document scope, remote input grant, desktop WebSocket token, IPC security, bundled backend env, and browser activity smoke.
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
- Mobile companion is included only if pairing, pending approvals, and approve/reject round trip were manually checked on the demo LAN or emulator setup.
- Any P2/P3 rows skipped for the demo are recorded as residual risks, not implied passes.

## 3. Release Artifact Gate

Run this after Windows release artifacts have been built:

```powershell
npm run release:gate
```

Equivalent expanded command:

```powershell
npm run qa:gate
.\scripts\build_all.ps1 -VerifyOnly
```

Pass criteria:

- The preflight gate passes on the same candidate.
- `dist\backend.exe`, `dist\Mavris-win-portable`, `dist\Mavris-win-portable.zip`, and `dist\Mavris-0.1.0-x64-self-extracting.exe` exist unless custom artifact paths are supplied directly to `scripts\build_all.ps1 -VerifyOnly`.
- The portable package contains `Mavris.exe`, `resources\backend\backend.exe`, app resources, renderer dist, and package manifest.
- The self-extracting executable has a valid PE header and is above the minimum release size enforced by `scripts\verify_packaging.ps1`.
- If a special offline Ollama release is being prepared, rerun verification with `-RequireBundledOllama` and confirm the runtime, models, and bundle manifest summaries match the packaged files.

## 4. Manual P1 Sign-Off

Before tagging a release candidate, verify these user-visible flows against `docs/qa/e2e-acceptance-matrix.md`:

| Area | Required check |
| --- | --- |
| First launch | Fresh start opens the desktop shell and reaches backend health. |
| Agent task loop | A read-only natural-language task creates visible progress and completes or fails with actionable copy. |
| Approval loop | One reversible action produces dry-run approval; one forbidden token/credential request is blocked. |
| Document QA | A test document answer includes the correct source/citation label. |
| Mobile companion | Pairing, pending approval list, and approve/reject round trip work on LAN or documented emulator setup. |
| Portable artifact | The release portable starts from a clean directory and can run a read-only diagnostic task. |

Manual checks may be waived only when the release explicitly excludes the affected surface. Record the waiver owner, reason, expiry condition, and follow-up task.

## 5. Stop-Ship Conditions

Do not release if any of these are true:

- A P0 row in the acceptance matrix fails or is untested.
- R2/R3 actions bypass dry-run approval, or R4 actions are no longer blocked.
- Secrets, tokens, cookies, one-time codes, payment text, or private file contents appear in logs, URLs, audit exports, screenshots, or release notes.
- Mobile or desktop token transport moves from header/subprotocol storage into URL query strings.
- Release artifacts are missing backend resources or package manifests.
- The candidate requires undocumented local environment state to launch.
- Demo materials or release notes claim a P2/P3 capability that was not verified or explicitly waived for this candidate.

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
- desktop smoke:

Demo-before-release gate:
- clean profile/test workspace:
- read-only task:
- approval loop:
- blocked risky request:
- document QA citation:
- mobile companion, if included:

Artifact gate:
- build_all -VerifyOnly:
- bundled Ollama verification, if applicable:

Manual sign-off:
- first launch:
- agent task loop:
- approval loop:
- document QA:
- mobile companion:
- portable artifact:

Waivers:
Residual risks:
```
