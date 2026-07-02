# Maintainability Hotspots

This file maps high-complexity areas that should be split or given explicit ownership before broad release. It supports `RR-P1-001` in `docs/release/release-readiness-dashboard.md`.

## Hotspot inventory

| Area | Current size | Current risk | Owner seam | Target state | Next safe step |
| --- | ---: | --- | --- | --- | --- |
| `desktop/src/main/ipc.ts` | 24 lines | Former large bridge has already been split into domain registrars; it remains security-critical as the composition point. | Desktop main process IPC registration and renderer trust exports. | Keep as a thin registrar only. | Keep new IPC domains in `desktop/src/main/ipc*Handlers.ts` or `desktop/src/main/ipc/validation/*`; do not regrow validation logic here. |
| `desktop/src/main/browserHost.ts` | 1096 lines after redaction and network-guard splits in this working tree | Browser lifecycle, embedded web contents hardening, IPC handlers, WebSocket bridge, DOM actions, and script builders still share one large surface. | BrowserHost lifecycle and transport orchestration. Redaction now belongs to `desktop/src/shared/browserHostRedaction.ts`; URL/network safety now belongs to `desktop/src/main/browserHostNetworkGuard.ts`. | Separate host lifecycle, permission/safety, transport bridge, DOM scripts, and remaining host orchestration. | Extract transport bridge or DOM script builders next, with focused tests or smoke coverage. |
| `desktop/src/shared/browserHostRedaction.ts` | 173 lines | Security-sensitive renderer-facing redaction must stay testable without Electron. | BrowserHost renderer/public-output redaction only. | Pure redaction helpers covered by Vitest and BrowserHost smoke. | Keep all future BrowserHost redaction changes here; add cases to `browserHostRedaction.test.ts`. |
| `desktop/src/main/browserHostNetworkGuard.ts` | 147 lines | Embedded browser URL safety must fail closed for file URLs, private ranges, metadata hosts, and DNS failures. | BrowserHost URL/navigation/request network guard only. | Pure network guard helpers covered by Vitest and BrowserHost smoke. | Keep private-network and DNS fail-closed rules here; do not add host/IP parsing back to `browserHost.ts`. |
| `desktop/src/renderer/styles.css` | 91 lines after ordered CSS split; largest shard is `desktop/src/renderer/styles.home.css` at 2474 lines | Global UI styling is easier to review after splitting, but feature shards still need ownership and eventual component-local CSS. | Renderer design tokens only. Feature/view styling now lives in ordered `desktop/src/renderer/styles.*.css` shards imported by `desktop/src/renderer/main.tsx`. | Continue shrinking feature shards by app shell, shared primitives, feature views, and component-local CSS. | Split `styles.home.css` or `styles.timeline-files.css` next after screenshot or smoke coverage exists. |
| `backend/app/core/db.py` | 512 lines | Schema migration, CRUD, FTS, settings, and privacy erase remain coupled behind a broad DB facade. | Compatibility facade for persisted local state. | Split schema/migrations, settings store, task store, index store, audit store. | Add migration contract tests, then extract settings store. |
| `backend/app/policy/policy_engine.py` | 1120 lines after browser-content split in this working tree | Policy classification, approval review, sensitive content detection, and default heuristics share one high-risk file. | Policy API and fail-closed decision interface. Browser content trust/warning parsing now belongs to `backend/app/policy/browser_content.py`. | Split risk taxonomy, classifiers, approval validators, and sensitive-field detectors behind the existing policy API. | Extract pure classifiers and approval decision helpers with focused unit tests. |
| `backend/app/policy/browser_content.py` | 37 lines | Browser tool-result trust markers must stay fail-closed and independently testable. | Browser-content warning and trust-label parsing only. | Pure browser-content marker parser covered by direct policy tests. | Keep recursive marker semantics here; do not add browser payload traversal back to `policy_engine.py`. |
| `backend/app/api/routes_tasks.py` | not measured in this pass | Task views, timeline/progress shaping, artifacts, replay, and rollback endpoints are coupled in one route module. | Public task API route contract. | Split public task projection, timeline/progress routes, artifact routes, replay, and rollback wiring. | Move pure response-shaping/redaction helpers first and lock the API shape with route tests. |
| `backend/app/config.py` | not measured in this pass | Defaults, env/config-file loading, coercion, secret resolution, and validation live in one broad settings surface. | Runtime settings contract and compatibility fields. | Split source readers, typed coercion/normalization, secret material loading, and public redaction. | Extract source loading/coercion helpers with table-driven tests before moving settings fields. |
| `backend/app/context/management.py` (`backend/app/context_management.py` compatibility shim) | 1620 lines after prompt-error and compact-boundary splits in this working tree | Context budgeting, compaction, provider retry, session summary, and LLM wrapper logic are hard to reason about in one file. | Context projection and provider-safe message shaping. Prompt-too-long parsing now belongs to `backend/app/context/prompt_errors.py`; compact-boundary parsing now belongs to `backend/app/context/compact_boundaries.py`. | Separate message projection, compaction policy, provider retry, token accounting, and session-summary injection. | Extract fallback trimming or provider retry helpers behind the existing shim. |
| `backend/app/context/prompt_errors.py` | 129 lines | Context-window provider errors need consistent compaction-trigger classification. | Prompt-too-long detection, token count parsing, and HTTP error body text extraction. | Pure prompt error classifier covered by context/openai resilience tests. | Keep provider-specific prompt-too-long heuristics here; do not add them back to `management.py`. |
| `backend/app/context/compact_boundaries.py` | 165 lines | Compact-boundary metadata rules need stable preserved-tail and tool-pair semantics. | Compact-boundary detection, retained-tail ids, preserved segment expansion, and public metadata redaction only. | Pure compact-boundary helpers covered by direct tests plus context compaction tests. | Keep persisted compact metadata compatibility rules here; do not add boundary-shape parsing back to `management.py`. |
| `backend/app/perception/ui_automation.py` | not measured in this pass | UIAutomation contracts, Windows COM adapter, fallback input, screenshot capture, and key mapping share one high-risk file. | Windows UI automation adapter. | Separate protocol/schemas, Windows adapter, input fallback, screenshot provider, and key mapping. | Extract pure selector/key mapping helpers first, then wrap with contract tests before moving platform code. |
| `scripts/collect_release_evidence_packet.ps1` and related evidence scripts | 423 lines for packet collector | Release logic is large PowerShell; hard to unit-test cross-platform. | Release evidence indexing and fail-closed summary assembly. | Core evidence validation in Python/TypeScript; PowerShell as Windows wrapper only. | Port pure parsing/validation rules to Python and test them. |

## Refactor rules

1. No behavior-preserving split without tests around the moved seam.
2. Security-sensitive validators should become pure functions where possible.
3. Every extracted module needs an owner comment or README section.
4. Avoid mixing artifact generation with pass/fail decision logic.
5. Keep release evidence fail-closed after each split.

## Near-term target

Before RC, at minimum:

- Link this hotspot map from the release dashboard. Done for `RR-P1-001`.
- Extract at least one pure validation/redaction module from a large desktop surface. Done: `desktop/src/shared/browserHostRedaction.ts` plus `desktop/src/shared/browserHostRedaction.test.ts`.
- Extract at least one pure BrowserHost URL/network safety module from a large desktop surface. Done: `desktop/src/main/browserHostNetworkGuard.ts` plus `desktop/src/main/browserHostNetworkGuard.test.ts`.
- Split the renderer global stylesheet into ordered feature shards while preserving cascade order. Done: `desktop/src/renderer/styles.css` plus `desktop/src/renderer/styles.*.css` imports in `desktop/src/renderer/main.tsx`.
- Extract at least one pure context error-classification module from `backend/app/context/management.py`. Done: `backend/app/context/prompt_errors.py`.
- Extract compact-boundary parsing from `backend/app/context/management.py`. Done: `backend/app/context/compact_boundaries.py` plus `backend/tests/test_context_compact_boundaries.py`.
- Extract at least one pure policy marker parser from `backend/app/policy/policy_engine.py`. Done: `backend/app/policy/browser_content.py` plus `backend/tests/test_policy_browser_content.py`.
- Add key-path tests before splitting `management.py` and `ui_automation.py`: context budget projection/compaction, approval-gated UI actions, selector matching, key mapping, screenshot fallback, and unavailable-provider behavior.
- Add observability counters around context compaction decisions, UIAutomation action outcomes, screenshot capture failures, and approval-denied paths so split modules preserve operational signals.
- Add a follow-up issue for every hotspot not changed in the candidate.

## Current evidence

- `npm --prefix desktop run typecheck`
- `npm --prefix desktop test`
- `npm --prefix desktop run build:renderer`
- `npm --prefix desktop run smoke:ipc`
- `npm --prefix desktop run smoke:browser-activity`
- CSS split equivalence check: ordered `styles.*.css` concatenation equals `HEAD:desktop/src/renderer/styles.css` after LF normalization.
- `.venv\Scripts\python.exe -m pytest backend\tests\test_context_management.py backend\tests\test_context_compaction.py backend\tests\test_context_usage.py backend\tests\test_openai_compatible_resilience.py -q`
- `.venv\Scripts\python.exe -m pytest backend\tests\test_context_compact_boundaries.py backend\tests\test_context_management.py backend\tests\test_context_compaction.py -q`
- `.venv\Scripts\python.exe -m pytest backend\tests\test_policy_engine.py backend\tests\test_policy_browser_content.py -q`
- `.venv\Scripts\python.exe -m ruff check backend`
