# Mobile Audit Maintenance

## 2026-06-20

- `npm --prefix mobile audit --omit=dev --audit-level=moderate --json` reports 0 vulnerabilities for production/runtime dependencies.
- `npm --prefix mobile audit --audit-level=moderate --json` reports 9 moderate dev-tooling advisories through the local `eas-cli` chain in `mobile/package.json`.
- The affected chain is `eas-cli` -> `@expo/eas-json`/`@expo/plugin-help`/`@expo/plugin-warn-if-update-available` -> `joi`/`@oclif/core`/`js-yaml` plus direct `tar` and `ts-deepmerge` advisories. npm's suggested fix downgrades `eas-cli` to `0.24.1`, which is a breaking and non-actionable fix for the Expo/EAS build flow.
- Waiver boundary: these findings are devDependencies used for local/EAS build orchestration, not mobile runtime packages. Keep the high-severity gate in `scripts/run_dependency_audit.ps1`, keep Android Companion documented as Preview, and do not claim real-device LAN/WSS, certificate trust, EAS submit, Play Console review, rollout, or production store distribution until separate evidence closes those gates.

## 2026-05-27

- Initial worktree check: `git status --porcelain=v1` was clean.
- Repository state: `main...origin/main [ahead 1]`.
- Updated Expo from `^56.0.4` to stable patch `^56.0.5` to stay within the current Expo 56 line and avoid canary or major-version churn.
- `npm --prefix mobile audit --omit=dev --audit-level=high` still reported the moderate `uuid <11.1.1` advisory through `xcode@3.0.1`; the suggested forced audit fix would install a breaking Expo version, so the fallback was to declare an npm override in `mobile/package.json`: `"uuid": "11.1.1"`.
- `mobile/package-lock.json` pins `node_modules/uuid` to `11.1.1`; the lockfile itself does not contain an `overridden` field.
- `npm --prefix mobile explain uuid` is the evidence that npm applies the override, reporting `uuid@11.1.1 overridden`.
