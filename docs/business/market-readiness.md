# Market Readiness Dashboard

This is the fail-closed source of truth for selling, invoicing, licensing, or publicly advertising a paid Lengrvis offer. Engineering release readiness and commercial readiness are separate gates: a signed, tested build is not automatically ready to sell.

Status values are restricted to `blocked`, `in_progress`, `passed`, and `waived`.

## Current decision

| Field | Value |
| --- | --- |
| Commercial launch decision | `blocked` |
| Target market | `TBD` |
| Contracting entity | `TBD` |
| Commercial owner | `TBD` |
| Last reviewed UTC | `TBD` |

## Stop-sell blockers

| ID | Area | Required evidence | Status | Artifact / link label | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| MR-P0-001 | Contracting identity and tax | Registered contracting identity, public business address where required, tax/invoice treatment, approved billing descriptor, and non-personal legal/privacy/support contacts. | blocked | TBD | TBD | Personal project identity and mailbox are not sufficient evidence for a public paid launch. |
| MR-P0-002 | Legal approval | Counsel-approved EULA, privacy policy, refund policy, DPA/SLA applicability, consumer withdrawal terms, and documented supported jurisdictions. | blocked | docs/legal/README.md | legal owner TBD | Current documents remain candidate drafts. |
| MR-P0-003 | Checkout and subscription lifecycle | Chosen payment processor, test/live merchant accounts, checkout, receipts/invoices, renewal and cancellation notice, refund flow, chargeback handling, and tax evidence. | blocked | TBD | TBD | No payment channel is currently integrated. |
| MR-P0-004 | License issuer operations | Production Ed25519 public key embedded or provisioned in release builds; private key custody, issuance log, expiry, replacement, device migration, refund downgrade, and revocation procedure tested. | in_progress | docs/business/license-operations.md; license-admin tests; Settings commerce panel | commercial owner TBD | Offline issuer CLI, encrypted key generation, hash-chained ledger, replacement metadata, signed revocation manifest, runtime fail-closed revocation, and release config checks exist. Production owner, real key custody, fingerprint, delivery/update channel, and rehearsal evidence remain open. |
| MR-P0-005 | Support and privacy operations | Published support scope, intake channel, severity routing, data-subject request runbook, diagnostic-package handling, deletion guidance, retention, and response ownership tested. | in_progress | docs/business/support-privacy-operations.md; Settings privacy data panel; privacy erase tests | support owner TBD | Desktop typed confirmation, native confirmation, local deletion result, diagnostic handling, severity routing, and DSR runbook exist. Named owners, monitored public channels, approved retention periods, jurisdiction-specific response guidance, and rehearsal evidence remain open. |
| MR-P0-006 | Claims and launch assets | Approved pricing page, feature matrix matched to entitlement tests, platform/preview labels, security and privacy claims review, release notes, onboarding, and rollback communication. | in_progress | docs/pricing.md; docs/release/release-readiness-dashboard.md | product owner TBD | Technical capability matrix exists; public claims and owner approval remain open. |

## Technical commercialization evidence

- Backend entitlement gates define Free, Pro, and Team capability boundaries.
- Remote input remains subject to per-action strong approval after entitlement checks.
- Offline commercial licenses use Ed25519 verification; the runtime does not contain the private signing key.
- Desktop settings expose plan, license state, enabled capabilities, quota state, subject, seats, and expiry.
- License import rejects invalid, expired, oversized, or deployment-managed replacements before persistence.
- The offline issuer CLI creates encrypted Ed25519 keys, stable license IDs, token-free hash-chained issuance logs, replacement links, and signed revocation manifests.
- Runtime revocation data is signature-verified; a matching ID or invalid manifest disables paid entitlements.
- Commercial release profiles require a valid public key and reject runtime private/deprecated signing keys.
- Cloud usage metering exists, but published quotas must not be promised until billing and cost ownership are approved.

## Rules

1. Do not accept payment, issue invoices, publish paid pricing, or call a plan generally available while any `MR-P0` row is `blocked`.
2. `passed` requires an artifact label and named owner.
3. `waived` requires a named owner, reason, expiry, and follow-up issue in Notes.
4. Test licenses and development public keys are not production issuer evidence.
5. A payment sandbox pass is not a live merchant, refund, tax, or support pass.
6. Keep `docs/pricing.md` as the sole capability matrix; other documents must link to it rather than duplicate it.

## Commands

```powershell
npm run market:readiness
npm run market:readiness:strict
```

The non-strict command validates structure and reports open blockers. Strict mode fails until every `MR-P0` row is `passed` or explicitly `waived`.
