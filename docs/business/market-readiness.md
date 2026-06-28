# Market Readiness Dashboard

This is the fail-closed source of truth for selling, invoicing, licensing, or publicly advertising a paid Lengrvis offer. Engineering release readiness and commercial readiness are separate gates: a signed, tested build is not automatically ready to sell.

Status values are restricted to `blocked`, `in_progress`, `passed`, and `waived`.

## Current decision

| Field | Value |
| --- | --- |
| Commercial launch decision | `waived for v0.1.1 maintenance packaging only; no paid/public commercial launch` |
| Target market | `TBD` |
| Contracting entity | `TBD` |
| Commercial owner | `suli9710` |
| Last reviewed UTC | `2026-06-27T12:10:00Z` |

## Stop-sell blockers

| ID | Area | Required evidence | Status | Artifact / link label | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| MR-P0-001 | Contracting identity and tax | Registered contracting identity, public business address where required, tax/invoice treatment, approved billing descriptor, and non-personal legal/privacy/support contacts. | waived | docs/business/market-readiness.md | suli9710 | Waived until 2026-07-27; reason: v0.1.1 is maintenance packaging only and accepts no payment, invoices, or paid public launch. Follow-up issue: complete contracting/tax evidence before any paid launch. |
| MR-P0-002 | Legal approval | Counsel-approved EULA, privacy policy, refund policy, DPA/SLA applicability, consumer withdrawal terms, and documented supported jurisdictions. | waived | docs/legal/README.md | suli9710 | Waived until 2026-07-27; reason: v0.1.1 makes no paid/public commercial claim. Follow-up issue: complete legal approval before public paid launch. |
| MR-P0-003 | Checkout and subscription lifecycle | Reviewed `commercial-loop-evidence-reviewed` accepted by `npm run evidence:commercial-loop`: subscription key creation, first activation, renewal refresh, cancellation, refund/revocation, chargeback runbook, invoice/receipt, and tax evidence. | waived | docs/business/market-readiness.md | suli9710 | Waived until 2026-07-27; reason: no checkout, subscription, renewal, invoice, refund, or chargeback flow is offered for v0.1.1. Follow-up issue: integrate and test payment lifecycle before paid launch. |
| MR-P0-004 | License issuer operations | Reviewed `commercial-loop-evidence-reviewed` accepted by `npm run evidence:commercial-loop`: production Ed25519 public key, activation API HTTPS, activation key hash storage, private key custody, issuance log, renewal/replacement/revocation rehearsals, and no runtime private key. | waived | docs/business/license-operations.md; license-admin tests; Settings commerce panel | suli9710 | Waived until 2026-07-27; reason: v0.1.1 does not issue production paid licenses. Follow-up issue: complete real key custody, fingerprint, delivery/update channel, activation server evidence, and rehearsal evidence before paid launch. |
| MR-P0-005 | Support and privacy operations | Published support scope, intake channel, severity routing, data-subject request runbook, diagnostic-package handling, deletion guidance, retention, and response ownership tested. | waived | docs/business/support-privacy-operations.md; Settings privacy data panel; privacy erase tests | suli9710 | Waived until 2026-07-27; reason: v0.1.1 is not a commercial support launch. Follow-up issue: name monitored support owners, retention, jurisdiction guidance, and rehearsal evidence before paid/public launch. |
| MR-P0-006 | Claims and launch assets | Approved pricing page, feature matrix matched to entitlement tests, platform/preview labels, security and privacy claims review, release notes, onboarding, and rollback communication. | waived | docs/pricing.md; docs/release/release-readiness-dashboard.md | suli9710 | Waived until 2026-07-27; reason: v0.1.1 claims remain limited to maintenance packaging and existing preview labels. Follow-up issue: approve public claims, release notes, onboarding, and rollback communication before launch. |

## Technical commercialization evidence

- Backend entitlement gates define Free, Pro, and Max capability boundaries.
- Remote input remains subject to per-action strong approval after entitlement checks.
- Offline commercial licenses use Ed25519 verification; the runtime does not contain the private signing key.
- First activation can call `LENGRVIS_ACTIVATION_BASE_URL/api/v1/activations`; the server stores only activation-key hashes and returns signed licenses.
- Desktop settings expose plan, license state, enabled capabilities, quota state, subject, seats, and expiry.
- License import rejects invalid, expired, oversized, or deployment-managed replacements before persistence.
- The offline issuer CLI creates encrypted Ed25519 keys, stable license IDs, token-free hash-chained issuance logs, replacement links, and signed revocation manifests.
- Runtime revocation data is signature-verified; a matching ID or invalid manifest disables paid entitlements.
- Commercial release profiles require a valid public key and reject runtime private/deprecated signing keys.
- Cloud usage metering and default fail-closed token enforcement exist: Free is capped at rolling 5h/5M plus 7d/20M tokens, Pro at 24h/10M tokens, and Max at 24h/100M tokens. Public paid quota claims still require billing, overage, and cost-owner approval before launch.

## Rules

1. Do not accept payment, issue invoices, publish paid pricing, or call a plan generally available unless every `MR-P0` row is `passed`; `waived` is only valid for no-sale maintenance packaging or explicitly excluded surfaces.
2. `passed` requires an artifact label and named owner.
3. `waived` requires a named owner, reason, expiry, and follow-up issue in Notes.
4. Test licenses and development public keys are not production issuer evidence.
5. A payment sandbox pass is not a live merchant, refund, tax, or support pass.
6. Keep `docs/pricing.md` as the sole capability matrix; other documents must link to it rather than duplicate it.

## Commands

```powershell
npm run market:readiness
npm run market:readiness:strict
npm run market:readiness:paid
npm run evidence:commercial-loop
```

The non-strict command validates structure and reports open blockers. Strict mode fails until every `MR-P0` row is `passed` or explicitly `waived`.
Paid-launch mode fails unless every `MR-P0` row is `passed`; use it before taking payment, issuing invoices, publishing paid pricing, or calling a paid plan generally available.
`evidence:commercial-loop` validates a reviewed Free/Pro/Max subscription activation evidence JSON; it does not create checkout, issue legal approval, or replace commercial owner sign-off.
