# Supply Chain Gate

Date: 2026-06-08

Scope: dependency lock, SBOM, and audit evidence for the current repository. This page complements the release gate without changing it.

## Audit Mapping

| Audit item | Status | Evidence | Remaining gap |
| --- | --- | --- | --- |
| P1-3 dependency lock evidence | Partially covered | Root `npm run deps:verify`, `scripts/verify_dependency_locks.ps1`, `desktop/package-lock.json`, `mobile/package-lock.json`, `backend/requirements-lock.txt` | Backend Python lock is direct dependencies only, not a full resolved transitive lock. |
| SBOM evidence | Not covered | No SBOM artifact or SBOM script found in this pass | Add CycloneDX/Syft or equivalent before claiming SBOM readiness. |
| Vulnerability audit evidence | Partially manual / historical | Productization notes mention a passing desktop npm high audit, but no root audit gate is codified | Add repeatable desktop/mobile npm audit and Python audit commands before treating this as an automated gate. |

## Current Automated Coverage

| Area | Current evidence | What it proves | What it does not prove |
| --- | --- | --- | --- |
| Desktop npm lock | `scripts/verify_dependency_locks.ps1` parses `desktop/package.json` and `desktop/package-lock.json` | Lockfile exists, root name/version match, direct manifest dependency specs match lock root, and direct packages exist in lock packages | It does not run `npm audit`, verify registry provenance, or produce an SBOM. |
| Mobile npm lock | `scripts/verify_dependency_locks.ps1` parses `mobile/package.json` and `mobile/package-lock.json` | Same as desktop lock verification | It does not prove Expo/native dependency installability on every target device. |
| Backend Python direct lock | `backend/requirements-lock.txt` plus `scripts/verify_dependency_locks.ps1` | Every direct requirement has a pinned `==` entry and lock lines are pinned | It is not a full resolver lock and does not pin all transitive dependencies. |
| QA entry point | Root `package.json` exposes `deps:verify` | A single command exists for lock drift checks when dependency manifests change | This is not an SBOM or vulnerability audit gate. |

## Required Evidence When Dependencies Change

Record these fields in QA handoff when `package.json`, `package-lock.json`, `backend/requirements.txt`, or `backend/requirements-lock.txt` changes:

| Field | Required value |
| --- | --- |
| Command | `npm run deps:verify` |
| Date | Local date of execution |
| Commit/worktree | Commit SHA if available, otherwise dirty-worktree note |
| Result | Exit code and key output lines |
| Scope note | State whether the change touched desktop npm, mobile npm, backend Python, or multiple areas |

## Recommended Manual Evidence Until Automated

These commands are recommended evidence, not current mandatory root scripts:

```powershell
npm --prefix desktop audit --audit-level=high
npm --prefix mobile audit --audit-level=high
```

For Python, add one repeatable workflow before claiming audit readiness. Acceptable future options include `pip-audit` against a full resolved requirements file, `uv pip compile` plus audit, or another documented Python vulnerability scanner.

For SBOM, add one repeatable workflow before claiming SBOM readiness. Acceptable future options include CycloneDX for npm/Python, Syft, or another SBOM generator that writes an artifact tied to the release candidate commit.

## Remaining Gaps

| Gap | Gate language to use |
| --- | --- |
| No full Python transitive lock | "Backend direct dependency lock verified; full Python resolver lock remains pending." |
| No SBOM artifact | "SBOM evidence is pending; do not claim SBOM readiness." |
| No codified root vulnerability audit | "Vulnerability audit is manual/historical unless commands and outputs are recorded for this candidate." |
| No provenance/signature verification | "Lock verification does not prove package provenance, signing, or registry integrity." |

## Suggested Evidence Command

```powershell
npm run deps:verify
```
