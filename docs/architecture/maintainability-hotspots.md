# Maintainability Hotspots

This file maps high-complexity areas that should be split or given explicit ownership before broad release. It supports `RR-P1-001` in `docs/release/release-readiness-dashboard.md`.

## Hotspot inventory

| Area | Current risk | Target state | First safe step |
| --- | --- | --- | --- |
| `desktop/src/main/ipc.ts` | Large security-critical bridge surface; difficult to audit changes. | Split by bridge domain: backend lifecycle, files/documents, settings, permissions, mobile pairing, skills/MCP. | Add domain-level tests and move pure validators first. |
| `desktop/src/main/browserHost.ts` | Browser automation, screenshots, and WebSocket bridge share one large surface. | Separate host lifecycle, permission/safety, screenshot/redaction, and transport bridge. | Extract redaction and transport helpers with tests. |
| `backend/app/core/db.py` | Schema migration, CRUD, FTS, settings, and privacy erase are tightly coupled. | Split schema/migrations, settings store, task store, index store, audit store. | Add migration contract tests, then extract settings store. |
| `backend/app/context_management.py` | Context budgeting and compaction logic are hard to reason about in one file. | Separate token estimation, message selection, compaction policy, reactive retry. | Extract token estimation and budget calculations. |
| `scripts/collect_release_evidence_packet.ps1` and related evidence scripts | Release logic is large PowerShell; hard to unit-test cross-platform. | Core evidence validation in Python/TypeScript; PowerShell as Windows wrapper only. | Port pure parsing/validation rules to Python and test them. |

## Refactor rules

1. No behavior-preserving split without tests around the moved boundary.
2. Security-sensitive validators should become pure functions where possible.
3. Every extracted module needs an owner comment or README section.
4. Avoid mixing artifact generation with pass/fail decision logic.
5. Keep release evidence fail-closed after each split.

## Near-term target

Before RC, at minimum:

- Link this hotspot map from the release dashboard.
- Extract at least one pure validation module from `ipc.ts` or `db.py`.
- Add a follow-up issue for every hotspot not changed in the candidate.
