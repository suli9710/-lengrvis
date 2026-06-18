# Round 6 修复冲刺 P2 报告 — Runs/Perception Hint + R6-H2 披露

**日期：** 2026-06-12  
**基线：** `.cursor/audit-r6-fix-sprint-report.md`（R6-H1 FIXED）  
**范围：** Runs/Perception hint 通道统一、Planner 工具过滤、Developer Engine 只读披露（R6-H2）

---

## 修复明细

### Runs / Perception `agent_hint` 通道 — FIXED ✅

| 组件 | 变更 |
|------|------|
| `backend/app/agents/worker_agents.py` | **新建** — 6 Worker allowlist SSOT |
| `backend/app/agents/delegation_metadata.py` | **新建** — `build_task_delegation_metadata`、`infer_supervisor_agent_hint`、`merge_run_task_metadata`、`developer_engine_capabilities` |
| `backend/app/services/run_service.py` | `create_run(..., agent_hint=, task_metadata=)` → metadata 传入 EngineRouter |
| `backend/app/orchestration/engine_router.py` | `start_run(..., task_metadata=)` |
| `backend/app/orchestration/os_execution_engine.py` | `create_task_shell(..., metadata=task_metadata)` |
| `backend/app/services/perception_suggestion_service.py` | `launch_suggestion` 传递 `suggestion.agent_hint` |
| `backend/app/services/task_service.py` | 系统诊断捷径 `create_run(..., agent_hint="ComputerAgent")` |
| `backend/app/orchestration/handlers/planning_handler.py` | hint 存在时 Planner 可见工具过滤为 hint owner + `tool.search`；TypeError fallback 保留 `agent_hint` |

**数据流（Runs API）：**

```
create_run(goal, agent_hint?)
  → merge_run_task_metadata (explicit > infer(goal))
  → OSExecutionEngine.create_task_shell(metadata)
  → PlanningHandler → 工具过滤 + Planner hint block
  → engine_capabilities_for_run 回读 supervisor_agent_hint
```

### R6-H2 Developer Engine 只读披露 — FIXED ✅

| 层 | 实现 |
|----|------|
| SSOT | `DEVELOPER_ENGINE_DISCLOSURE` / `developer_engine_capabilities()` |
| Engine | `developer_engine.py` — `capability_mode` + `capability_disclosure` + `writes_enabled: false` |
| API | `RunCreateResponse` / `RunStateResponse.engine_capabilities` |
| Desktop | `runEngineAgentName` →「开发引擎（只读）」；`runDescription` 附加 disclosure 文案 |

---

## 8-Agent 交叉审查（每改动文件 ≥4 lens）

| 文件/域 | Sec | Rel | Arch | Logic | 综合 |
|---------|-----|-----|------|-------|------|
| `worker_agents.py` + `delegation_metadata.py` | PASS | WARN | WARN | WARN | WARN |
| `run_service.py` + OS engine 链 | WARN | WARN | PASS | WARN | WARN |
| `planning_handler.py` 工具过滤 | WARN | WARN | PASS | WARN | WARN |
| `developer_engine` + API + desktop | PASS | PASS | PASS | PASS | **PASS** |
| `test_delegation_metadata.py` | PASS | WARN | PASS | WARN | PASS |

**审查后追加硬化：** `build_task_delegation_metadata` 对 `extra["supervisor_agent_hint"]` 做 allowlist 归一化；显式 `agent_hint` 优先于 metadata 中的冲突值。

**共识（无 FAIL）：**
- 无权限绕过；hint 为规划偏好 + 工具面缩减
- 真实 LLM 仍软约束；`POST /runs` 无 `agent_hint` 字段（仅 goal 推断）
- `handle_user_goal` / `_task_for_state` 重建 shell 仍为 bypass（R8 候选）

---

## 验证汇总（Coverage Gate）

| 命令 | 结果 |
|------|------|
| `pytest tests/test_delegation_metadata.py` | **8 passed** |
| `pytest tests/test_agent_hint_routing.py` | **9 passed** |
| `pytest tests/test_golden_tasks.py` | **39 passed** |
| `pytest tests/test_execution_engines.py` (developer 子集) | **1 passed** |
| `pytest tests/test_runs_api.py::test_run_api_routes_developer_engine_and_replays_events` | **1 passed** |
| Desktop `vitest` `runEngineAgentName` | **新增 1 case** |

Gate：**PASS**

---

## 状态迁移

| 项目 | P1 后 | P2 后 |
|------|-------|-------|
| R6-H1 chat → Planner | FIXED | FIXED |
| Runs/Perception hint bypass | OPEN | **FIXED（OS 路径 + 推断）** |
| R6-H2 Developer 只读披露 | OPEN | **FIXED（API + Desktop）** |
| Planner 真实 LLM 硬校验 | OPEN | OPEN（R8） |
| `POST /runs` 显式 `agent_hint` | — | OPEN（R8） |

---

## R8 候选

1. `RunCreateRequest.agent_hint` 可选字段
2. `SupervisorAgent` 改用 `worker_agents` SSOT，删除重复 allowlist
3. Post-plan 工具 owner 与 hint 硬校验
4. `PlanningHandler` 工具过滤集成测试
5. Desktop 使用 `engine_capabilities.supervisor_agent_hint` 显示 Worker 名称
