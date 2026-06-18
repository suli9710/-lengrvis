# Round 6 修复冲刺 P3 报告 — R8 API hint + 硬校验 + SSOT

**日期：** 2026-06-12  
**基线：** `.cursor/audit-r6-fix-sprint-p2-report.md`  
**范围：** `POST /runs` 显式 `agent_hint`、Planner 工具面硬校验、Supervisor SSOT、Desktop Worker 显示

---

## 修复明细

### R8-1 — `POST /runs` 接受 `agent_hint` — FIXED ✅

- `RunCreateRequest.agent_hint`（未知值 allowlist 剥离）
- `routes_runs.create_run` → `run_service.create_run(..., agent_hint=)`
- 测试：`test_runs_api_accepts_agent_hint`

### R8-2 — Planner 工具面硬校验 — FIXED ✅

- `plan_tools_outside_visible` / `plan_matches_supervisor_hint`
- `PlanningHandler`：hint 存在时 2 次 retry → strip 越界 step → `SupervisorHintPlanError`
- **OS 引擎补齐：** `_create_reviewed_plan` 从 `task.metadata` 读取 hint 并传入 `_create_plan`

### R8-3 — Supervisor SSOT — FIXED ✅

- `supervisor_agent._is_known_agent` → `normalize_supervisor_agent_hint`
- `SUPERVISOR_SCHEMA` agent 列表来自 `KNOWN_SUPERVISOR_WORKER_AGENTS`

### R8-4 — Desktop Worker 显示 — FIXED ✅

- `runEngineAgentName` 优先使用 `engine_capabilities.supervisor_agent_hint` + `zhAgentName`
- Vitest：`prefers supervisor worker hint over generic engine label`

### R8-5 — `_task_for_state` 恢复 shell — FIXED ✅（P2 延续）

- 丢失 task 行时用 `merge_run_task_metadata(goal=...)` 重建 metadata

---

## 8-Agent 交叉审查

| 域 | Sec | Rel | Arch | Logic | 综合 |
|----|-----|-----|------|-------|------|
| `RunCreateRequest` + routes | WARN | PASS | PASS | WARN | WARN |
| `planning_handler` guard | WARN | WARN | PASS | WARN | WARN |
| `os_execution_engine` hint wire | PASS | PASS | PASS | PASS | **PASS** |
| `supervisor_agent` SSOT | PASS | PASS | WARN | PASS | WARN |
| Desktop mappers | PASS | PASS | PASS | PASS | **PASS** |

**无 FAIL。** 审查后已补 OS `_create_reviewed_plan` hint 传递（Rel/Sec 指出的主路径缺口）。

---

## 验证汇总

| 命令 | 结果 |
|------|------|
| `test_delegation_metadata.py` | 11 passed |
| `test_agent_hint_routing.py` | 9 passed |
| `test_supervisor_chat_flow.py` | 18 passed |
| `test_golden_tasks.py` | 39 passed |
| Desktop `mappers.test.ts` | 9 passed |
| **合计（P3 收尾全量定向）** | **76 passed / 0 failed** |

Gate：**PASS**（含 golden browser/search + runs API hint + supervisor local-provider 回归）

---

## 状态迁移

| 项目 | P2 | P3 |
|------|----|----|
| `POST /runs` 显式 `agent_hint` | OPEN | **FIXED** |
| Planner 工具面硬校验 | OPEN | **FIXED** |
| OS run 路径 hint 消费 | OPEN | **FIXED** |
| Supervisor allowlist SSOT | 部分 | **FIXED** |
| Desktop Worker 名显示 | OPEN | **FIXED** |

## P3 收尾修复（2026-06-12）

1. **`test_executable_turn_uses_local_provider_when_available`** — `RecordingPlanProvider` 改为返回 FileAgent + `file.search_by_name`（dry_run），与 Supervisor FileAgent hint 及 Planner 工具面校验一致。
2. **Golden OS runs 回归** — `MockProvider._plan_from_supervisor_hint` 优先复用 `_build_goal_plan`（agent 匹配时）；`infer_supervisor_agent_hint` 将「整理+文件/发票」优先路由到 FileAgent（先于 DocumentAgent）。

---

1. 无效 `agent_hint` API 返回 422 而非静默推断
2. `infer_supervisor_agent_hint` 与 `DELEGATION_RULES` 合并
3. Partial strip 后 replan 或 fail-closed
4. Guard/retry 集成测试 + audit 事件断言
