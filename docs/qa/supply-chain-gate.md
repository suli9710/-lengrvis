# Supply Chain Gate

Date: 2026-06-19

Scope: dependency lock, SBOM, and audit evidence for the current repository. This page complements the release gate without turning supply-chain inventory into release sign-off.

## Audit Mapping

| Audit item | Status | Evidence | Remaining gap |
| --- | --- | --- | --- |
| P1-3 dependency lock evidence | Covered | Root `npm run deps:verify`, `scripts/verify_dependency_locks.ps1`, workspace QA `package.json` / `package-lock.json`, `desktop/package-lock.json`, `mobile/package-lock.json`, `backend/requirements-lock.txt`, `requirements-dev-lock.txt`, `backend/requirements-build-lock.txt`, `scripts/acceleration-requirements-lock.txt` | Lock verification checks npm transitive SRI integrity and allowlisted HTTPS registry sources, but does not prove package signatures, trusted-builder provenance, or license approval. |
| Python transitive locks | Covered | Runtime, development, backend build, and acceleration locks are fully hash-pinned; the verifier requires direct dependency coverage and `sha256` hashes for every package, and the development lock must contain every runtime pin at the same version. | Regenerate with the documented `uv pip compile --generate-hashes` command whenever the matching requirements file changes. |
| SBOM evidence | Covered | `npm run sbom:generate`, `scripts/generate_sbom.ps1`, `scripts/generate_sbom.py`, CI artifact `current-sbom` (`.tmp/sbom/lengrvis-sbom.cdx.json`) | SBOM includes runtime/development/build/acceleration Python hashes, npm integrity hashes, and npm license metadata when lockfiles provide it; vulnerability, license approval, and provenance review remain separate evidence. |
| Vulnerability and secret audit evidence | Covered by release and audit workflows | `npm run audit:deps`, `npm run security:secrets`, `.github/workflows/security-audit.yml`, `.gitleaks-ci.toml` | Scheduled/PR SCA and gitleaks are still not the same artifact as release-owner review and need reviewed output for a candidate. |
| Skill/MCP extension supply chain | Covered by extension security gate | `npm run security:extensions`, `scripts/verify_skill_mcp_supply_chain.py`, `scripts/verify_extension_security_gate.ps1`, `backend/tests/test_app_skill_protocol.py`, `backend/tests/test_skill_routes.py`, `backend/tests/test_mcp_client.py` | This proves release-profile controls and writes a redacted gate artifact; release-owner sign-off and candidate-specific package/server approval remain separate. |

## Current Automated Coverage

| Area | Current evidence | What it proves | What it does not prove |
| --- | --- | --- | --- |
| Workspace QA npm lock | `scripts/verify_dependency_locks.ps1` parses root `package.json` and `package-lock.json` | Release and QA tooling has an exact lock; root name/version and direct manifest specs match; direct packages exist; and every transitive non-link package has SRI integrity and an allowlisted HTTPS npm registry source | It inventories the QA/delivery toolchain, not dependencies shipped in the desktop or mobile runtime, and does not prove package signatures, trusted-builder provenance, or license acceptability. |
| Desktop npm lock | `scripts/verify_dependency_locks.ps1` parses `desktop/package.json` and `desktop/package-lock.json` | Lockfile exists, root name/version match, direct manifest dependency specs match lock root, direct packages exist in lock packages, every transitive non-link package has SRI integrity, and each resolved source is an allowlisted HTTPS npm registry URL | It does not prove package signatures, trusted-builder provenance, or license acceptability. |
| Mobile npm lock | `scripts/verify_dependency_locks.ps1` parses `mobile/package.json` and `mobile/package-lock.json` | Same as desktop lock verification | It does not prove Expo/native dependency installability on every target device. |
| Python transitive locks | `backend/requirements-lock.txt`, `requirements-dev-lock.txt`, `backend/requirements-build-lock.txt`, `scripts/acceleration-requirements-lock.txt`, plus `scripts/verify_dependency_locks.ps1` | All Python lock entries are pinned, all direct requirements are present, every package carries at least one `sha256` hash, and development/runtime pins agree | It does not prove package signatures or trusted-builder provenance. |
| SBOM | `scripts/generate_sbom.py` reads runtime/development/build/acceleration Python locks plus workspace QA, desktop, and mobile npm locks and emits CycloneDX 1.5 JSON | A repeatable machine-generated component inventory tied to the current commit, including Python sha256 hashes and npm lock integrity hashes; root toolchain components carry `lengrvis:npm_project=workspace-qa` | It is not a vulnerability report, license approval, provenance proof, or release-owner sign-off. |
| CI evidence | `.github/workflows/ci.yml` `supply-chain` job and final `release-evidence` job | Push/PR CI records lock/SBOM status and uploads `current-sbom` plus `current-release-evidence` | CI artifacts still need manual review before release sign-off. |
| Skill/MCP release-profile gate | `scripts/verify_skill_mcp_supply_chain.py` and `npm run security:extensions` | Unsigned Skills are rejected when `LENGRVIS_SKILL_REQUIRE_TRUSTED_SIGNATURES=true`, signed Skills verify against configured trusted keys, Skill imports can require explicit permission-diff review with audit evidence, and MCP configs can require owner, policy id, and explicit `allowed_tools` | It does not prove a public marketplace, enterprise policy rollout, or release-owner sign-off for a specific third-party package/server. |

## Required Evidence When Dependencies Change

Record these fields in QA handoff when `package.json`, `package-lock.json`, runtime/development/build Python requirements or locks, or acceleration requirements/locks change:

| Field | Required value |
| --- | --- |
| Lock command | `npm run deps:verify` |
| SBOM command | `npm run sbom:generate` |
| Vulnerability audit command | `npm run audit:deps` |
| Secret scan command | `npm run security:secrets` |
| Date | Local date of execution |
| Commit/worktree | Commit SHA if available, otherwise dirty-worktree note |
| Result | Exit code and key output lines |
| Scope note | State whether the change touched workspace QA npm, desktop npm, mobile npm, backend Python, or multiple areas |
| Artifact | `.tmp/sbom/lengrvis-sbom.cdx.json` locally, or CI artifact `current-sbom` |

## Recommended Manual Evidence

These commands remain recommended candidate evidence alongside lock/SBOM generation:

```powershell
npm run audit:deps
npm run security:secrets
npm audit --audit-level=high
npm --prefix desktop audit --audit-level=high
npm --prefix mobile audit --audit-level=high
```

For extension supply chain evidence, run:

```powershell
npm run security:extensions
python scripts/verify_skill_mcp_supply_chain.py
```

For a release profile, set `LENGRVIS_SKILL_REQUIRE_TRUSTED_SIGNATURES=true`,
`LENGRVIS_SKILL_REQUIRE_PERMISSION_DIFF_REVIEW=true`, and
`LENGRVIS_MCP_REQUIRE_OWNER_POLICY=true`. MCP server entries must include
`owner`, `policy_id`, and explicit `allowed_tools`.

For Python vulnerability review, use `npm run audit:deps`. It audits `backend/requirements-lock.txt`, `requirements-dev-lock.txt`, `backend/requirements-build-lock.txt`, and `scripts/acceleration-requirements-lock.txt` with the current Python platform environment markers; any `pip-audit` finding or audit error fails closed. Windows RC evidence covers Windows-applicable pinned entries, while macOS/Linux-only marker dependencies need a matching platform audit or an explicit OSV/multi-platform scanner artifact before cross-platform vulnerability coverage is claimed. If `pip-audit` is missing or intentionally skipped, record the waiver owner, reason, expiry condition, and follow-up task before any release claim.

## Remaining Gaps

| Gap | Gate language to use |
| --- | --- |
| No package provenance/signature verification | "Lock/SBOM evidence records package identity and versions only; provenance and signatures remain separate residual risk unless reviewed." |
| Vulnerability output not attached for a candidate | "SBOM generated; vulnerability audit output still needs candidate-specific evidence." |
| License review not fully automated | "SBOM inventory includes npm lockfile license metadata where present; release license approval remains manual unless a candidate-specific license scan is attached." |
| No candidate-specific Skill/MCP owner approval attached | "Skill/MCP release-profile controls passed; candidate-specific Skill package/server owner approval still needs release-owner review before sign-off." |

## Suggested Evidence Commands

```powershell
npm run deps:verify
npm run audit:deps
npm run security:secrets
npm run sbom:generate
```
