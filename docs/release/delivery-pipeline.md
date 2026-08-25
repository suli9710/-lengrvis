# Delivery Pipeline (Closed Loop)

This is the single, ordered, fail-closed delivery chain that turns a commit into a
release-candidate decision. It is implemented by `scripts/delivery_pipeline.py` and
exposed through npm scripts.

Install the root QA toolchain with `npm ci --ignore-scripts --engine-strict` before
running executable stages. The root lock pins the official MCP conformance CLI and
its full transitive graph; CI, the candidate workflow, and the publish preflight job
perform this install explicitly. The reviewed-evidence workflow and clean publish job
do not execute npm.

Formal candidate and publish jobs never execute the third-party conformance CLI in
a privileged runner. `release-candidate.yml` uses a `contents: read` producer to
upload only the raw `checks.json`, then a clean, dependency-free sealer checkout to
validate those bytes and emit `mcp-conformance-job-evidence/v2`. The evidence binds
the repository, commit, candidate run/attempt, root lock digest, conformance version,
Node version, and the complete raw results summary. The candidate RC gate and read-only
publish preflight verify that sealed artifact before installing project dependencies.

Real-provider quality uses the same producer/sealer split. Only the producer enters
the protected `release-candidate` environment, and the provider key exists only on
its evaluation step. The formal `--release-evidence` profile is fixed, unfiltered,
and currently materializes all 25 eligible golden tasks plus 105 versioned benchmark
tasks. A clean sealer with no environment or provider secret recomputes the report
contract and emits `real-llm-quality-evidence/v2`. The candidate bundle, sealed MCP
and real-LLM evidence, and reviewed-evidence bundle are promotion inputs whose
artifact names include the producer run ID and run attempt and whose retention is
fixed at 30 days; raw producer artifacts are retained for 14 days. The
`release-publish-preflight-diagnostics-<run-id>-<run-attempt>` artifact is itself
attempt-bound publish diagnostic output, not a promotion input. Non-strict `delivery:run`
may execute the conformance CLI locally under the
`mcp-conformance-minimal` environment policy; that filtering is development defense
in depth, not a release isolation boundary.

## Stages

| Order | Stage | Required | Backing command | Purpose |
| ---: | --- | :---: | --- | --- |
| 1 | qa-gate | yes | `npm run qa:gate` | Backend tests, desktop/mobile typecheck, desktop smoke. |
| 2 | golden-gate | yes | `npm run golden:gate` | Deterministic golden-task regression gate. |
| 3 | mcp-conformance | yes | Development: `npm run mcp:conformance`; strict/candidate/paid: `python scripts/mcp_conformance_evidence.py verify ...` | Run official MCP initialize/tools/SSE conformance only in development or the unprivileged candidate producer; a clean sealer creates the candidate-bound artifact verified by formal release stages. |
| 4 | maintainability-gate | yes | `npm run maintainability:gate` | Source-size p95 and per-area anti-regrowth gate. |
| 5 | review-scorecard | yes | `npm run review:scorecard` | Validate full-review scorecard totals and prevent 100/100 claims while RR-P0 evidence remains unfinished. |
| 6 | agentic-threat-model | yes | `npm run security:threat-model` | Validate trust boundaries and the OWASP Agentic control/evidence map. |
| 7 | supply-chain | yes | `npm run supply-chain:verify` | Lock verification and SBOM for workspace QA, desktop, mobile, and runtime/development/build/acceleration Python dependencies. |
| 8 | dependency-audit | yes | `npm run audit:deps` | Workspace QA, desktop, and mobile npm audits plus pip-audit over runtime/development/build/acceleration Python locks. |
| 9 | secret-scan | yes | `npm run security:secrets` | Strict gitleaks source snapshot scan. |
| 10 | security-extensions | yes | `npm run security:extensions` | Extension/skill security gate. |
| 11 | release-safety | yes | `npm run release:safety` | Release safety checks. |
| 12 | market-readiness | yes | `python scripts/check_market_readiness.py` | Validate commercial identity, legal, payment, license-issuer, support, and claims readiness (`--paid-launch` only in paid launch mode). |
| 13 | current-release-evidence | no / strict yes | `npm run evidence:current-release` | Generate the current CI/release evidence summary used by strict readiness. |
| 14 | readiness | yes | `python scripts/check_release_readiness_dashboard.py` | Validate the engineering readiness dashboard (`--rc-release` in strict/paid modes). |
| 15 | evidence | no / strict yes | `npm run evidence:release` | Collect the release evidence packet. |

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
The formal real-LLM producer runs
`scripts/run_real_llm_eval.py --quality-gate --release-evidence`. It refuses mock
providers and all task/filter/threshold/report-path overrides, requires the complete
current corpus (25 eligible golden tasks plus 105 benchmark tasks), and writes the
report once without replacement. The clean sealer replays the corpus-derived task
contract, recomputes every summary and quality-gate field, and binds the report and
dataset digests to the exact candidate. The strict `real-llm-eval` stage verifies
that evidence; it does not call a provider.
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
# The formal workflow supplies both isolated quality-evidence artifacts. A manual
# diagnostic must download both artifacts from the exact candidate run/attempt.
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
   In GitHub, rerun all jobs rather than only failed jobs: both machine-quality
   evidence artifacts are bound to `github.run_attempt`, so a partial rerun
   intentionally fails closed when the new attempt has no matching producer output.
   After a candidate succeeds, keep the default branch pinned to that exact candidate
   commit through reviewed-evidence sealing and final publication. GitHub
   `workflow_dispatch` derives `GITHUB_SHA` from its selected branch or tag, while the
   reviewed-evidence job deliberately accepts only the default branch and requires
   that SHA to equal the candidate run's head SHA. If the default branch advances,
   create and review a new candidate rather than reusing the old one. Enforcing and
   recording this release freeze requires protected branch/tag rulesets, named release
   owners, and environment reviewers; the repository cannot supply that personnel
   governance or make a release tag immutable by workflow code alone.
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
   The current-release evidence `Run id` and `Run attempt` fields use that candidate
   identity; a later reviewed-evidence or publish workflow run cannot substitute its
   own run attempt.
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
   package/version, and signer digest. A valid reviewed-evidence Ed25519 signature alone is not
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
   It verifies the requested tag against the immutable candidate identity and
   requires both candidate and reviewed-evidence run IDs/attempts as dispatch inputs.
   Readiness validation uses that explicit identity, but publication additionally
   requires the dispatch SHA, candidate run, reviewed-evidence run, and default branch
   to remain on the same frozen candidate commit. The release remains a draft until a
   separate clean runner, with no checkout, dependency install, or repository script
   execution, redownloads the exact run-attempt candidate artifact and re-verifies all
   eight subjects against both attestation bundles. It then uploads a fixed nine-file
   set and verifies unique names, uploaded state, SHA-256 digests, byte sizes, release
   target, and the recursively peeled remote tag immediately before and after making
   the release public.

## What this closes and what it does not

Closes: ordering, fail-closed aggregation, a single verdict artifact, and a clear
mapping from gates to release decision.

Does not close on its own: real-world execution evidence (clean-machine, Android
device, result-quality review, diagnostics review) or the external substance behind
commercial operations (lawyer, tax, payment processor, support staffing). Those
remain manual P0 responsibilities, but paid launch mode now requires signed
commercial operations evidence before `market:readiness:paid` can pass. Provider
 quality producers and their sealers are isolated. Final GitHub publication is also
 isolated in a clean, single-step job that alone receives `contents: write`; the
 repository-code preflight remains `contents: read`. Candidate signing and attestation
 still execute after repository-controlled build code in the candidate job, so a
 future trust-partitioning change should move those two operations into a clean job
 that consumes only allowlisted, digest-bound subjects. Repository/environment secrets
 and explicit shell/`gh` CLI token injection are scoped to the steps that need them.
 The GitHub-created `GITHUB_TOKEN` remains a job-level capability governed by each
 job's minimum `permissions`; omitting an explicit `GH_TOKEN` environment variable does
 not make `github.token` unavailable to actions. The before/after remote-tag checks
 detect drift during publication, but protected tag rulesets and tightly governed
 `contents: write` access remain external requirements for an immutable release ref.
