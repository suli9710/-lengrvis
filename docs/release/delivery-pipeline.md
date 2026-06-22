# Delivery Pipeline (Closed Loop)

This is the single, ordered, fail-closed delivery chain that turns a commit into a
release-candidate decision. It is implemented by `scripts/delivery_pipeline.py` and
exposed through npm scripts.

## Stages

| Order | Stage | Required | Backing command | Purpose |
| ---: | --- | :---: | --- | --- |
| 1 | qa-gate | yes | `npm run qa:gate` | Backend tests, desktop/mobile typecheck, desktop smoke. |
| 2 | supply-chain | yes | `npm run supply-chain:verify` | Dependency lock verification + SBOM. |
| 3 | security-extensions | yes | `npm run security:extensions` | Extension/skill security gate. |
| 4 | release-safety | yes | `npm run release:safety` | Release safety checks. |
| 5 | readiness | yes | `python scripts/check_release_readiness_dashboard.py` | Validate the readiness dashboard (strict in RC mode). |
| 6 | evidence | no | `npm run evidence:release` | Collect the release evidence packet. |

## Commands

```bash
# Inspect the ordered plan without running anything (safe on any machine).
npm run delivery:plan

# Run the full chain and write build/delivery-verdict.json.
npm run delivery:run

# Release-candidate mode: strict readiness, blocked P0 rows fail the pipeline.
npm run delivery:rc
```

## Verdict contract

The orchestrator prints and optionally writes a JSON verdict:

```json
{
  "strict": true,
  "ok": false,
  "decision": "blocked",
  "required_failures": ["readiness"],
  "optional_failures": [],
  "skipped": ["evidence"],
  "stages": [ { "name": "qa-gate", "status": "passed", "exit_code": 0 } ]
}
```

- `ok=false` and a non-zero exit code whenever any required stage fails.
- Remaining stages are `skipped` after the first required failure unless `--keep-going`.
- `decision` is `passed` only when all required stages pass. `passed` means gates
  cleared, not that the product is released.

## Closed-loop rules

1. A real release candidate must use `delivery:rc` (strict). Non-strict runs are for
   day-to-day development and never authorize a tag or announcement.
2. The pipeline does not replace manual evidence. `RR-P0-001..006` in the readiness
   dashboard still require human-confirmed clean-machine, Android, result-quality,
   and diagnostics evidence.
3. The JSON verdict should be attached to the RC handoff and the release evidence
   packet.
4. Do not weaken a required stage to optional to make the pipeline pass. Use an
   explicit, owner-approved waiver row in the dashboard instead.
5. CI should run `delivery:plan` and the readiness validator on every PR. A real
   release runs `delivery:rc` on the candidate build host.

## What this closes and what it does not

Closes: ordering, fail-closed aggregation, a single verdict artifact, and a clear
mapping from gates to release decision.

Does not close on its own: the real-world execution evidence (clean-machine,
Android device, 30+ task result-quality review, diagnostics external-share review).
Those remain manual P0 rows and must be satisfied before `delivery:rc` can pass in
strict mode.
