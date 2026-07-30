# Delivery Pipeline (Closed Loop)

This is the single, ordered, fail-closed delivery chain that turns a commit into a
release-candidate decision. It is implemented by `scripts/delivery_pipeline.py` and
exposed through npm scripts.

## Stages

| Order | Stage | Required | Backing command | Purpose |
| ---: | --- | :---: | --- | --- |
| 1 | qa-gate | yes | `npm run qa:gate` | Backend tests, desktop/mobile typecheck, desktop smoke. |
| 2 | golden-gate | yes | `npm run golden:gate` | Deterministic golden-task regression gate. |
| 3 | mcp-conformance | yes | `npm run mcp:conformance` | Official MCP initialize, tools/call, and SSE retry/Last-Event-ID client conformance. |
| 4 | maintainability-gate | yes | `npm run maintainability:gate` | Source-size p95 and per-area anti-regrowth gate. |
| 5 | review-scorecard | yes | `npm run review:scorecard` | Validate full-review scorecard totals and prevent 100/100 claims while RR-P0 evidence remains unfinished. |
| 6 | agentic-threat-model | yes | `npm run security:threat-model` | Validate trust boundaries and the OWASP Agentic control/evidence map. |
| 7 | supply-chain | yes | `npm run supply-chain:verify` | Dependency lock verification + SBOM. |
| 8 | dependency-audit | yes | `npm run audit:deps` | npm audit plus pip-audit over runtime/build/acceleration Python locks. |
| 9 | secret-scan | yes | `npm run security:secrets` | Strict gitleaks source snapshot scan. |
| 10 | security-extensions | yes | `npm run security:extensions` | Extension/skill security gate. |
| 11 | release-safety | yes | `npm run release:safety` | Release safety checks. |
| 12 | market-readiness | yes | `python scripts/check_market_readiness.py` | Validate commercial identity, legal, payment, license-issuer, support, and claims readiness (`--paid-launch` only in paid launch mode). |
| 13 | current-release-evidence | no / strict yes | `npm run evidence:current-release` | Generate the current CI/release evidence summary used by strict readiness. |
| 13 | readiness | yes | `python scripts/check_release_readiness_dashboard.py` | Validate the engineering readiness dashboard (`--rc-release` in strict/paid modes). |
| 14 | evidence | no / strict yes | `npm run evidence:release` | Collect the release evidence packet. |

Non-strict `delivery:run` inserts required `release-artifact-preflight` and
`signed-artifacts` stages after `release-safety` unless `--skip-signature-verify` is
passed. Strict RC mode (`delivery:rc`) always runs `signed-artifacts` and ignores
`--skip-signature-verify`.

Strict RC mode inserts additional required stages after golden/safety/artifact checks:
`real-llm-eval`, `candidate-binding-context`, `release-owner-signature`,
`packaging-verify`, `signed-artifacts`, `distribution-evidence`,
`clean-machine-evidence`, `result-quality-evidence`, `diagnostics-evidence`, and
`android-strict-gate`.
`candidate-binding-context` requires an explicit immutable candidate identity and
checks its full commit against the checkout. The three reviewed-evidence stages run
their Python validators with `--require-candidate-binding`; they reject a validly
signed artifact from any other candidate rather than accepting it as replayable
evidence. `diagnostics-evidence` validates the signed
`diagnostics-external-review-evidence-reviewed` artifact; the machine chain can be
ready while this stage still blocks on the actual package human content-review
artifact.
`release-owner-signature` verifies a detached Ed25519 signature over a canonical
payload containing the repository, release tag, full candidate commit, candidate
run/attempt, reviewed-evidence run/attempt, immutable build identifier, release
owner, and manual sign-off status. A non-empty approval string is not sufficient.
The production environment must provide the public key through
`LENGRVIS_RELEASE_OWNER_PUBLIC_KEY`; the private key stays offline.
The real-LLM stage runs `scripts/run_real_llm_eval.py --quality-gate`, which refuses
mock providers and requires at least 20 eligible `runs` / `chat` tasks to run before
the quality metrics can pass. It also records numerator/denominator counts for each
quality rate and fails strict RC when a core metric is measured on too small a sample.
Paid-launch mode adds `commercial-loop`, `support-privacy-evidence`,
`claims-launch-evidence`, and `commercial-operations-evidence`, then runs market
readiness with `--paid-launch`.
These stages require reviewed evidence JSON and real Android APK/device evidence;
template/preflight outputs intentionally fail them.
Strict RC mode also upgrades both `current-release-evidence` and `evidence` from
optional to required. The current-release evidence generator records the
pre-generation git worktree status, and strict sign-off fails unless that status
is `clean`, so dirty-worktree candidates cannot be promoted by commit SHA alone.

Windows RC signing order (`.github/workflows/release-candidate.yml`):

1. `npm run review:scorecard` verifies the full-review scorecard before any release artifacts are built.
2. `npm run security:threat-model` verifies the Agentic trust and control map before artifacts are built.
3. `build_all.ps1` builds backend, portable tree, zip, and self-extracting EXE.
4. `sign_windows_backend.ps1` signs `dist/backend.exe` and copies it into the portable tree.
5. `refresh_portable_release_bundle.ps1` re-compresses portable and rebuilds the self-extracting EXE.
6. `sign_windows_portable_artifacts.ps1` signs the portable launcher and self-extracting EXE.
7. `desktop dist:signed` signs Electron installer artifacts.

## Commands

```bash
# Inspect the ordered plan without running anything (safe on any machine).
npm run delivery:plan

# Run the full chain and write build/delivery-verdict.json.
npm run delivery:run

# Release-candidate mode: strict engineering and market readiness; blocked P0 rows fail the pipeline.
npm run delivery:rc

# Paid/public launch mode: RC engineering evidence plus passed MR-P0 commercial evidence.
npm run delivery:paid-launch

# With the exact candidate identity variables set, emit the canonical bytes that
# the release owner signs offline. This command never accepts a private key.
npm run release:owner-signoff-payload
```

## Verdict contract

The orchestrator prints and optionally writes a JSON verdict:

```json
{
  "strict": true,
  "skip_signature_verify": false,
  "warnings": [],
  "ok": false,
  "decision": "blocked",
  "required_failures": ["market-readiness"],
  "optional_failures": [],
  "skipped": ["evidence"],
  "stages": [ { "name": "qa-gate", "status": "passed", "exit_code": 0 } ]
}
```

- `warnings` records delivery policy deviations (for example skipped signature
  verification in non-strict mode, or `--skip-signature-verify` ignored in strict RC).

- `ok=false` and a non-zero exit code whenever any required stage fails.
- Remaining stages are `skipped` after the first required failure unless `--keep-going`.
- `decision` is `passed` only when all required stages pass. `passed` means gates
  cleared, not that the product is released.

## Closed-loop rules

1. A real release candidate must use `delivery:rc` (strict). Non-strict runs are for
   day-to-day development and never authorize a tag or announcement.
2. A paid/public launch must use `delivery:paid-launch`; `delivery:rc` is necessary
   but not sufficient to accept payment, issue invoices, publish paid pricing, or
   call a paid plan generally available. No-sale RCs must not be blocked on
   commercial-loop evidence while the market dashboard records scoped no-sale
   waivers.
3. The pipeline does not replace manual evidence. `RR-P0` engineering rows and
   `MR-P0` commercial rows still require their named real-world artifacts and owners.
4. Strict Android evidence is supplied through `LENGRVIS_ANDROID_APK_PATH` and
   `LENGRVIS_ANDROID_REAL_DEVICE_EVIDENCE_PATH`; strict reviewed evidence checkers use
   their default build paths or `LENGRVIS_*_EVIDENCE_PATH` overrides. All strict
   reviewed evidence must also match the explicit candidate context:
   `LENGRVIS_RELEASE_CANDIDATE_COMMIT`,
   `LENGRVIS_RELEASE_BUILD_IDENTIFIER`,
   `LENGRVIS_RELEASE_CANDIDATE_REPOSITORY`,
   `LENGRVIS_RELEASE_CANDIDATE_RUN_ID`, and
   `LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT`. The build identifier is immutable and
   must be `rc-<run-id>-<attempt>-<full-40-char-commit>`.
   Promotion also requires `LENGRVIS_REVIEWED_EVIDENCE_RUN_ID`,
   `LENGRVIS_REVIEWED_EVIDENCE_RUN_ATTEMPT`,
   `LENGRVIS_RELEASE_OWNER_PUBLIC_KEY`, and a detached
   `RELEASE_OWNER_SIGNATURE`, each bound to the same canonical owner-signoff
   payload. Strict current-release evidence records the payload digest and public
   key fingerprint and fails unless cryptographic verification succeeds.
   `LENGRVIS_ANDROID_RELEASE_CERTIFICATE_SHA256` must come from the protected
   production environment. Strict validation does not discover tools from `PATH`:
   it requires one approved `build-tools/<version>` root, the expected version,
   and protected SHA-256 values for `apksigner.bat`, `apksigner.jar`, and
   `aapt2.exe`. Only that verified toolchain may run `apksigner verify --verbose
   --print-certs` and `aapt` inspection. The gate requires v2 and v3 signatures,
   compares the signer certificate SHA-256 to the controlled identity, matches
   package/version to `mobile/app.json`, and inspects the final binary manifest
   for debug, test-only, backup, cleartext, and exported-component safety.
   The sealed reviewed evidence must also contain `app.provenance` binding the
   candidate source, reviewed builder invocation and timestamp, APK digest,
   package/version, and signer digest. A valid reviewed-evidence HMAC alone is not
   APK code-signing verification.
5. The JSON verdict should be attached to the RC handoff and the release evidence
   packet.
6. Do not weaken a required stage to optional to make the pipeline pass. Use an
   explicit, owner-approved waiver row in the dashboard instead.
7. CI should run `delivery:plan`, `npm run review:scorecard`, and both readiness
validators on every PR. A real release runs `delivery:rc` on the candidate
build host.
8. A GitHub Release is dispatched manually only after a signed candidate. The
   publish workflow derives current-release machine evidence from actual
   preflight step outcomes; it must never substitute an all-success JSON value.
   It checks out the requested tag, derives the candidate commit from that checkout,
   and requires the reviewed candidate run ID and attempt as dispatch inputs before
   strict delivery can begin.

## What this closes and what it does not

Closes: ordering, fail-closed aggregation, a single verdict artifact, and a clear
mapping from gates to release decision.

Does not close on its own: real-world execution evidence (clean-machine, Android
device, result-quality review, diagnostics review) or the external substance behind
commercial operations (lawyer, tax, payment processor, support staffing). Those
remain manual P0 responsibilities, but paid launch mode now requires signed
commercial operations evidence before `market:readiness:paid` can pass.
