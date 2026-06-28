# Contributing

Lengrvis is a Windows-first local OS agent with backend, desktop, and mobile
packages. Keep changes scoped, prefer the existing project patterns, and include
focused verification evidence in every PR.

## Development Setup

Use the commands documented in `README.md`. For backend work, install from the
hash-locked backend requirements first when reproducing CI:

```powershell
python -m pip install --require-hashes -r backend/requirements-lock.txt
python -m pip install -r requirements-dev.txt
```

## Checks Before PR

- Backend: `python -m pytest backend/tests` or a focused test file.
- Desktop: `npm --prefix desktop run typecheck`, `npm --prefix desktop test`, and relevant smokes.
- Mobile: `npm --prefix mobile run typecheck` and relevant smokes.
- Supply chain: `npm run deps:verify` after dependency changes.
- Security: run pre-commit hooks before pushing. Secret scanning uses
  `scripts/secret_scan.ps1` with `.gitleaks-ci.toml`, scans a Git source
  snapshot, and requires either a `gitleaks` binary or Go.

## Release-Sensitive Changes

Packaging, signing, update, dependency, local-secret, auth, and remote-control
changes need a short note in the PR describing the release impact and any
remaining manual evidence required by `docs/qa/release-gate.md`.
