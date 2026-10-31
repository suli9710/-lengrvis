# AGENTS.md

Lengrvis is a Windows-first local "OS Agent" assistant. It is a pnpm/npm
monorepo with three parts:

- `backend/` — FastAPI engine (agents, policy, tools, SQLite index, LLM routing, mobile pairing). The core product.
- `desktop/` — Electron + React + Vite app (primary UI). Connects to the backend at `http://127.0.0.1:8000`.
- `mobile/` — Expo / React Native Android companion (Preview, optional).

Authoritative dev commands live in `README.md` ("源码开发 setup" / "运行"), `package.json`, `desktop/package.json`,
and the PowerShell helpers in `scripts/`. The PowerShell scripts only run on Windows.

## Cursor Cloud / Linux development notes

When working on Linux, the repo's `.ps1`/`.cmd` launchers do not run. Drive the underlying
commands directly. If the startup update script has not provisioned `.venv`, create it and install
`python -m pip install --require-hashes -r requirements-dev-lock.txt` (includes Ruff).
Use the hashed development lock for installation: `requirements-dev.txt` is a resolver input
and cannot be installed directly because it includes a hash-locked runtime file. Install
the Playwright Chromium browser for both Python and desktop, and run
`npm --prefix desktop ci` + `npm --prefix mobile ci`. Run
`npm ci --ignore-scripts --engine-strict` at the repository root before MCP conformance or release commands.
CI itself runs on `windows-latest`, so a
clean local Linux run will show a small set of Windows-only failures (see backend/desktop notes below).

### Backend (required, the core product)
- Run the full-featured app, not the thin guardian entrypoint:
  `.venv/bin/python -m uvicorn backend.main:full_app --host 127.0.0.1 --port 8000`
  (`backend.main:app` is only the guardian shim.)
- No LLM key is needed for local dev: set `LENGRVIS_MODE=efficiency` and `LENGRVIS_ALLOW_MOCK_FALLBACK=true`.
  The `MockProvider` returns a fixed canned reply and does NOT actually plan or delegate tasks (so it never
  creates engine runs/approvals); a real OpenAI-compatible key or local LLM is required to exercise planning.
- SQLite and all secrets auto-create under `./.lengrvis_data` on first start (no DB server).
- State-changing desktop APIs require header `x-lengrvis-desktop-token` whose value is the contents of
  `./.lengrvis_data/desktop_api.secret` (auto-generated on first backend start). `/api/health` is exempt.

### Backend tests / lint
- Tests: set `LENGRVIS_AUDIT_HMAC_SECRET` to a local test-only value, then run
  `python -m pytest backend/tests` (config in `pytest.ini`).
- IMPORTANT: put the venv on `PATH` (e.g. `export PATH="$PWD/.venv/bin:$PATH"` or activate it) before running
  pytest. Some code/tests spawn a bare `python` subprocess (golden tasks, developer/code engine); this VM only
  has `python3`, so without `.venv/bin` on PATH those tests fail with `FileNotFoundError: 'python'`.
- Linux limitations: Windows-only features cannot run there — remote-desktop screen capture
  (`backend/app/services/remote_desktop_service.py` is guarded by `sys.platform == "win32"`), recycle-bin/trash
  delete flows, and `C:\Windows`-style system-path classification. Use the current test results for counts.
- Lint: `.venv/bin/python -m ruff check backend` (config in `backend/pyproject.toml`).
  `.github/workflows/lint.yml` gates full-tree Ruff debt and strictly checks lint and formatting on changed
  backend Python files. Pre-commit checks changed files too. Format/fix: `ruff format` / `ruff check --fix`.
  On Windows, use `.venv/Scripts/python.exe` and put `.venv/Scripts` on PATH for subprocess tests.

### Desktop (UI)
- Typecheck/tests/build all work: `npm --prefix desktop run typecheck`, `npm --prefix desktop test` (Vitest),
  `npm --prefix desktop run build:renderer`.
- `npm --prefix desktop run smoke` runs the smoke groups listed in `desktop/package.json` (18 at the
  October 2026 audit). On Linux, `smoke:ipc` has a known failure at the
  `isTrustedRendererUrl(file://…)` assertion in `scripts/ipc-security-smoke.cjs`. This is a test-only Windows
  path assumption (`file:///` + an absolute path yields `file:////…` on Linux, only `file:///C:/…` on Windows);
  the product code uses `pathToFileURL` and is correct. CI runs on `windows-latest`, so this smoke is green there.
  Run individual smokes to validate the rest, e.g. `npm --prefix desktop run smoke:desktop-token`.
- `vite.config.ts` sets both `build.target` and `optimizeDeps.esbuildOptions.target` to `es2022`, including
  the dev dependency optimizer. This avoids esbuild's object-rest lowering issue with broad browser targets.
  Run the checked-in `dev:web` configuration directly.
- Electron full app needs a display; for UI work use the browser dev server above (or the `computerUse` desktop).

### Running the UI against the backend in a browser (dev:web)
- In `dev:web`, the renderer makes cross-origin fetches to `:8000`; the custom desktop-token header triggers a
  CORS preflight `OPTIONS` that the token guard 401s, and Electron-IPC-only endpoints (e.g. `POST /api/runs`,
  `/api/documents/*`, `/api/ws/*`) are blocked in web mode — so chat falls back and reports "需要 Electron 桌面模式".
- For a browser dev session, start the backend with the test-only escape hatch so the token guard is bypassed and
  preflight succeeds: `LENGRVIS_TEST=1 LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL=true` (plus the efficiency/mock env
  above). Then `POST /api/chat` (the conversational entrypoint) works from the browser. Normal end users run the
  full Electron app, which uses IPC and has none of these CORS/token issues.
