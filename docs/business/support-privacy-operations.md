# Support and Privacy Operations Runbook

> Status: operational draft. The product workflows below exist, but public support ownership, dedicated contact channels, jurisdiction-specific response periods, and a rehearsal record remain launch blockers.

## Ownership and intake

| Responsibility | Owner | Launch requirement |
| --- | --- | --- |
| Support duty owner | TBD | Name a primary and backup owner. |
| Privacy request owner | TBD | Name an accountable privacy contact. |
| Security escalation | `SECURITY.md` process | Keep security reports out of ordinary support queues. |
| Public support channel | TBD | Publish a non-personal mailbox or ticket portal. |
| Public privacy channel | TBD | Publish a monitored privacy mailbox or form. |

Until those fields are assigned, support and privacy operations are `in_progress` and cannot support a public paid launch.

Every request record must include a case ID, received time, category, severity, owner, current status, promised next update, affected product version, and closure evidence. Do not copy credentials, license tokens, pairing codes, private file contents, or raw diagnostic packages into issue trackers.

## Scope and severity

| Severity | Example | Internal routing target |
| --- | --- | --- |
| P1 | Suspected data exposure, destructive action outside approval, or paid service broadly unavailable | Acknowledge immediately during staffed hours; security/privacy owner takes control. |
| P2 | Core workflow unavailable with no reasonable workaround | Assign within one business day. |
| P3 | Degraded feature with a workaround | Assign within two business days. |
| P4 | Question, documentation issue, or feature request | Triage in the normal backlog. |

These are internal routing targets, not a public SLA. External response commitments require a staffed rota and approved legal terms.

## Diagnostic package handling

1. Ask for the smallest evidence needed. Prefer product version, error time, visible error text, and reproduction steps before requesting a package.
2. The user must initiate **Export diagnostics package** and approve the Electron native confirmation. The app never uploads it automatically.
3. Tell the user to inspect the package before sharing. Automated redaction does not make it `public_safe`.
4. Store received packages only in an access-controlled support location. Record the case ID, recipient, receipt time, purpose, and planned deletion date.
5. Do not paste package contents into chat, public issues, analytics, or model prompts. Extract only the minimum redacted facts needed for the case.
6. Delete the received package when the case closes or the approved retention period expires, whichever comes first. Record deletion evidence.
7. Escalate immediately if the package contains secrets, third-party personal data, or unexpected raw local paths.

## Data-subject and deletion requests

1. Create a privacy case without asking for unnecessary identity documents. Because the current product has no cloud account store, first determine whether the request concerns local device data, a diagnostic package previously sent to support, or a future hosted service.
2. For local device data, direct the user to **Settings > 本机数据与隐私**.
3. The user must type `删除本机数据`, then approve the native Electron warning. The renderer cannot submit the backend confirmation token directly.
4. Explain the scope before deletion:
   - Deleted: tasks, conversations, runs, recordings, approvals, pairings, memories, file index data, usage events, perception records, and exported diagnostic packages.
   - Optional: app settings and permission policies.
   - Preserved: the tamper-evident audit chain and a deletion event.
   - Manual: local log directories and external model caches.
5. Never ask the user to send the local audit database as proof. The UI success result and case note are sufficient unless legal counsel defines a stronger process.
6. For a diagnostic package already held by support, locate it by case ID, delete all controlled copies, and record completion.
7. Escalate requests involving statutory deadlines, litigation holds, minors, identity disputes, or data held by a third-party model provider to the privacy/legal owner.

## Retention baseline

| Data | Draft retention rule |
| --- | --- |
| Support case metadata | Keep only as long as needed for support, fraud prevention, and legal obligations; final period TBD by legal owner. |
| User-provided diagnostic package | Delete at case closure or the approved short retention limit; limit TBD before launch. |
| Security incident evidence | Follow the incident and legal-hold process; owner and period TBD. |
| Local product data | Controlled by the user through the desktop deletion workflow. |
| Local audit chain | Preserved by the deletion workflow for security accountability; legal basis and retention require counsel review. |

## Release rehearsal

Before marking `MR-P0-005` passed, attach dated evidence for:

- one desktop deletion run with and without settings removal;
- one denied run for an incorrect phrase and one cancelled native confirmation;
- one diagnostic export, human content review, controlled receipt, and recorded deletion;
- one mock P1 privacy escalation and one ordinary support case;
- named primary/backup owners, monitored channels, approved retention periods, and jurisdiction-specific response guidance;
- review of public policy, UI copy, support scripts, and actual product behavior against the same release candidate.

