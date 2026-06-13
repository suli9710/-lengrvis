# AGENTS.md

## Cursor Cloud specific instructions

Lengrvis is a **Windows-first local OS-agent** product in a monorepo with three components:
`backend/` (Python 3.12 + FastAPI, the engine; required, port `8000`), `desktop/`
(Electron + React + Vite UI; Vite dev server on `5173`), and `mobile/` (Expo/React
Native Android companion; optional). SQLite is the only datastore (no DB server). See
`README.md` for the full feature/run/build/test command reference — the notes below
only capture non-obvious caveats for working in the cloud (Linux) environment.

### Environment / tooling
- A Python virtualenv lives at `.venv` (created by the startup update script). Use
  `.venv/bin/python` or `source .venv/bin/activate`.
- The `scripts/*.ps1` helpers and the root `package.json` scripts (`npm run dev`,
  `test`, `qa:gate`, `golden:gate`, etc.) are **PowerShell / Windows-only**. On Linux,
  run the underlying commands directly (uvicorn, vite, pytest) as documented below.

### Running the services (Linux / headless)
- Backend (use `full_app`, not the slim `app`):
  `python -m uvicorn backend.main:full_app --host 127.0.0.1 --port 8000`
- Web UI: `npm --prefix desktop run dev:web` (Vite on `127.0.0.1:5173`). The full
  Electron app (`npm --prefix desktop run dev`) needs a display; prefer `dev:web` here.
- **dev:web auth/token:** the browser renderer talks to the backend cross-origin and
  must send the desktop API token. Start Vite with `VITE_LENGRVIS_DESKTOP_API_TOKEN`
  set to the **same** value as the backend's `LENGRVIS_DESKTOP_API_TOKEN` (or the
  auto-generated `.lengrvis_data/desktop_api.secret`). Setting one explicit fixed token
  on both processes avoids token-mismatch 401s. CORS for `:5173` is already configured.
- **dev:web limits:** mutating/computer-control + run endpoints (e.g. `/api/runs`) are
  intentionally blocked in web mode ("需要 Electron 渲染进程"), so natural-language task
  *execution* does not complete in the browser. Read-only GET capabilities (system
  info/diagnostics via the "此电脑" view, file search) do work and are the easiest
  end-to-end smoke. Full task execution requires the packaged/Electron app.

### LLM dependency
- Natural-language planning needs a real OpenAI-compatible provider (configure
  `LENGRVIS_*` in `.env`). Without one, efficiency/hybrid mode falls back to a
  `MockProvider` stub that cannot produce full structured plans, so NL tasks remain
  stuck in `planning` (you'll see `context.reactive_retry` audit events). Deterministic
  read-only capabilities run fine without any LLM. `privacy` mode requires a local LLM
  server (Ollama/LM Studio/llama.cpp) and never uses the mock fallback.

### Tests (backend pytest)
- Run with the audit secret CI uses: `LENGRVIS_AUDIT_HMAC_SECRET=ci-audit-hmac-secret`.
- **Activate the venv (or otherwise put `python` on PATH) before pytest** — several
  tests spawn a `python` subprocess (developer engine, golden delete tasks). This
  system only has `python3` on bare PATH, so without activation they fail with
  `FileNotFoundError: ... 'python'`.
- Command: `python -m pytest backend/tests -q -n auto` (xdist).
- **~14 tests fail on Linux and that is expected** — they assert Windows-specific
  behavior: Windows system paths (`test_cleanup_review_blocks_system_and_sensitive_paths`),
  `send2trash`/Linux trash semantics (`test_supervisor_chat_flow` trash tests, golden
  `gt-run-del-*`), and Windows remote-desktop screen capture (`test_remote_desktop.py`).
  ~1800 tests pass. CI runs on `windows-latest`, where these pass.

### Lint
- Lint is `ruff` via the pre-commit hook (pinned `ruff==0.9.6`), scoped to **changed
  files under `backend/`** only. The tree is *not* ruff-clean wholesale, so do not run
  `ruff check backend/` over the whole tree and expect zero findings — lint only the
  files you changed.

### Desktop
- Typecheck/build/unit tests work on Linux: `npm --prefix desktop run typecheck`,
  `npm --prefix desktop run build`, `npm --prefix desktop test` (Vitest).
