# Changelog

All notable changes to Lengrvis should be recorded here.

## Unreleased

## 0.1.3 - Draft

- Simplified the desktop task workspace, approval navigation, draft recovery, and result loading.
- Added encrypted database snapshots and SQLite runtime checks for packaged backends.
- Preserved content provenance across execution boundaries and tightened desktop security checks.
- Refactored context and run services, clarified MCP compatibility, and updated dependency/build tooling.
- Build verification and release sign-off are pending; see [the draft release notes](docs/release/v0.1.3.md).

## 0.1.1 - 2026-06-27

- Added blocking cross-platform backend CI on Linux and macOS.
- Added sha256-hashed backend Python lock verification and SBOM hash metadata.
- Added gitleaks, CodeQL, and Dependabot release-safety automation.
- Added signed macOS release configuration checks and Linux release checksum manifest verification.
