# Round 2 Audit — Wave 0 Baseline

**Generated:** 2026-06-11  
**Manifest:** `.cursor/audit-r2-manifest.txt` — **493** git-tracked source files

## Dependency SCA (`npm run audit:deps`)

| Target | Result |
|--------|--------|
| desktop npm | **FAIL** — 2 critical (`shell-quote` via `concurrently`) |
| mobile npm | 5 moderate (`joi` via `eas-cli`, dev tooling) |
| backend pip-audit | **SKIP** — `pip_audit` module not installed locally |

## Dangerous Pattern Grep

| Pattern | Hits |
|---------|------|
| `except: pass` / bare except pass | 0 in prod py/ts |
| `shell=True`, `eval(`, `dangerouslySetInnerHTML` | 0 |
| `asyncio.Queue()` unbounded | 2 — `file_watcher.py:137`, `dispatcher.py:65` |
| `INSERT OR REPLACE` + `created_at` | Multiple in `db.py` (plans, chat_messages, etc.) |
| `follow_redirects` | MCP client **no** explicit setting; skill sandbox `False`; browser runtime `True` |

## P0 Anchor Spot-Check (Wave 0)

| Anchor | Status |
|--------|--------|
| Parallel shared `context` | **STILL PRESENT** — `step_scheduler_handler.py:156` passes same `context` to parallel tasks |
| lifespan TaskPool shutdown | **STILL MISSING** — `main.py:104-116` finally block has no `get_pool().shutdown()` |
| MCP SSRF | **STILL PRESENT** — `mcp/client.py:93-95` posts to `self.config.url` without URL validation |
