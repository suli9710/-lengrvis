# Release Readiness Dashboard

This dashboard is the single fail-closed view for deciding whether a Lengrvis candidate can move from engineering validation to RC or public release.

It intentionally distinguishes **machine evidence**, **manual evidence**, **waivers**, and **release-owner sign-off**. A helper or template output is not a pass until the matching evidence row is marked `passed` with an artifact label and owner.

This dashboard covers engineering delivery. A paid or publicly advertised commercial launch must also pass `docs/business/market-readiness.md`; neither dashboard substitutes for the other.

## Current candidate

| Field | Value |
| --- | --- |
| Candidate commit | `1176c66` |
| Build id | `main CI 28288204979` |
| Platform | `Windows primary; Android companion preview` |
| Release owner | `suli9710` |
| Dashboard last reviewed UTC | `2026-06-27T12:10:00Z` |
| Decision | `waived for v0.1.1 maintenance packaging only; not RC, GA, commercial launch, or public claims sign-off` |

## Stop-ship blockers

Status values are restricted to: `blocked`, `in_progress`, `passed`, `waived`.

| ID | Area | Required evidence | Status | Artifact / link label | Owner | Expiry / next review | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| RR-P0-001 | Clean-machine Windows install | Reviewed `clean-machine-release-evidence-reviewed` accepted by `npm run evidence:clean-machine-verify`: install, launch, backend health, first read-only task, diagnostics export, uninstall/rollback, and artifact redaction review on a clean Windows machine/profile. | waived | [main CI run 28288204979](https://github.com/suli9710/-lengrvis/actions/runs/28288204979) | suli9710 | 2026-07-27 | Reason: v0.1.1 is maintenance packaging only, not RC or public GA. Follow-up issue: collect clean-machine Windows evidence before RC. |
| RR-P0-002 | Local model clean-machine path | Reviewed clean-machine evidence accepted by `npm run evidence:clean-machine-verify -- --require-local-model`: runtime install/start/pull, model version, privacy-mode task smoke, or explicit blocked handoff if unavailable. | waived | [main CI run 28288204979](https://github.com/suli9710/-lengrvis/actions/runs/28288204979) | suli9710 | 2026-07-27 | Reason: no privacy-mode marketing claim is made for v0.1.1. Follow-up issue: collect local-model clean-machine evidence before privacy-mode claim. |
| RR-P0-003 | Android real-device / emulator LAN-WSS | Strict `npm run android:release-gate` with installable APK and reviewed Android evidence JSON: QR pairing, HTTPS/WSS approval stream, remote screen stream, remote input grant, revoke/expiry, certificate trust, redacted screenshot/log review. | waived | [main CI run 28288204979](https://github.com/suli9710/-lengrvis/actions/runs/28288204979) | suli9710 | 2026-07-27 | Reason: Android companion remains preview for v0.1.1. Follow-up issue: collect real-device LAN/WSS evidence before mobile release claim. |
| RR-P0-004 | Result quality review | 30+ realistic natural-language tasks with outcome, user-visible artifact review, success rate, rewrite rate, and safety false-positive/false-negative notes. | waived | [main CI run 28288204979](https://github.com/suli9710/-lengrvis/actions/runs/28288204979) | suli9710 | 2026-07-27 | Reason: v0.1.1 relies on automated gates only and is not task-result quality sign-off. Follow-up issue: run 30+ realistic task review before RC. |
| RR-P0-005 | Diagnostics external-share review | Actual exported diagnostic package manually checked for paths, tokens, device identifiers, task text, logs, model paths, and public-sharing decision. | waived | [main CI run 28288204979](https://github.com/suli9710/-lengrvis/actions/runs/28288204979) | suli9710 | 2026-07-27 | Reason: no public-safe diagnostics sharing claim is made for v0.1.1. Follow-up issue: complete diagnostics external-share review before any public diagnostic artifact. |
| RR-P0-006 | RC handoff and release-owner sign-off | Candidate commit/build/platform, full gate log, manual P1 checks, waivers, residual risk, owner approval. | waived | [main CI run 28288204979](https://github.com/suli9710/-lengrvis/actions/runs/28288204979) | suli9710 | 2026-07-27 | Reason: release owner approves scope-limited v0.1.1 maintenance packaging only. Follow-up issue: produce full RC handoff before RC, GA, announcement, or broad distribution. |

## P1 hardening backlog

| ID | Area | Required change | Status | Artifact / link label | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| RR-P1-001 | Large-file maintainability | Split or ownership-map `desktop/src/main/ipc.ts`, `desktop/src/main/browserHost.ts`, `backend/app/core/db.py`, `backend/app/context_management.py`, and largest release scripts. | in_progress | TBD | TBD | Start with ownership map if code split is too risky. |
| RR-P1-002 | Skill / MCP supply chain | Enforce signed-skill policy for release profile, permission diff review, third-party MCP owner policy, and audit artifact. | blocked | TBD | TBD | Current safeguards are good but not enough for broad ecosystem release. |
| RR-P1-003 | Security exception audit | Review broad `except Exception` in security-sensitive paths and replace with specific exception classes or explicit audit comments. | in_progress | TBD | TBD | Best-effort paths may keep broad catches with justification. |
| RR-P1-004 | Release evidence UX | Keep this dashboard updated and reference it from RC handoff. | in_progress | docs/release/release-readiness-dashboard.md | release owner | This file is the source of truth. |

## Required commands before strict release review

```powershell
npm run hygiene
npm run deps:verify
npm run supply-chain:verify
npm run security:extensions
npm run qa:gate
npm run evidence:result-quality-verify
npm run release:check
npm run release:readiness
npm run release:readiness:strict
npm run release:readiness:rc
npm run market:readiness
npm run market:readiness:strict
```

Strict readiness accepts explicit release-owner-approved `waived` rows for scoped maintenance packaging. RC/GA readiness must use `npm run release:readiness:rc`, which fails until every P0 blocker is `passed`.

## Manual evidence rules

1. Use artifact labels, not private absolute paths, in this dashboard.
2. A template-only helper output must stay `blocked` or `in_progress` until the real run evidence exists.
3. `waived` requires owner, reason, expiry, and follow-up issue.
4. Do not mark mobile, clean-machine, diagnostics public-safety, or result quality as passed based only on CI.
5. Do not tag, publish, announce, or share diagnostics externally while any P0 row is `blocked`.

## Review cadence

- Update this file once per release candidate.
- Attach exact command logs or artifact labels in the matching row.
- Keep historical evidence in release artifacts; keep this dashboard focused on the current candidate.
