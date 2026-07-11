# Full Review Scorecard

Last reviewed: 2026-07-09

This scorecard records the last clean-candidate full-repository review baseline
and the path to 100/100. It is review evidence, not release sign-off.

Machine check: `npm run review:scorecard`; it fails closed on a dirty worktree.
`--allow-dirty` is document-only validation and must not be used by CI or a
release workflow.
For any 100/100 claim, the machine check also requires strict RC release
readiness to pass against current release evidence.

## Last Verified Clean-Candidate Score

| Area | Score | Evidence |
| --- | ---: | --- |
| Backend correctness and safety | 25 / 25 | `python -m pytest backend/tests` passed with 2834 passed and 12 skipped; `python -m ruff check backend` passed. |
| Desktop quality gates | 15 / 15 | `npm --prefix desktop run typecheck`, `npm --prefix desktop test`, `npm --prefix desktop run build:renderer`, and `npm --prefix desktop run smoke` passed. |
| Mobile companion gates | 15 / 15 | `npm --prefix mobile run typecheck` and mobile smokes for token, task companion, remote input grant, wakeup, Android back, hardening, manifest resources, and LAN TLS passed. |
| Supply-chain and security gates | 15 / 15 | `npm run deps:verify`, `npm run audit:deps`, `npm run security:extensions`, `npm run security:secrets`, and `npm run sbom:generate` passed. |
| Maintainability | 14 / 15 | `npm run maintainability:gate` passed. Large legacy modules remain intentionally allowlisted and tracked in `docs/architecture/maintainability-hotspots.md`. |
| Release readiness evidence | 10 / 15 | Non-strict readiness and market readiness pass. Strict RC readiness remains blocked by missing real-world/manual evidence and stale current-release evidence for this dirty worktree. |

Total: 94 / 100.

## Findings

This baseline is not a live claim about an uncommitted working tree. A source,
test, or configuration change must pass the relevant gates and be committed
before this score can be used as candidate evidence.

The remaining score gap is evidence-driven:

- `npm run release:readiness:strict` fails while all seven `RR-P0-*` rows are
  `in_progress`.
- `npm run release:readiness:rc` fails for the same RR-P0 evidence gaps.
- `npm run evidence:diagnostics-verify` fails until
  `build/diagnostics-external-review-evidence-reviewed.json` exists and validates.
- `docs/release/current-release-evidence.md` still records CI evidence for commit
  `2bbfe6613ecba87c535e13225b3a6d474775f7dd`, while the current checked-out HEAD
  during this review was `307c968e`.
- Strict RC readiness now also rejects stale current-release evidence unless its
  execution-command table includes `npm run review:scorecard`.
- Strict readiness requires a clean worktree, current candidate evidence,
  `machine_gates_passed`, and release-owner approval/signature.

## Path To 100

To move from 94/100 to 100/100, collect and verify the missing release evidence
instead of weakening gates:

1. Produce current CI release evidence for the final clean candidate commit.
2. Complete reviewed clean-machine Windows evidence.
3. Complete reviewed local-model clean-machine evidence or record the accepted
   blocked handoff artifact.
4. Complete reviewed Android APK/device or emulator LAN/WSS evidence.
5. Complete 30+ task result-quality reviewed evidence.
6. Complete signed diagnostics external-review evidence for the actual exported
   package contents.
7. Run `npm run security:threat-model` for the final candidate and record
   security/release-owner review of open residual risk.
8. Complete public Beta/RC handoff and release-owner approval/signature.
9. Re-run `npm run release:readiness:rc` and require exit code 0.

Do not mark this scorecard 100/100 while any strict RR-P0 release evidence row is
missing, stale, unsigned, or based only on a template/preflight helper.
