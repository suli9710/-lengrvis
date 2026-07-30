# Commercial Launch Baseline

Last captured: 2026-07-12

This file records the current paid/public launch baseline for the maintainability
and product-completeness push. It is a blocker inventory, not release evidence
and not owner sign-off.

## Commands Run

```powershell
npm run delivery:plan
npm run market:readiness:paid
npm run release:readiness:rc
npm run evidence:paid-launch-template
```

## Paid Launch Blockers

`npm run market:readiness:paid` currently fails because every MR-P0 row in
`docs/business/market-readiness.md` is `blocked`: the earlier v0.1.1
maintenance-only waivers do not apply to v0.1.2. Paid/public launch requires all
six rows to be `passed`:

- `MR-P0-001` Contracting identity and tax.
- `MR-P0-002` Legal approval.
- `MR-P0-003` Checkout and subscription lifecycle.
- `MR-P0-004` License issuer operations.
- `MR-P0-005` Support and privacy operations.
- `MR-P0-006` Claims and launch assets.

## RC Release Blockers

`npm run release:readiness:rc` currently fails because all seven release P0 rows
in `docs/release/release-readiness-dashboard.md` are `in_progress`:

- `RR-P0-001` Clean-machine Windows install.
- `RR-P0-002` Local model clean-machine path.
- `RR-P0-003` Mobile real-device LAN/WSS evidence.
- `RR-P0-004` Natural-language result quality.
- `RR-P0-005` Diagnostics external-share review.
- `RR-P0-006` Public Beta/RC handoff and release-owner sign-off.
- `RR-P0-007` Agentic threat model and OWASP control map.

The same command also reports that `docs/release/current-release-evidence.md`
is stale for the checked-out HEAD and still has incomplete machine gates,
pending manual sign-off, and a pending owner signature.

## Template Outputs

`npm run evidence:paid-launch-template` generated fail-closed templates under:

- `.tmp/paid-launch-evidence-templates/support-privacy-operations-evidence.template.json`
- `.tmp/paid-launch-evidence-templates/claims-launch-evidence.template.json`
- `.tmp/paid-launch-evidence-templates/commercial-loop-evidence.template.json`
- `.tmp/paid-launch-evidence-templates/commercial-operations-evidence.template.json`
- `.tmp/paid-launch-evidence-templates/paid-launch-evidence-templates.md`

These files are collection aids only. They are not reviewed evidence and cannot
make `release:paid-launch` pass until the required external legal, tax, payment,
support, privacy, claims, production license, clean-machine, real-device, and
manual review artifacts are filled and verified.
