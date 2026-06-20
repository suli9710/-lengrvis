# Supply Chain Gate

Date: 2026-06-19

Scope: dependency lock, SBOM, and audit evidence for the current repository. This page complements the release gate without turning supply-chain inventory into release sign-off.

## Audit Mapping

| Audit item | Status | Evidence | Remaining gap |
| --- | --- | --- | --- |
| P1-3 dependency lock evidence | Covered | Root `npm run deps:verify`, `scripts/verify_dependency_locks.ps1`, `desktop/package-lock.json`, `mobile/package-lock.json`, `backend/requirements-lock.txt` | Lock verification does not prove registry provenance, package signatures, or hash pinning. |
| Python transitive lock | Covered | `backend/requirements-lock.txt` is a fully resolved `uv pip compile` lock and the verifier now requires resolver provenance (`# via ...`) plus more pinned packages than direct requirements. | Regenerate with the documented `uv pip compile` command whenever `backend/requirements.txt` changes. |
| SBOM evidence | Covered | `npm run sbom:generate`, `scripts/generate_sbom.ps1`, `scripts/generate_sbom.py`, CI artifact `current-sbom` (`.tmp/sbom/lengrvis-sbom.cdx.json`) | SBOM is inventory only; vulnerability, license, and provenance review remain separate evidence. |
| Vulnerability audit evidence | Covered by separate audit workflow | `npm run audit:deps`, `.github/workflows/security-audit.yml` | Weekly/manual SCA is not the same artifact as the SBOM and still needs reviewed output for a candidate. |

## Current Automated Coverage

| Area | Current evidence | What it proves | What it does not prove |
| --- | --- | --- | --- |
| Desktop npm lock | `scripts/verify_dependency_locks.ps1` parses `desktop/package.json` and `desktop/package-lock.json` | Lockfile exists, root name/version match, direct manifest dependency specs match lock root, and direct packages exist in lock packages | It does not prove registry provenance, signing, or license acceptability. |
| Mobile npm lock | `scripts/verify_dependency_locks.ps1` parses `mobile/package.json` and `mobile/package-lock.json` | Same as desktop lock verification | It does not prove Expo/native dependency installability on every target device. |
| Backend Python transitive lock | `backend/requirements-lock.txt` plus `scripts/verify_dependency_locks.ps1` | All Python lock entries are pinned, all direct requirements are present, and the lock has resolver provenance for transitive packages | It does not include hash pins or package signatures. |
| SBOM | `scripts/generate_sbom.py` reads backend Python lock plus desktop/mobile npm locks and emits CycloneDX 1.5 JSON | A repeatable machine-generated component inventory tied to the current commit | It is not a vulnerability report, license approval, or release-owner sign-off. |
| CI evidence | `.github/workflows/ci.yml` `supply-chain` job and final `release-evidence` job | Push/PR CI records lock/SBOM status and uploads `current-sbom` plus `current-release-evidence` | CI artifacts still need manual review before release sign-off. |

## Required Evidence When Dependencies Change

Record these fields in QA handoff when `package.json`, `package-lock.json`, `backend/requirements.txt`, or `backend/requirements-lock.txt` changes:

| Field | Required value |
| --- | --- |
| Lock command | `npm run deps:verify` |
| SBOM command | `npm run sbom:generate` |
| Date | Local date of execution |
| Commit/worktree | Commit SHA if available, otherwise dirty-worktree note |
| Result | Exit code and key output lines |
| Scope note | State whether the change touched desktop npm, mobile npm, backend Python, or multiple areas |
| Artifact | `.tmp/sbom/lengrvis-sbom.cdx.json` locally, or CI artifact `current-sbom` |

## Recommended Manual Evidence

These commands remain recommended candidate evidence alongside lock/SBOM generation:

```powershell
npm run audit:deps
npm --prefix desktop audit --audit-level=high
npm --prefix mobile audit --audit-level=high
```

For Python vulnerability review, use the existing `pip-audit -r backend/requirements-lock.txt` path through `npm run audit:deps`. If `pip-audit` is missing or intentionally skipped, record the waiver owner, reason, expiry condition, and follow-up task before any release claim.

## Remaining Gaps

| Gap | Gate language to use |
| --- | --- |
| No package provenance/signature verification | "Lock/SBOM evidence records package identity and versions only; provenance and signatures remain separate residual risk unless reviewed." |
| No hash-pinned Python lock | "Python transitive dependencies are fully resolved and pinned by version, but package hashes are not enforced." |
| Vulnerability output not attached for a candidate | "SBOM generated; vulnerability audit output still needs candidate-specific evidence." |
| License review not automated | "SBOM inventory exists; license review remains manual unless a candidate-specific license scan is attached." |

## Suggested Evidence Commands

```powershell
npm run deps:verify
npm run sbom:generate
```
