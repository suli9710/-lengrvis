# Release Readiness Dashboard

This dashboard is the single fail-closed view for deciding whether a Lengrvis candidate can move from engineering validation to RC or public release.

It intentionally distinguishes **machine evidence**, **manual evidence**, **waivers**, and **release-owner sign-off**. A helper or template output is not a pass until the matching evidence row is marked `passed` with an artifact label and owner.

## Current candidate

| Field | Value |
| --- | --- |
| Candidate commit | `TBD` |
| Build id | `TBD` |
| Platform | `Windows primary; Android companion preview` |
| Release owner | `TBD` |
| Dashboard last reviewed UTC | `TBD` |
| Decision | `blocked` |

## Stop-ship blockers

Status values are restricted to: `blocked`, `in_progress`, `passed`, `waived`.

| ID | Area | Required evidence | Status | Artifact / link label | Owner | Expiry / next review | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| RR-P0-001 | Clean-machine Windows install | Install, launch, backend health, first read-only task, diagnostics export, uninstall/rollback on a clean Windows machine/profile. | blocked | TBD | TBD | TBD | Required before RC. |
| RR-P0-002 | Local model clean-machine path | Runtime install/start/pull, privacy-mode task smoke, explicit failure state if model unavailable. | blocked | TBD | TBD | TBD | Do not market privacy mode until passed. |
| RR-P0-003 | Android real-device / emulator LAN-WSS | QR pairing, HTTPS/WSS approval stream, remote screen stream, remote input grant, revoke/expiry, certificate trust, redacted screenshot/log review. | blocked | TBD | TBD | TBD | Mobile remains preview until passed. |
| RR-P0-004 | Result quality review | 30+ realistic natural-language tasks with outcome, user-visible artifact review, success rate, rewrite rate, and safety false-positive/false-negative notes. | blocked | TBD | TBD | TBD | Machine golden tasks are not enough. |
| RR-P0-005 | Diagnostics external-share review | Actual exported diagnostic package manually checked for paths, tokens, device identifiers, task text, logs, model paths, and public-sharing decision. | blocked | TBD | TBD | TBD | No `public_safe=true` claim without this. |
| RR-P0-006 | RC handoff and release-owner sign-off | Candidate commit/build/platform, full gate log, manual P1 checks, waivers, residual risk, owner approval. | blocked | TBD | TBD | TBD | Required before tag or announcement. |

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
npm run release:check
npm run release:readiness
npm run release:readiness:strict
```

Strict readiness is expected to fail until every P0 blocker is `passed` or has an explicit release-owner-approved `waived` row.

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
