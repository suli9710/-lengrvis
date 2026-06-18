# Round 6 修复冲刺报告 — R6-H1 agent_hint 路由 + Golden 覆盖

**日期：** 2026-06-12  
**基线：** `.cursor/audit-r6-final-report.md`（能力审计 68/C，R6-H1 OPEN High）  
**范围：** Supervisor `agent_hint` → `task.metadata` → Planner；browser/search golden 任务

---

## 修复明细

### R6-H1（High）：Supervisor hint 未进入 Planner — FIXED ✅

**问题：** 主管 Agent 的 `agent_hint` 只用于 UI/审计文案，Planner 与 MockProvider 看不到委派信号，导致「已交给浏览器/搜索 Agent」与真实计划（如 `file.search_by_name`）不一致。

**改动链：**

| 文件 | 变更 |
|------|------|
| `backend/app/services/task_service.py` | `_delegate_task` 写入 `metadata["supervisor_agent_hint"]`（allowlist 归一化）；`_run_task_through_orchestrator` 经 `get_task` 重载 DB；TaskPool 经 `_enqueue()` 正确入队 |
| `backend/app/orchestration/handlers/planning_handler.py` | 从 `task.metadata` 读取 hint 并传入 `create_plan` |
| `backend/app/agents/planner_agent.py` | `KNOWN_SUPERVISOR_WORKER_AGENTS`、`normalize_supervisor_agent_hint`、`format_supervisor_hint_block`；确定性计划按 hint 跳过跨 Agent 捷径 |
| `backend/app/llm/prompts/planner_user.md` | 新增 `$supervisor_hint_block` |
| `backend/app/llm/mock_provider.py` | **根因修复：** 在 `User goal:` 切分**之前**提取 `Supervisor routing hint:`，Mock 测试路径可生成正确 browser/search 计划 |
| `backend/app/agents/orchestrator_agent.py` | 审计事件附带 `supervisor_agent_hint` |

**数据流：**

```
SupervisorAgent.decide → _delegate_task(metadata)
  → create_task_shell (DB upsert)
  → TaskPool → get_task 重载
  → PlanningHandler → PlannerAgent.create_plan(agent_hint=…)
  → planner_user.md hint block + 确定性门控
  → MockProvider._plan_from_supervisor_hint（测试/Mock）
```

### Golden 覆盖 — browser / search — ADDED ✅

| Golden ID | 断言 |
|-----------|------|
| `gt-chat-browser-hint` | delegate → BrowserAgent；metadata hint；plan `[browser.read_page]` |
| `gt-chat-search-hint` | delegate → SearchAgent；metadata hint；plan `[search.query]` |
| `gt-tool-tool-search` | `tool.search` 在 deferred stub 下 `total ≥ 1` |

`test_data/golden_tasks/golden_tasks.json` 增至 **37** 任务；数据集完整性门禁含 `browser` / `search` 类别。

### 测试 — ADDED ✅

- `backend/tests/test_agent_hint_routing.py`（8 用例）：whitelist、确定性跳过、prompt 注入、metadata 持久化、MockProvider hint 提取
- `backend/tests/test_golden_tasks.py`：`_register_golden_deferred_tools` 支持 `deferred_tools` fixture

---

## 8-Agent 交叉审查（每改动文件 ≥4 lens）

审查模型：Security / Reliability / Architecture / Logic（4 global lenses × 9 文件 = 每文件 4 次审查）

| 文件 | Sec | Rel | Arch | Logic | 综合 |
|------|-----|-----|------|-------|------|
| `task_service.py` | WARN | WARN | WARN | PASS | WARN |
| `planning_handler.py` | PASS | WARN | PASS | PASS | WARN |
| `planner_agent.py` | PASS | PASS | PASS | WARN | WARN |
| `mock_provider.py` | WARN | WARN | PASS | WARN | WARN |
| `planner_user.md` | PASS | PASS | PASS | PASS | PASS |
| `orchestrator_agent.py` | WARN | PASS | PASS | PASS | WARN |
| `test_agent_hint_routing.py` | PASS | PASS | PASS | WARN | PASS |
| `test_golden_tasks.py` | PASS | PASS | PASS | PASS | PASS |
| `golden_tasks.json` | PASS | PASS | PASS | PASS | PASS |

**审查共识（无 FAIL）：**

1. **Security (WARN)：** hint 为路由偏好非授权边界；已在写入/读取侧 `normalize_supervisor_agent_hint`；Mock 全 prompt 正则提取可被 memory  spoof（仅 mock/dev 路径）。
2. **Reliability (WARN)：** 真实 LLM 仍靠软 prompt 引导；runs/perception 入口未携带 hint；TaskPool 重复 submit 仍为既有风险。
3. **Architecture (WARN)：** chat/mobile 路径 SSOT 清晰；`/api/runs`、perception launch、system-diagnostics 捷径仍 bypass metadata。
4. **Logic (WARN)：** 未知/大小写错误 hint fail-open 放开确定性门控；Mock hint 计划 goal-blind（golden 回归可接受）。

**审查后追加硬化：** `_delegate_task` 与 `PlanningHandler` 写入/读取时使用 `normalize_supervisor_agent_hint`。

---

## 验证汇总（Coverage Gate）

| 命令 | 结果 |
|------|------|
| `pytest tests/test_agent_hint_routing.py` | **8 passed** |
| `pytest tests/test_golden_tasks.py` | **38 passed**（含 dataset integrity） |
| `pytest tests/test_supervisor_chat_flow.py` | **18 passed** |
| **定向套件合计** | **64 passed / 0 failed** (~56s) |

Gate：**PASS** — R6-H1 修复路径有单元 + golden + supervisor flow 三重证据。

---

## 状态迁移

| 项目 | R6 终报 | 本冲刺后 |
|------|---------|----------|
| R6-H1 hint → Planner | OPEN High | **FIXED（chat/mobile + mock/golden 实证）** |
| R6-M1 Browser golden | 0 覆盖 | **ADDED（gt-chat-browser-hint）** |
| R6-M2 Search golden | 0 覆盖 | **ADDED（gt-chat-search-hint + gt-tool-tool-search）** |
| R6-H2 Developer 只读披露 | OPEN High | **未改（R7 候选）** |

---

## 剩余建议（R7 候选）

1. **统一委派入口：** `create_delegated_task()` 供 runs/perception/system-diagnostics 复用，消除 hint bypass。
2. **真实 LLM 硬校验：** plan 首步 `agent_name` / tool owner 与 hint 不一致时 auto-revise 或限制 tool 列表。
3. **集中 worker allowlist：** `app/agents/worker_agents.py` 供 Supervisor / Planner / mobile 共用。
4. **MockProvider 边界：** 仅从 `$supervisor_hint_block` 段解析 hint，禁止全 prompt 正则。
5. **R6-H2 产品披露：** Developer Engine `writes_enabled=False` 在 UI/文档显式说明。
