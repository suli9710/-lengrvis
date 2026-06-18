审计完成。下面是 Round 7 可靠性视角的完整发现清单。先回答你指定的核心问题:三个 `RuntimeWarning` 是**测试环境的良性现象,不是"无 loop 时静默丢弃协程"的缺陷**——`_schedule_background`(run_service.py:991)有 loop 时用 `loop.create_task`,无 loop 时起新线程跑 `asyncio.run(coro)`,两条路径协程都会被执行;warning 出现在测试 teardown 时事件循环关闭、外层 wrapper task 还没来得及运行,导致内部协程对象未被 await 就被 GC。`task_service.py:166` 同理:`_spawn_background` 无 loop 时 `asyncio.create_task` 直接抛 `RuntimeError`(显式失败而非静默丢弃)。但审计在追踪过程中发现了线程回退路径上几个真实问题,见下。

---

# Round 7 可靠性审计报告(mavris backend)

## 发现清单

### 高严重度

**R7-H1【高 | services/browser_activity_runtime.py:247, 549】会话与事件无界增长(内存泄漏)**
- 证据:`self._sessions[session.id] = session`(L247)只增不删——`session_close`(L259)只改状态不从 dict 移除;`self._events.append(event)`(L549)为纯追加 list,全文件无任何 `pop`/eviction/`maxlen`。
- 影响:长期运行进程中浏览器活动越多内存越大,且 `list_events` 类查询越来越慢。
- 建议:`_events` 改用 `collections.deque(maxlen=N)` 或定期按时间裁剪;closed 会话延迟 TTL 后从 `_sessions` 移除。

**R7-H2【高 | orchestration/os_execution_engine.py:687-691】并行批次 stop 时丢弃已完成兄弟步骤的结果,可能导致重复执行副作用**
- 证据:

```687:691:backend/app/orchestration/os_execution_engine.py
            if stop_requested and work:
                for pending_work in list(work.keys()):
                    pending_work.cancel()
                await asyncio.gather(*work.keys(), return_exceptions=True)
                work.clear()
```

- 分析:`asyncio.wait(FIRST_COMPLETED)` 返回部分 done 集后,`work` 中可能残留**已完成但未收集**的任务(对已完成任务 `cancel()` 是 no-op)。这些任务的结果被 `gather(...)` 后直接丢弃,从不调用 `write_back_step`——而步骤状态只改在 snapshot 上(L649 隔离快照设计),真实 plan step 仍为 PENDING。工具副作用(写文件、发请求)已经发生,审批/恢复后该步骤会被当作未执行而**重跑**,产生重复副作用。对比:`handlers/step_scheduler_handler.py` 的 `_drain_running_after_stop`(L215-234)正确地对已完成兄弟做了写回,本函数没有。
- 建议:stop 排空时区分 done 与 pending:done 的照常 `write_back_step` + 记入 `results`,只 cancel 真正 pending 的。

### 中严重度

**R7-M1【中 | services/run_service.py:991-1037 + 354-369】线程回退路径的 run 无法中途取消**
- 证据:同步路由(`routes_runs` 的 resume/cancel、审批后 `resume_runs_for_task`)在 FastAPI 线程池中无运行 loop,`_schedule_background` 起新线程跑 `asyncio.run`,`_ACTIVE_RUN_TASKS[run_id]` 存的是 `concurrent.futures.Future`。`_cancel_active_run_task`(L354)对它只调 `work.cancel()`——`concurrent.futures.Future.cancel()` 对**已开始运行**的工作是 no-op。
- 影响:被 resume 的 run 取消时无法打断进行中的工具调用(最长工具超时 300s),只能等引擎 loop 在下一个 turn 边界轮询 `_run_cancelled` 才停。docstring 声称"cancels in-flight loops"与实际不符。
- 建议:线程路径在 runner 内记录该线程自己的 loop 与顶层 task 引用,取消时 `loop.call_soon_threadsafe(task.cancel)` 封送进去。

**R7-M2【中 | services/run_service.py:222-247】shutdown_runs 不排空线程路径的 run**
- 证据:`shutdown_runs` 只对 `asyncio.isfuture(...)` 的对象做 `gather`,`concurrent.futures.Future` 被排除,仅被(无效的)`cancel()`。线程是 daemon,进程退出时被硬杀,可能在 DB 写/工具执行中途死亡。
- 建议:对线程 future 用 `asyncio.wrap_future` 或 `to_thread(future.result, timeout)` 一并等待。

**R7-M3【中 | orchestration/tool_runtime.py(_SHARED_PATH_LOCKS)】路径写锁按事件循环隔离,跨 loop 失效**
- 证据:`_SHARED_PATH_LOCKS` 是 `WeakKeyDictionary` 以 loop 为 key 的 per-loop `asyncio.Lock` 集合。结合 R7-M1 的线程路径,主 loop 上的 run 与线程恢复的 run 各自持有独立锁实例,对同一文件路径的并发写**不互斥**。
- 建议:跨 loop 的路径互斥需改为 `threading.Lock`(在 `to_thread` 中获取)或进程级单写者队列。

**R7-M4【中 | api/routes_approvals.py:44、services/scheduler_service.py:197】绕过 orchestrator_registry 新建 Orchestrator,live 消息发到无人订阅的 bus**
- 证据:`await OrchestratorAgent().execute_approved_step(approval)`——审批执行用全新实例与全新 `AgentBus`,而 WS 客户端与 run bridge 订阅的是 registry 中绑定的那个 bus。消息有 DB 持久化兜底(终态 drain 能补齐),但**审批步骤执行期间的实时流丢失**。`scheduler_service` 创建任务后也不 `orchestrator_registry.bind`(对比 `task_service.create_task:56` 有 bind)。
- 建议:两处改用 `orchestrator_registry.get_or_create_for_task(task_id, OrchestratorAgent)`。

**R7-M5【中 | orchestration/run_event_bus.py:39 + core/db.py:1156】run 事件在事件循环线程上同步写 SQLite**
- 证据:`publish` 直接调 `db.insert_run_event`,持 `_EVENT_WRITE_LOCK` + `busy_timeout=5000`。高争用时阻塞整个 loop 最多 5s+。`_bridge_task_messages` 初始 replay 最多 1000 条消息逐条同步写,放大该问题。`agent_bus` 已用专用持久化写线程(`_PERSIST_QUEUE`/`_PERSIST_THREAD`)解决同类问题,`run_event_bus` 未跟进。
- 建议:复用 agent_bus 的写线程模式,publish 仅入队。

**R7-M6【中 | services/run_service.py:338】`router.cancel_run` 后台任务无强引用、异常无人观察**
- 证据:`_schedule_background(router.cancel_run(run.id), ...)` 返回的 task 未保存——`loop.create_task` 结果若无强引用可能被 GC 打断;其异常也只在 GC 时打 "never retrieved" 日志,取消子任务/清理读状态可能静默失败。
- 建议:存入模块级 task 集合 + `add_done_callback` 记录异常(或直接同步 await,该路径本就在专线程中)。

**R7-M7【中 | orchestration/execution_engine.py(InMemoryRunStore)】default_run_store 无淘汰**
- 证据:`_runs: dict[str, RunState]` 进程生命周期内只增不减,每个 RunState 含完整 plan 深拷贝。
- 建议:终态 run 在引擎 loop finally 中从 store 移除(DB 已有持久化),或加 LRU 上限。

### 低严重度

**R7-L1【低 | services/run_service.py:120/124、services/task_service.py:166】RuntimeWarning 为测试 teardown 良性现象,但暴露行为不一致**
- 证据:warning 中的协程名是 `create_run` L121/L124 处创建的协程对象;若 wrapper `run_with_data_dir` 执行过,内部协程必然被 await。即测试里 loop 在 wrapper task 调度前关闭→内部协程对象未 await 即 GC。生产中主 loop 常驻,不触发。`_spawn_background` 无 loop 时抛 `RuntimeError`(非静默);但 `_delegate_task` 缺少 `resume_task`(L218-226)那样的线程 fallback,同步上下文调用会直接炸,三个入口行为不一致。
- 建议:`_spawn_background` 加与 `_schedule_background` 一致的无 loop 线程 fallback;测试侧可在 fixture teardown 中 await 排空后台任务。

**R7-L2【低 | services/task_service.py:47-50】`_BACKGROUND_TASKS` 不读取任务异常**
- done callback 只 `discard`,协程内未捕获异常仅留 "Task exception was never retrieved" 日志。建议在 callback 中 `task.exception()` 并记日志。

**R7-L3【低 | services/scheduler_service.py:64, 77-95】`_fired_ids` 无界增长;`stop()` 超时 cancel 后不再 await**
- 建议:`_fired_ids` 限长;超时分支 cancel 后再 `gather(return_exceptions=True)` 一次。

**R7-L4【低 | perception/environment_stream.py:296, 373】`stop()` 不排空 `_pending_tasks`;`is_running()` 与 `call_soon_threadsafe` 之间有 loop 关闭竞态**
- 建议:stop 时 cancel + 限时 gather `_pending_tasks`;`call_soon_threadsafe` 包 `except RuntimeError`。

**R7-L5【低 | main.py(lifespan)+ run_service.recover_interrupted_runs:189】崩溃恢复只覆盖 RUNNING 的 run**
- PLANNING/EXECUTING_STEP/CONSULTATION 等非终态 Task 行在崩溃后成为孤儿。建议启动时一并扫描非终态 task 置 PAUSED。

**R7-L6【低 | orchestration/agent_bus.py:141 区域、run_event_bus.py:100 区域】`loop.is_closed()` 检查与 `call_soon_threadsafe` 之间的竞态**
- 已有 `suppress(RuntimeError)` 缓解,极端时序下个别订阅者丢一条投递。可接受,建议保留注释说明。

**R7-L7【低 | services/browser_activity_runtime.py:502 区域】`BrowserSession` 字段更新无 per-session 互斥**
- `_lock` 只护 dict 结构;并行步骤共用同一 session id 时 `current_url/title` 等字段可能交错。建议持锁更新或整对象原子替换。

**R7-L8【低 | services/task_pool.py】单例 `asyncio.Semaphore` 隐式绑定首个 loop**
- 当前代码路径(`handle_chat` 主 loop、`resume_task` 专线程绕过 pool)不踩雷,但若未来从线程路径调 `get_pool().submit` 会 `RuntimeError: ... different event loop`。属潜在地雷,建议 `get_pool` 按 loop 维度缓存或文档注明约束。

---

## 已验证良好

- **agent_bus 持久化写线程**:`_PERSIST_QUEUE` + 专线程串行写、`flush_agent_message_writes` 读屏障、`current_thread is _PERSIST_THREAD` 自死锁防护——正确解决了事件循环上同步写 DB 的问题。
- **plan_snapshot 隔离模式**:`snapshot_step`/`write_back_step` 深拷贝 + 串行写回;`step_scheduler_handler._drain_running_after_stop` 对已完成兄弟步骤正确写回(os_execution_engine 应向它看齐,见 R7-H2)。
- **orchestrator_registry**:RLock 保护、`get_or_create_for_task` 原子;`routes_chat` WS 循环中重解析 registry bus 并 re-subscribe,正确处理"先订阅 fallback bus、后绑定真 bus"的时序。
- **db.py**:WAL + `busy_timeout=5000`、`claim_scheduled_task_run` 用 `BEGIN IMMEDIATE` 原子认领、audit HMAC secret 在 `_EVENT_WRITE_LOCK` 外解析(无自死锁)、`_EVENT_WRITE_LOCK` 为 RLock 且无嵌套异序获取。
- **tool_runtime**:`asyncio.to_thread` 卸载阻塞工具 + `asyncio.wait_for` 超时、超时/取消路径 finally 释放路径锁。
- **引擎 loop 生命周期**(run_service.py:442-540 区域):finally 块按序 set stop_event → drain bridge → untrack active task → release router → 仅终态才释放 orchestrator registry,层次清晰。
- **跨线程取消封送**:`_cancel_active_run_task` 对 `asyncio.Task` 用 `get_loop().call_soon_threadsafe(work.cancel)`,正确。
- **scheduler `_executions`** 持强引用防 GC;**lengrvis_code 进程注册表** terminate→限时 wait→kill 的外部进程清理完整。
- **resource_state**:`_TASK_READ_STATES` 全程持锁,`cancel_run` 后 `clear_task_read_states` 清理读状态。
- **lifespan shutdown** 调 `shutdown_runs` + `flush_agent_message_writes` + `scheduler.stop()` + `task_pool.shutdown()`,主 loop 路径排空完整。
- **task_recording** 截图经 `asyncio.to_thread`,不阻塞 loop。

---

## 可靠性评分:**72 / 100**

**总评**:主事件循环上的 run 生命周期经多轮修复已相当扎实(注册表、快照、排空、超时清理均到位),剩余风险集中在三条"旁路"上——同步路由触发的线程+独立 loop 回退路径(取消失效、shutdown 不排空、路径锁失效)、并行批次 stop 时丢弃已完成结果导致的重复执行隐患、以及浏览器运行时/内存 store 的无界增长。
