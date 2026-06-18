# Round 3 透镜审计 — 可靠性 / 并发

**日期：** 2026-06-12  
**透镜：** Reliability & Concurrency（严格）  
**源码范围（9 模块）：**

| 模块 | 路径 |
|------|------|
| OS 执行引擎 | `backend/app/orchestration/os_execution_engine.py` |
| Step 调度器 | `backend/app/orchestration/handlers/step_scheduler_handler.py` |
| 资源状态 | `backend/app/orchestration/resource_state.py` |
| 工具运行时 | `backend/app/orchestration/tool_runtime.py` |
| Lifespan | `backend/app/main.py` |
| Orchestrator 注册表 | `backend/app/orchestration/orchestrator_registry.py` |
| Agent 总线 | `backend/app/orchestration/agent_bus.py` |
| 状态机 | `backend/app/orchestration/state_machine.py` |
| 浏览器活动运行时 | `backend/app/services/browser_activity_runtime.py` |

**基线：** Round 2 P0 表（`.cursor/audit-r2-final-report.md` §3）+ Sprint P1/P2/P3 修复报告  
**验证：** 对照源码 + 回归测试（`test_parallel_context_isolation`, `test_cancel_run_drains_tasks`, `test_tool_timeout`, `test_lifespan_shutdown`, `test_orchestrator_registry`, `test_skipped_completion_semantics`, `test_state_machine_nonstrict`, `test_tool_runtime::test_dry_run_preview_serializes_with_real_write_on_same_path`）

---

## 1. 执行摘要

| 指标 | Round 2 | Round 3 |
|------|---------|---------|
| 本透镜相关 P0 | 11 | 11（同 ID 追踪） |
| **FIXED** | 0 | **5** |
| **PARTIAL** | 0 | **4** |
| **OPEN** | 11 | **2** |
| 透镜得分（0–100） | **38** | **74** |

Round 2 在本透镜内几乎无修复；Sprint 后 **cancel / fatal 中止 / tool timeout / lifespan drain / dry-run 路径锁 / 并行 context 隔离** 均有代码与测试支撑。剩余风险集中在 **跨 run orchestrator 回落**、**并行共享 task/plan 对象**、**全局 read-state 无锁**、**浏览器冷启动** 与 **Orchestrator 多入口生命周期**。

---

## 2. Round 2 P0 对照表（本透镜范围）

| ID | R2 发现 | R3 状态 | 证据 / 备注 |
|----|---------|---------|-------------|
| **P0-01** | 并行 step 共享 `context`/`task`/`plan` 可变对象 | **PARTIAL** | `copy.deepcopy(context)` 已用于并行 launch（`step_scheduler_handler.py:157`, `os_execution_engine.py:605`）；`test_parallel_context_isolation.py` 通过。**仍共享**同一 `Task`/`Plan`/`PlanStep` 实例，并行 `_execute_step` 可交错修改 step.status / plan.steps。 |
| **P0-02** | `run_plan_turn` 回落 `_orchestrator()` 单例；EngineRouter 全局单引擎 | **PARTIAL** | 新增 `orchestrator_registry.py` + `_orchestrators_by_run`；`process_plan`/`run_turn`/`cancel_run` 用 `_orchestrator_for_state`。**但** `run_plan_turn` L186 仍 `orchestrator = self._orchestrator()`，并发多 run 时 `self.orchestrator` 可被覆盖。 |
| **P0-03** | `cancel_run` 不 cancel 在途 asyncio.Task | **FIXED** | `_run_tasks` + `_register_run_task` + `cancel_run` gather（`os_execution_engine.py:113-121, 862-874`）；`test_cancel_run_drains_tasks.py`。 |
| **P0-04** | 并行遇 fatal 不中止同批步骤 | **FIXED** | `stop_requested` + cancel 兄弟 task（scheduler L639-644, engine L639-644）；`test_parallel_context_isolation` fatal 用例 <1s 完成。 |
| **P0-05** | `_TASK_READ_STATES` 任务级共享无锁 | **PARTIAL** | 读写改为 **step 级** key（`resource_state.py:233-234, 376-389`）；`test_parallel_context_isolation` 隔离通过。**仍** 模块级 `dict` 无 `threading.Lock`；多线程 / 多 worker 下 upsert 非原子。 |
| **P0-06** | dry-run 跳过路径写锁（TOCTOU） | **FIXED** | `_write_lock_keys` 对 `supports_dry_run` 工具仍加路径锁（`tool_runtime.py:1071-1086`）；`test_dry_run_preview_serializes_with_real_write_on_same_path` 验证 preview/write 互斥。 |
| **P0-10** | lifespan 无 `TaskPool.shutdown()` | **FIXED** | `main.py:115` `await get_pool().shutdown()`；`test_lifespan_shutdown.py`。 |
| **P0-11** | 工具执行无全局 timeout | **FIXED** | `asyncio.wait_for` + `asyncio.to_thread`（`tool_runtime.py:1051-1058`）；可配置 `tool_timeout_seconds`；`test_tool_timeout.py`。 |
| **P0-14** | 每次操作 sync Playwright 冷启动阻塞 loop | **PARTIAL** | `LocalBrowserActivityAdapter.perform` 仍 **同步** `sync_playwright()` 每次新建 browser（`browser_activity_runtime.py:119-215`）。经 `ToolRuntime._execute_tool_body` → `to_thread` **不阻塞 event loop**，但 **线程池可被长时间占用**，且无 browser 复用 / 连接池。 |
| **P0-17** | Orchestrator 三套生命周期分裂 | **PARTIAL** | `OrchestratorRegistry` 统一 task/run 绑定（`orchestrator_registry.py`）；OS engine / run_service / chat 已接线。**仍** `task_service` 直建 orchestrator、`release_run` 不释放 `_by_task`、无 TTL  eviction。 |
| **P0-18** | 策略双轨执行（tool_runtime vs policy_engine） | **OPEN** | `review_and_maybe_prepare_approval` 与 `_requires_runtime_approval` / `permission_mode` 仍双路径；架构债未在本 sprint 收敛。 |

### 本透镜外 P0（仅索引，不计分）

P0-07 MCP SSRF、P0-08 Cloud LLM SSRF、P0-09 Desktop token、P0-12 桌面硬杀、P0-13 dev:web、P0-15 配对码、P0-16 ORT 脚本 — 已在其他 sprint 处理或仍 OPEN，见 R2 报告。

---

## 3. Round 2 P1 相关项（本文件触及）

| ID | 发现 | R3 状态 |
|----|------|---------|
| P1-02 | non-strict 非法迁移仍写入 | **FIXED** — `state_machine.py:72-76` 非 strict 仅 audit 后 `return task`，不 upsert；`test_state_machine_nonstrict.py` |
| P1-03 | 全 SKIPPED → COMPLETED | **FIXED** — `_all_steps_skipped_with_blocked_dependencies` / `_has_success_with_blocked_skips` → FAILED（`step_scheduler_handler.py:264-269`）；`test_skipped_completion_semantics.py` |
| P1-08 | scheduler stop 不 await executions | **OPEN**（`scheduler_service.py` 不在本透镜 9 文件内，未改） |
| P1-18 | AgentBus 类级订阅串扰 | **FIXED** — 订阅表为实例字段（`agent_bus.py:22-24`）；`test_orchestrator_registry.py::test_agent_bus_instances_do_not_share_subscriptions` |

---

## 4. Round 3 新发现（透镜内）

| Sev | 位置 | 发现 |
|-----|------|------|
| High | `os_execution_engine.py:178-186` | `run_plan_turn` 未使用 `_orchestrator_for_state(state)`，与 `process_plan`/`run_turn` 不一致 → 并发 run 串 orchestrator |
| Medium | `orchestrator_registry.py:57-59` | `release_run` 仅 pop run 映射，不 `release_task`；长跑进程 orchestrator/bus 常驻内存 |
| Medium | `browser_activity_runtime.py:225-226` | `_events` 无界 append；长 session 内存增长 |
| Medium | `agent_bus.py:26-29` | `publish` 同步 `db.upsert_model`；高吞吐 WS 下延迟尖刺 |
| Low | `step_scheduler_handler.py:229-240` | `_cancel_running_steps` gather 后未 pop `state.running`，依赖外层 break；可读性 / 二次 cancel 边缘 case |

---

## 5. 模块级快照

### 5.1 `os_execution_engine.py`

- **强：** cancel drain、并行 fatal 取消、registry 绑定、reflection defer。
- **弱：** `run_plan_turn` orchestrator 选择；`process_plan` 失败路径 L173 用 `_orchestrator()`；并行仍共享 task/plan。

### 5.2 `step_scheduler_handler.py`

- **强：** deepcopy context、stop/drain、blocked-skip → FAILED、CancelledError 传播。
- **弱：** 与 OS engine 双份调度逻辑，长期漂移风险。

### 5.3 `resource_state.py`

- **强：** step 级 read cache、TTL、approval 态 compare、replan 标记。
- **弱：** 全局 dict 无锁；并行 **不同 step 写同一 path** 依赖 ToolRuntime 路径锁而非 read-state 锁。

### 5.4 `tool_runtime.py`

- **强：** path lock（含 dry-run）、to_thread + timeout、ResourceStateError 结构化、WeakKeyDictionary per-loop locks。
- **弱：** P0-18 策略双轨；`_execute_tool_under_locks` 递归 async with 多锁顺序固定为 sorted keys（死锁风险低）。

### 5.5 `main.py` lifespan

- **强：** 关闭链 `watcher → environment_stream → scheduler → task_pool → ollama`。
- **弱：** 无显式等待 in-flight run / OS engine task 完成（依赖 pool shutdown 超时）。

### 5.6 `orchestrator_registry.py`

- **强：** RLock、task/run 双索引、bus 隔离。
- **弱：** 无 release 策略文档；`get_or_create_for_task` 与 OS engine `_new_orchestrator` 可能重复 factory。

### 5.7 `agent_bus.py`

- **强：** 实例订阅、loop 关闭清理、队列 drop-oldest。
- **弱：** 跨线程 publish 依赖 `call_soon_threadsafe`；同步 DB 写。

### 5.8 `state_machine.py`

- **强：** strict / non-strict 语义清晰；非法迁移 audit。
- **弱：** 默认 `strict_state_machine` 仍可能为 False（配置层）。

### 5.9 `browser_activity_runtime.py`

- **强：** SSRF 私网拦截、敏感 selector、approval 门控、RLock on sessions。
- **弱：** 每 action 冷启动 Playwright；adapter 无 async；session 与 tool 线程模型割裂。

---

## 6. 测试覆盖（透镜）

| 行为 | 测试 | 状态 |
|------|------|------|
| 并行 context 隔离 | `test_parallel_context_isolation.py` | ✅ |
| fatal 取消兄弟 step | 同上 | ✅ |
| cancel_run drain | `test_cancel_run_drains_tasks.py` | ✅ |
| tool timeout | `test_tool_timeout.py` | ✅ |
| lifespan pool shutdown | `test_lifespan_shutdown.py` | ✅ |
| dry-run / write 路径锁 | `test_tool_runtime.py` | ✅ |
| registry / bus 隔离 | `test_orchestrator_registry.py` | ✅ |
| non-strict 不持久化 | `test_state_machine_nonstrict.py` | ✅ |
| blocked skip 语义 | `test_skipped_completion_semantics.py` | ✅ |
| 并行 task/plan 隔离 | — | ❌ 缺失 |
| 并发双 run orchestrator 不串 | — | ❌ 缺失 |
| browser 线程池耗尽 / 泄漏 | — | ❌ 缺失 |

---

## 7. 透镜得分：**74 / 100**

| 维度 | 权重 | 分 | 说明 |
|------|------|-----|------|
| 并行正确性 | 30% | 20/30 | context 隔离 + fatal cancel；task/plan 共享未解 |
| 取消 / 关闭 | 20% | 17/20 | cancel + lifespan 好；browser/run 全局 drain 弱 |
| 资源 / TOCTOU | 20% | 16/20 | 路径锁 + step read scope；全局 dict 无锁 |
| 超时 / 背压 | 15% | 13/15 | tool timeout；browser 长占线程 |
| 架构隔离 | 15% | 8/15 | registry/bus 改善；orchestrator 多入口 + P0-18 |

**较 R2（38）提升 +36：** Sprint 交付了 R2 本透镜最致命的 5 项 P0 修复；未达 85+ 主因是 **P0-02 残留** 与 **P0-01 task/plan 共享**。

---

## 8. Top 5 OPEN（优先修复）

1. **P0-02 残留 — `run_plan_turn` orchestrator 回落**  
   - 修复：`run_plan_turn` 全程使用 `_orchestrator_for_state(current)`；禁止并发 run 写 `self.orchestrator` 单字段。  
   - 测试：两 run 并行 `process_plan`，断言 bus / handler 不交叉。

2. **P0-01 残留 — 并行共享 `Task`/`Plan`/`PlanStep`**  
   - 修复：并行 batch 只读 plan 快照 +  per-step 状态写回串行化；或 step 级锁。  
   - 测试：并行改 step.status 不丢更新。

3. **P0-05 残留 — `_TASK_READ_STATES` 无同步**  
   - 修复：`threading.RLock` 或 per-task asyncio.Lock；文档化单进程假设。  
   - 测试：并行 remember + validate 同 path。

4. **P0-14 残留 — Browser Playwright 冷启动 + 线程占用**  
   - 修复：session 级 browser 复用、专用 executor 限额、或 async Playwright。  
   - 测试：N 并发 browser.observe 不拖死 pool。

5. **P0-17 / P0-18 — Orchestrator 生命周期 + 策略单轨**  
   - 修复：`release_task` 与 run 完成挂钩；收敛 approval 决策到单一 policy 面。  
   - 测试：registry 无泄漏；approval 路径一致性。

---

## 9. 中文摘要

**结论：** 相对 Round 2， orchestration 可靠性有 **实质性改善**。原先最危险的「取消不停任务、并行 fatal 不止损、工具无超时、进程退出不 drain、dry-run 无锁」均已 **FIXED** 并有测试。AgentBus 实例隔离、状态机 non-strict 语义、SKIPPED 完成判定亦已修正。

**仍未关闭的核心：** （1）OS 引擎 `run_plan_turn` 仍用全局式 `_orchestrator()`，多 run 并发可能 **串 orchestrator**；（2）并行只复制了 context，**task/plan 仍共享可变对象**；（3）资源 read-state 全局 dict **无锁**；（4）浏览器每次 **同步冷启动 Playwright**，靠 thread pool 隔离但不省资源；（5）Orchestrator **多入口 + 策略双轨** 架构债。

**建议下一步：** 优先 1–2 项（一周可测可合并），再 browser pool + registry eviction，最后 policy 单轨 refactor。

**透镜得分：74/100**（R2 同透镜约 38/100）。

---

*Round 3 Reliability/Concurrency 透镜 — 基于源码与 R2 P0 追踪生成。*
