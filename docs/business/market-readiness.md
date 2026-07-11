# Market Readiness Dashboard

This is the fail-closed source of truth for selling, invoicing, licensing, or publicly advertising a paid Lengrvis offer. Engineering release readiness and commercial readiness are separate gates: a signed, tested build is not automatically ready to sell.

Status values are restricted to `blocked`, `in_progress`, `passed`, and `waived`.

## Current decision

| Field | Value |
| --- | --- |
| Commercial launch decision | `blocked for v0.1.2; the v0.1.1 maintenance-only waiver does not apply` |
| Target market | `TBD` |
| Contracting entity | `TBD` |
| Commercial owner | `suli9710` |
| Last reviewed UTC | `2026-06-27T12:10:00Z` |

## Stop-sell blockers

| ID | Area | Required evidence | Status | Artifact / link label | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| MR-P0-001 | Contracting identity and tax | Reviewed `commercial-operations-evidence-reviewed` accepted by `npm run evidence:commercial-operations-verify`: registered contracting identity, public business address or exemption, tax registration/treatment, invoice tax display, approved billing descriptor, and non-personal legal/privacy/support contacts. | blocked | docs/business/commercial-operations.md; docs/business/payment-tax-operations.md | suli9710 | The v0.1.1 maintenance-only waiver is out of scope for v0.1.2. Current-version evidence or a new owner-approved v0.1.2 waiver is required. Follow-up issue: COMM-101. |
| MR-P0-002 | Legal approval | Reviewed `commercial-operations-evidence-reviewed` accepted by `npm run evidence:commercial-operations-verify`: official source register, legal risk memo, counsel-approved EULA, privacy policy, refund policy, DPA/SLA applicability, consumer withdrawal terms, supported jurisdictions, and public contact terms. | blocked | docs/business/commercial-operations.md; docs/legal/commercial-legal-approval-checklist.md; docs/legal/legal-source-register.md; docs/legal/commercial-legal-risk-memo.md | suli9710 | The v0.1.1 maintenance-only waiver is out of scope for v0.1.2. Current-version legal approval or a new owner-approved v0.1.2 waiver is required. Follow-up issue: COMM-102. |
| MR-P0-003 | Checkout and subscription lifecycle | Reviewed `commercial-loop-evidence-reviewed` plus reviewed `commercial-operations-evidence-reviewed`: subscription key creation, first activation, renewal refresh, cancellation, refund/revocation, chargeback runbook, invoice/receipt, tax treatment, settlement, and reconciliation. | blocked | docs/business/commercial-operations.md; docs/business/payment-tax-operations.md; docs/business/license-operations.md | suli9710 | The v0.1.1 maintenance-only waiver is out of scope for v0.1.2. Current-version lifecycle evidence or a new owner-approved v0.1.2 waiver is required. Follow-up issue: COMM-103. |
| MR-P0-004 | License issuer operations | Reviewed `commercial-loop-evidence-reviewed` accepted by `npm run evidence:commercial-loop`: production Ed25519 public key, activation API HTTPS, activation key hash storage, private key custody, issuance log, renewal/replacement/revocation rehearsals, and no runtime private key. | blocked | docs/business/license-operations.md; license-admin tests; Settings commerce panel | suli9710 | The v0.1.1 maintenance-only waiver is out of scope for v0.1.2. Current-version issuer evidence or a new owner-approved v0.1.2 waiver is required. Follow-up issue: COMM-104. |
| MR-P0-005 | Support and privacy operations | Reviewed `support-privacy-operations-evidence-reviewed` plus reviewed `commercial-operations-evidence-reviewed`: published support scope, monitored intake channel, severity/SLA terms, privacy escalation, diagnostic-package handling, deletion guidance, retention, response ownership, and customer scripts. | blocked | docs/business/commercial-operations.md; docs/business/support-privacy-operations.md; docs/business/support-refund-operations.md | suli9710 | The v0.1.1 maintenance-only waiver is out of scope for v0.1.2. Current-version operations evidence or a new owner-approved v0.1.2 waiver is required. Follow-up issue: COMM-105. |
| MR-P0-006 | Claims and launch assets | Reviewed `claims-launch-evidence-reviewed` plus reviewed `commercial-operations-evidence-reviewed`: approved pricing page, feature matrix matched to entitlement tests, platform/preview labels, security and privacy claims review, prohibited claims list, release notes, onboarding, and rollback communication. | blocked | docs/business/commercial-operations.md; docs/business/public-claims-register.md; docs/pricing.md | suli9710 | The v0.1.1 maintenance-only waiver is out of scope for v0.1.2. Current-version claims evidence or a new owner-approved v0.1.2 waiver is required. Follow-up issue: COMM-106. |

## Waiver Follow-up Tracker

- COMM-101: Complete signed contracting identity, tax treatment, invoice display, billing descriptor, and non-personal commercial contact evidence before any paid launch.
- COMM-102: Complete signed legal approval for source register, legal risk memo, EULA, privacy, refunds, DPA/SLA applicability, jurisdictions, and public contact terms before public paid launch.
- COMM-103: Integrate and test checkout, subscription lifecycle, invoice/receipt, refund, chargeback, settlement, and reconciliation evidence before paid launch.
- COMM-104: Complete production license key custody, fingerprint, delivery/update channel, activation server, renewal/replacement/revocation, and rehearsal evidence before paid launch.
- COMM-105: Name monitored support owners, retention policy, jurisdiction guidance, diagnostic handling, privacy escalation, and support rehearsal evidence before paid/public launch.
- COMM-106: Approve public claims, pricing assets, release notes, onboarding, platform labels, prohibited-claims review, and rollback communication before launch.

## Technical commercialization evidence

- Backend entitlement gates define Free, Plus, and Pro capability boundaries.
- Remote input remains subject to per-action strong approval after entitlement checks.
- Offline commercial licenses use Ed25519 verification; the runtime does not contain the private signing key.
- First activation can call `LENGRVIS_ACTIVATION_BASE_URL/api/v1/activations`; the server stores only activation-key hashes and returns signed licenses.
- Desktop settings expose plan, license state, enabled capabilities, quota state, subject, seats, and expiry.
- License import rejects invalid, expired, oversized, or deployment-managed replacements before persistence.
- The offline issuer CLI creates encrypted Ed25519 keys, stable license IDs, token-free hash-chained issuance logs, replacement links, and signed revocation manifests.
- Runtime revocation data is signature-verified; a matching ID or invalid manifest disables paid entitlements.
- Commercial release profiles require a valid public key and reject runtime private/deprecated signing keys.
- Cloud usage metering and default fail-closed token enforcement exist: Free is capped at rolling 5h/5M plus 7d/20M tokens, Plus at 24h/10M tokens, and Pro at 24h/100M tokens. Plus and Pro share the same safety controls; Pro adds model quality and quota only. Public paid quota claims still require billing, overage, and cost-owner approval before launch.

## Rules

1. Do not accept payment, issue invoices, publish paid pricing, or call a plan generally available unless every `MR-P0` row is `passed`; `waived` is only valid for no-sale maintenance packaging or explicitly excluded surfaces.
2. `passed` requires an artifact label and named owner.
3. `waived` requires a named owner, reason, expiry, follow-up issue, and exactly one explicit `Waiver release: v<current-version>` scope in Notes.
4. Test licenses and development public keys are not production issuer evidence.
5. A payment sandbox pass is not a live merchant, refund, tax, or support pass.
6. Keep `docs/pricing.md` as the sole capability matrix; other documents must link to it rather than duplicate it.

## Commands

```powershell
npm run market:readiness
npm run market:readiness:strict
npm run market:readiness:paid
npm run evidence:commercial-loop
npm run evidence:support-privacy-verify
npm run evidence:claims-launch-verify
npm run evidence:commercial-operations-verify
npm run evidence:commercial-operations-seal
npm run evidence:paid-launch-template
npm run delivery:paid-launch
```

The non-strict command validates structure and reports open blockers. Strict mode fails until every `MR-P0` row is `passed` or explicitly `waived`.
Paid-launch mode fails unless every `MR-P0` row is `passed`; use it before taking payment, issuing invoices, publishing paid pricing, or calling a paid plan generally available.
`evidence:commercial-loop` validates a reviewed Free/Plus/Pro subscription activation evidence JSON; it does not create checkout, issue legal approval, or replace commercial owner sign-off.
`evidence:commercial-operations-verify` validates the reviewed legal, payment collection, tax, support, refund, and public-claims operations evidence tying the commercial launch together.
`evidence:commercial-operations-seal` HMAC-seals completed reviewed commercial operations JSON after it passes the same verifier; it refuses templates and is not launch sign-off.
`evidence:paid-launch-template` creates fail-closed template files under `.tmp` so owners have a concrete collection path; it is not reviewed evidence and cannot make a paid launch pass. `evidence:support-privacy-verify` and `evidence:claims-launch-verify` validate reviewed support/privacy rehearsal and public claims evidence JSON. `delivery:paid-launch` combines RC engineering gates with these commercial gates and `market:readiness:paid`; missing evidence is an expected paid-launch blocker.
