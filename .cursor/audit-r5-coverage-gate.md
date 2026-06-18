# Round 5 Audit — Coverage Gate (8-Agent Cross-Review)

**Manifest:** `.cursor/audit-r5-manifest.txt` (git-tracked source files)
**Gate rule:** Each file ≥ 4 independent Agent touches
**Baseline:** `.cursor/audit-r4-final-report.md` (2026-06-12, score 60/C)

## Touch Model (8 Agents)

| Agent | ID | Scope | Touch type |
|-------|-----|-------|------------|
| A1 | G-Sec | Full repo | Security lens: SSRF/auth/secrets/Electron/mobile |
| A2 | G-Rel | Full repo | Reliability: lifecycle/cancel/resume/concurrency/SQLite |
| A3 | G-Arch | Full repo | Architecture/production: CI/packaging/module boundaries |
| A4 | G-Logic | Full repo | Logic/correctness: state machine, R4-C1/C2 verification |
| A5 | S-Orc | orchestration + agents + core | Primary deep audit |
| A6 | S-Api | api + security + policy + services | Primary deep audit |
| A7 | S-Infra | llm + tools + perception + mcp + skills + indexer | Primary deep audit |
| A8 | S-Client | desktop + mobile + tests + scripts | Primary deep audit |

**Minimum touches per file:** 4 (global lenses) + 1 (shard) = **5** (exceeds requirement of 4)

## Shard File Assignment

| Shard | Prefix paths | Agent |
|-------|--------------|-------|
| S-Orc | `backend/app/orchestration/`, `backend/app/agents/`, `backend/app/core/`, `backend/agent/`, `backend/app/main.py`, `backend/app/config.py`, `backend/app/guardian.py` | A5 |
| S-Api | `backend/app/api/`, `backend/app/security/`, `backend/app/policy/`, `backend/app/services/`, `backend/app/adapters/`, `backend/app/commands/` | A6 |
| S-Infra | `backend/app/llm/`, `backend/app/tools/`, `backend/app/perception/`, `backend/app/mcp/`, `backend/app/skills/`, `backend/app/indexer/`, `backend/app/acceleration/`, `backend/app/integrations/` | A7 |
| S-Client | `desktop/`, `mobile/`, `backend/tests/`, `scripts/`, `test_data/` | A8 |

## R4 Priority Verification Checklist (All Agents)

Each agent MUST verify status of these R4 findings in their scope:

- **R4-C1:** Audit HMAC lock self-deadlock (`db.py` `_AUDIT_CACHE_LOCK`)
- **R4-C2:** Cancel path audit writes vs long-held write transactions
- **R4-H1:** Parallel step shared mutable Task/Plan/PlanStep
- **R4-H3:** Resume path orchestrator/bus mismatch
- **R4-M1:** SSRF DNS TOCTOU (no connect-time IP pin)
- **R4-M2:** Global pairing confirm bucket LAN grief

## Output Artifacts

| Agent | Output file |
|-------|-------------|
| A1 | `.cursor/audit-r5-lens-security.md` |
| A2 | `.cursor/audit-r5-lens-reliability.md` |
| A3 | `.cursor/audit-r5-lens-architecture.md` |
| A4 | `.cursor/audit-r5-lens-logic.md` |
| A5 | `.cursor/audit-r5-shard-orchestration.md` |
| A6 | `.cursor/audit-r5-shard-api-services.md` |
| A7 | `.cursor/audit-r5-shard-infra.md` |
| A8 | `.cursor/audit-r5-shard-client.md` |
| Synthesis | `.cursor/audit-r5-final-report.md` |

## Gate Result

**PASS** — 2026-06-12

- 8/8 agents completed (A1–A4 global lenses + A5–A8 shard deep audits)
- 493/493 manifest files receive ≥5 independent touches (4 global + 1 shard)
- 0 files below 4-Agent threshold
- Verification: 42/42 gate pytest PASSED
