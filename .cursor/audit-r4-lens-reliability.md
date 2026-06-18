# Round 4 可靠性/并发审计报告 — mavris backend

**审计范围：** 工作树当前磁盘代码（含未提交 R3 修复）
**基线：** `.cursor/audit-r3-lens-reliability.md`（74 分）、`.cursor/audit-r3-final-report.md`

---

## 一、R3 遗留风险逐项核实

### R3-1. `run_plan_turn` 回落全局 orchestrator — **PARTIAL（代码级仍在，生产路径被缓解）**

证据：

```186:187:backend/app/orchestration/os_execution_engine.py
        orchestrator = self._orchestrator()
        current = state or self._initial_state_for_plan(task, plan)
```

```1168:1175:backend/app/orchestration/os_execution_engine.py
    def _orchestrator_for_state(self, state: RunState) -> OrchestratorAgent:
        orchestrator = self._orchestrators_by_run.get(state.run_id)
        if orchestrator is not None:
            self.orchestrator = orchestrator
            return orchestrator
```

- `run_plan_turn` 及其全部下游 helper（`_select_turn_steps` L309、`_handle_step_graph_error` L267、`_finish_from_plan` L706、`_sync_task_status` L886、`_execute_selected_steps` L607、`_execute_one_step` L666、`process_plan` 失败路径 L173）仍使用 `self._orchestrator()`，且 `_orchestrator_for_state` 会**写覆盖 `self.orchestrator` 单字段**（L1171）——同一 engine 上并发双 run 在 await 点交错时会串 orchestrator/bus。
- **缓解事实**：`run_service._engine_router()`（`run_service.py:564-576`）现在**每个 run 创建全新 EngineRouter + 全新 OSExecutionEngine**（`create_run` L61、`_schedule_resume` L225），router 经 `_RUN_ENGINE_ROUTERS` 与 run 绑定；`task_service` 路径每 task 新建 `OrchestratorAgent()`（`task_service.py:53,142`），其 engine 构造时绑定 owner（`orchestrator_agent.py:88`）。生产路径上一个 engine 实际只服务一个 run。
- **结论**：并发双 run 在当前生产接线下**不会**串状态，但不变量靠"调用方每次 new engine"维系，无任何断言保护；engine 层 API 仍允许串。缺并发双 run 集成测试（R3 已指出，仍缺）。

### R3-2. 并行 step 共享可变 Task/Plan/PlanStep — **OPEN（未变）**

证据：

```157:162:backend/app/orchestration/handlers/step_scheduler_handler.py
            step_context = copy.deepcopy(context)
            work = asyncio.create_task(
                orchestrator._execute_step(task, plan, step, step_context, observation, threaded_tools=threaded_tools),
```

- `os_execution_engine.py:605-607` 同样只 deepcopy `context`，`task`/`plan`/`step` 原对象直接传入并行 task；`set_step_status` 直接改共享 step（`os_execution_engine.py:920`、`step_scheduler_handler.py:174`），`_finish_turn`/`_finish_from_plan` 在并行 task 仍可能在跑时 `db.upsert_model("plans", plan)`（`os_execution_engine.py:768,806`），并行 recovery（L629-637）也改同一 plan。
- 失败场景：两个并行 step 一个失败触发 recovery 改写 plan.steps，另一 step 同时被持久化 → 写入半新半旧的 plan 快照 / step 状态丢更新。
- 单 event loop 下交错只发生在 await 点（GIL + 协作式调度），实际破坏概率低于多线程，但 recovery/persist 路径有大量 await，窗口真实存在。仍无 `test_parallel task/plan 隔离` 测试。

### R3-3. cancel 无法 drain 旧 engine — **FIXED**

证据：

```38:40:backend/app/services/run_service.py
_ACTIVE_RUN_TASKS: dict[str, asyncio.Future | concurrent.futures.Future] = {}
_RUN_ENGINE_ROUTERS: dict[str, EngineRouter] = {}
_ACTIVE_RUN_TASKS_LOCK = threading.RLock()
```

```255:258:backend/app/services/run_service.py
        try:
            settings = get_effective_settings()
            router = _router_for_run(run.id, settings)
            _schedule_background(router.cancel_run(run.id), data_dir=_run_data_dir(run))
```

- run 级 engine registry 已落地：`_track_run_router`（L423，create/resume 均注册 L96/L480）、`_router_for_run`（L428）、loop 结束 `_release_run_router`（L420）。`cancel_run` 取到**同一 router 实例**，engine `cancel_run` 会 cancel + gather `_run_tasks`（`os_execution_engine.py:117-121`），再 `_cancel_active_run_task`（L274-288）跨线程 cancel 引擎 loop。有测试 `tests/test_run_router_registry.py::test_cancel_run_reuses_tracked_router`。
- 残余小缺口：router 已释放（run 自然结束）后 cancel 回落新建空 engine（L433），此时无在途 task，无害。

### R3-4. read-state 全局 dict 无锁 — **OPEN（未变）**

证据：

```83:88:backend/app/orchestration/resource_state.py
_TASK_READ_STATES: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
```

- 写入点 `remember_read_states_for_tool`（L234 链式 `setdefault`）在 `asyncio.to_thread` 工具线程中执行（`tool_runtime.py:1053-1054`），是**真正的多线程并发写**，无 `threading.Lock`。读 L389、清理 L86-88 同样裸访问。
- 缓解：key 已是 task→step→path 三级，并行 step 写不同子 dict，CPython GIL 使单次 setdefault 原子，实际丢失概率低；但属未文档化的实现依赖。

### R3-5. lifespan 关闭链 / desktop 退出 drain — **PARTIAL**

证据：

```104:118:backend/app/main.py
    finally:
        ...
        await scheduler.stop()
        await get_pool().shutdown()
```

- 后端进程内：关闭链完整（http client → session → watcher → env stream → scheduler → TaskPool → ollama），且 **scheduler.stop 现在会 drain 在途 executions（30s 超时）**（`scheduler_service.py:86-97`，R3 P1-08 已修复）。
- 缺口 1：run_service 的 engine loop 用 `loop.create_task` 直接调度（`run_service.py:888`），**不在 TaskPool 内**，lifespan 不会 cancel/await 它们，关停时被 uvicorn 直接掐断。
- 缺口 2：desktop 退出路径 `before-quit`（`main.ts:365-389`）只调 `backend.stop()` → Windows 上 `taskkill /T /F`（`backendProcess.ts:447-461`）**硬杀**，未先调 `/api/runtime/background`（`prepare_for_background` 的 8 秒 pause-drain，`run_service.py:150-167`）；background drain 只在托盘隐藏路径生效（`main.ts:139-153`）。

### R3-6. Playwright 同步冷启动 — **PARTIAL（未变）**

证据：

```119:124:backend/app/services/browser_activity_runtime.py
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
```

- 每个 observe/screenshot/wait/click 动作仍冷启动一次 chromium（L119-218，4 处）；经 `tool_runtime.py:1051-1058` 的 `to_thread + wait_for(timeout)` 不堵事件循环且有 300s 上限（`config.py:255`），但无 browser 复用，N 并发浏览器 step 可长时间占满默认线程池。

---

## 二、Round 4 全部发现（按严重度）

### Critical

无。R2/R3 时代的 Critical 级（cancel 不停任务、无超时、无 drain）均已关闭并有测试。

### High

| ID | 标题 | 证据 | 失败场景 | 状态 |
|----|------|------|----------|------|
| **R4-H1** | 并行 step 共享可变 `Task`/`Plan`/`PlanStep` | `step_scheduler_handler.py:157-162`、`os_execution_engine.py:605-607,629-637,768` | 并行 batch 中 recovery 改 plan 与另一 step 的状态写/持久化交错 → plan 半旧快照落库、step 状态丢更新 | R3遗留 P0-01 — **OPEN** |
| **R4-H2** | `run_plan_turn` 链仍走 `self._orchestrator()` 单字段，不变量靠调用方 new engine 维系 | `os_execution_engine.py:186,267,309,607,666,706,886,1171` | 任何未来调用方在共享 engine 上并发跑两 run → orchestrator/bus 串台 | R3遗留 P0-02 — **PARTIAL**（生产路径已被 per-run engine 缓解，代码级未收敛，缺并发双 run 测试） |
| **R4-H3** | **新发现：resume 路径 orchestrator/bus 失配，timeline 静默丢消息** | `os_execution_engine.py:1168-1175` 不查全局 `orchestrator_registry`；`run_service.py:481-489` bridge 订阅 `orchestrator_registry.bus_for_task` 的**旧** bus；resume 后 fresh engine 的 `run_turn → _orchestrator_for_state` 走 `_orchestrator()` 新建 orchestrator（新 bus），且不回写 registry | 暂停/审批后 resume 的 run，新 orchestrator 发布的 agent 消息发到新 bus，bridge 听旧 bus → run 时间线/审批事件丢失（部分由 `_bridge_task_messages` 的 DB 尾扫 L522-525 兜底，但实时流断） | **新发现** |
| **R4-H4** | 策略双轨（tool_runtime vs policy_engine） | R3 P0-18，本轮未见收敛代码 | 同一写操作两套审批判定可能给出不一致结论 | R3遗留 P0-18 — **OPEN**（架构债，跨透镜） |

### Medium

| ID | 标题 | 证据 | 失败场景 | 状态 |
|----|------|------|----------|------|
| R4-M1 | `_TASK_READ_STATES` 全局 dict 无锁，且写入发生在 to_thread 工具线程 | `resource_state.py:83,234,389`；`tool_runtime.py:1053` | 多线程 setdefault/清理竞争，极端下丢 read-state → 误报 READ_STATE_REQUIRED | R3遗留 P0-05 — **OPEN** |
| R4-M2 | desktop 退出硬杀，无 background drain | `main.ts:365-389` → `backendProcess.ts:438-474`（taskkill /T /F）；drain 仅托盘路径 `main.ts:147` | 用户直接退出时在途 run/审批/WAL 事务被掐断 | R3遗留 P0-12 — **PARTIAL** |
| R4-M3 | lifespan 不 drain run_service 引擎 loop（不在 TaskPool） | `run_service.py:867-888`（`loop.create_task`）；`main.py:104-118` 无 `_ACTIVE_RUN_TASKS` 处理 | 后端正常关停时在途 run 无 graceful 终止，run 卡 RUNNING | R3遗留（关闭链缺口）— **OPEN** |
| R4-M4 | SQLite 多写者 `database is locked` 风险 | `db.py:147-161`（每操作新连接，WAL+busy_timeout 5s）；`BEGIN IMMEDIATE` 热路径：run_events L634-636/L1126、audit L1168、approvals L1655 等 | 高事件吞吐 + watcher/scheduler 线程 + 跨进程（pytest 并行）下写超时报错；R3 实测已偶发 | R3遗留 — **PARTIAL**（WAL/busy_timeout 是缓解非根治；单进程内建议串行写队列） |
| R4-M5 | `orchestrator_registry._by_task` 永不释放（`release_task` 生产零调用） | `orchestrator_registry.py:61-66`；grep 仅测试调用；`os_execution_engine.py:131` 只 `release_run` | 长跑桌面进程 orchestrator+bus+engine 每 task 常驻 → 内存泄漏 | R3遗留 — **OPEN** |
| R4-M6 | AgentBus.publish 同步 DB 写在事件循环线程 | `agent_bus.py:26-29`（`db.upsert_model` 同步执行） | 高频消息发布时事件循环延迟尖刺，叠加 R4-M4 锁等待最长 5s 堵 loop | R3遗留 — **OPEN** |
| R4-M7 | **新发现：WS 订阅 fallback bus 失配** | `routes_chat.py:19,51`：orchestrator 未绑定时 `bus_for_task` 回落模块级 `bus`，此后 orchestrator 绑定 registry，发布走自己的 bus，早连的 WS 永远收不到 | 客户端在任务创建早期建立 WS → 只收到 heartbeat，无实时消息 | **新发现** |
| R4-M8 | InMemoryRunStore 无界 + 易失 | `execution_engine.py:35-66`（`_runs` 无 eviction；进程重启全丢，靠 `_state_from_run` 从 runs 表重建 L612-628） | 长跑进程 RunState 累积；崩溃后 resume 依赖 DB 快照的降级重建 | R3遗留 — **PARTIAL**（DB 重建路径已存在） |

### Low

| ID | 标题 | 证据 | 失败场景 | 状态 |
|----|------|------|----------|------|
| R4-L1 | `_resume_engine_loop` 吞掉 resume 异常且不打日志 | `run_service.py:474-477`（`except Exception: resumed = state`） | resume 失败被静默吞掉，以旧 state 继续跑 | 新发现 |
| R4-L2 | scheduler `_fired_ids` 无界增长 | `scheduler_service.py:64,182`（只 add 不清理） | 长跑进程缓慢内存增长 | 新发现 |
| R4-L3 | WS 每连接重放全部历史消息 + `sent_message_ids` 无界 | `routes_chat.py:53-58`；AgentBus 队列 drop-oldest（`agent_bus.py:91-96`）丢消息无 resync 信号 | 长任务大量消息时连接慢、慢客户端静默丢中段事件 | 新发现 |
| R4-L4 | 非 strict 状态机为默认（`strict_state_machine=False`） | `config.py:254,578`；非法迁移仅 audit 不持久化（`state_machine.py:72-76`，行为正确） | 非法迁移静默被忽略，依赖 audit 才能发现编排 bug | R3遗留 — 行为已修复（P1-02 FIXED），默认值保守性存疑 |
| R4-L5 | `_cancel_running_steps` gather 后步骤标记顺序问题（R3 Low） | `step_scheduler_handler.py:229-240`：先 gather 后才对 `state.running.values()` 标 FAILED，未 pop | 二次 cancel 边缘 case 下重复标记；实际由外层 raise 兜住 | R3遗留 — OPEN（影响极小） |
| R4-L6 | browser `_events` 无界 append | `browser_activity_runtime.py:225` | 长 session 内存增长 | R3遗留 — OPEN |

### 已确认修复（本轮验证通过的 R3 项）

- **P0-03 cancel drain**：`os_execution_engine.py:113-132` + `run_service.py:274-288` + `test_cancel_run_drains_tasks`。
- **R3-014 run 级 engine registry**：`run_service.py:39,423-438` + `test_run_router_registry.py`。✅ 本轮关闭
- **P1-08 scheduler stop 不等 executions**：`scheduler_service.py:86-97` 已 drain（30s + cancel 兜底）。✅ 本轮关闭
- **P0-11 工具超时**：`tool_runtime.py:1051-1058`（to_thread + wait_for，默认 300s 可配）。LLM 侧 httpx 双层超时（`openai_compatible.py:48,167`，默认 30s/60s）。
- **P0-10 lifespan TaskPool shutdown**：`main.py:115` + `task_pool.py:85-91`。
- **P1-03 / P1-02 / AgentBus 实例隔离 / scheduler 原子 claim**（`db.py:922-1007` BEGIN IMMEDIATE compare-and-claim）均维持 FIXED。
- 状态机本身（`state_machine.py`）未见新竞态：transition 是同步函数，读改写在单次调用内完成；真正的竞态面在上层并行共享 task（即 R4-H1）。

---

## 三、可靠性透镜评分

### **70 / 100**（封顶生效）

| 维度 | 权重 | 得分 | 说明 |
|------|------|------|------|
| 并行正确性 | 30% | 21/30 | context 隔离 + fatal cancel 稳；**task/plan 共享（H1）仍 OPEN**，orchestrator 单字段（H2）未收敛 |
| 取消 / 关闭 | 20% | 17/20 | run 级 router registry（H3→FIXED）+ scheduler drain 是本轮实质进步；lifespan 不 drain 引擎 loop、desktop 硬杀仍欠 |
| 资源 / TOCTOU | 20% | 16/20 | 路径锁 + step 级 read-state 维持；全局 dict 无锁且确在多线程写 |
| 超时 / 背压 | 15% | 12/15 | tool/LLM 超时完备；WS drop-oldest 无 resync、AgentBus 同步 DB 写、SQLite 锁竞争 |
| 架构隔离 | 15% | 10/15 | per-run engine 实例化大幅缓解串台；registry 泄漏、resume bus 失配（新 H3）、策略双轨 |

**原始合计约 76**；按严格规则 **R4-H1（OPEN High）封顶 70**。

**评分理由**：相对 R3（74/封顶70），本轮净进步是 run 级 engine registry 和 scheduler 排水两项关闭，且确认 per-run engine 实例化使"并发双 run 串 orchestrator"在现有生产接线下不可触发。但 (1) 并行共享 Task/Plan 这一 R2 时代的 P0-01 连续三轮未动，仍是唯一可直接导致**状态损坏**的 OPEN High；(2) 新发现 resume 路径 bus 失配会静默丢 run 事件流；(3) 关闭链在"后端进程内"完整但"desktop 退出"与"引擎 loop drain"两端仍开口。建议 R5 优先级：① 并行 batch 改 plan 快照 + 串行写回（H1）；② `_orchestrator_for_state` 接入全局 registry 并在 resume 时回绑 bus（H3，改动小收益大）；③ `run_plan_turn` 全链改用 per-state orchestrator + 并发双 run 集成测试（H2 收口）。
