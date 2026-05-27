# Mobile Audit Maintenance

## 2026-05-27

- Initial worktree check: `git status --porcelain=v1` was clean.
- Repository state: `main...origin/main [ahead 1]`.
- Updated Expo from `^56.0.4` to stable patch `^56.0.5` to stay within the current Expo 56 line and avoid canary or major-version churn.
- `npm --prefix mobile audit --omit=dev --audit-level=high` still reported the moderate `uuid <11.1.1` advisory through `xcode@3.0.1`; the suggested forced audit fix would install a breaking Expo version, so the fallback was to declare an npm override in `mobile/package.json`: `"uuid": "11.1.1"`.
- `mobile/package-lock.json` pins `node_modules/uuid` to `11.1.1`; the lockfile itself does not contain an `overridden` field.
- `npm --prefix mobile explain uuid` is the evidence that npm applies the override, reporting `uuid@11.1.1 overridden`.
