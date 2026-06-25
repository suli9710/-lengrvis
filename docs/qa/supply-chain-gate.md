# Supply Chain Gate

Date: 2026-06-19

Scope: dependency lock, SBOM, and audit evidence for the current repository. This page complements the release gate without turning supply-chain inventory into release sign-off.

## Audit Mapping

| Audit item | Status | Evidence | Remaining gap |
| --- | --- | --- | --- |
| P1-3 dependency lock evidence | Covered | Root `npm run deps:verify`, `scripts/verify_dependency_locks.ps1`, `desktop/package-lock.json`, `mobile/package-lock.json`, `backend/requirements-lock.txt` | Lock verification does not prove registry provenance or package signatures. |
| Python transitive lock | Covered | `backend/requirements-lock.txt` is a fully resolved `uv pip compile --generate-hashes` lock and the verifier requires resolver provenance (`# via ...`), direct dependency coverage, and `sha256` hashes for every package. | Regenerate with the documented `uv pip compile --generate-hashes` command whenever `backend/requirements.txt` changes. |
| SBOM evidence | Covered | `npm run sbom:generate`, `scripts/generate_sbom.ps1`, `scripts/generate_sbom.py`, CI artifact `current-sbom` (`.tmp/sbom/lengrvis-sbom.cdx.json`) | SBOM includes Python hashes, npm integrity hashes, and npm license metadata when lockfiles provide it; vulnerability, license approval, and provenance review remain separate evidence. |
| Vulnerability and secret audit evidence | Covered by separate audit workflow | `npm run audit:deps`, `.github/workflows/security-audit.yml`, `.gitleaks.toml` | Scheduled/PR SCA and gitleaks are not the same artifact as release-owner review and still need reviewed output for a candidate. |

## Current Automated Coverage

| Area | Current evidence | What it proves | What it does not prove |
| --- | --- | --- | --- |
| Desktop npm lock | `scripts/verify_dependency_locks.ps1` parses `desktop/package.json` and `desktop/package-lock.json` | Lockfile exists, root name/version match, direct manifest dependency specs match lock root, and direct packages exist in lock packages | It does not prove registry provenance, signing, or license acceptability. |
| Mobile npm lock | `scripts/verify_dependency_locks.ps1` parses `mobile/package.json` and `mobile/package-lock.json` | Same as desktop lock verification | It does not prove Expo/native dependency installability on every target device. |
| Backend Python transitive lock | `backend/requirements-lock.txt` plus `scripts/verify_dependency_locks.ps1` | All Python lock entries are pinned, all direct requirements are present, the lock has resolver provenance, and every package carries at least one `sha256` hash | It does not prove package signatures or trusted-builder provenance. |
| SBOM | `scripts/generate_sbom.py` reads backend Python lock plus desktop/mobile npm locks and emits CycloneDX 1.5 JSON | A repeatable machine-generated component inventory tied to the current commit, including Python sha256 hashes and npm lock integrity hashes | It is not a vulnerability report, license approval, or release-owner sign-off. |
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
| Vulnerability output not attached for a candidate | "SBOM generated; vulnerability audit output still needs candidate-specific evidence." |
| License review not fully automated | "SBOM inventory includes npm lockfile license metadata where present; release license approval remains manual unless a candidate-specific license scan is attached." |

## Suggested Evidence Commands

```powershell
npm run deps:verify
npm run sbom:generate
```
