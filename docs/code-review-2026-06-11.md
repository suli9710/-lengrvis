# Mavris (Lengrvis) 全项目代码审查与优化建议报告

> 审查日期：2026-06-11
> 审查范围：后端编排与 Agent 层、数据/服务/API 层、工具与安全策略层、桌面端 Electron + React、测试与工程化体系（五个方向均已完成）
> 严重程度定义：🔴 高 = 正确性/安全/全局性能问题，应尽快修复；🟡 中 = 显著性能或可维护性问题；🟢 低 = 局部优化与代码卫生

---

## 总体结论

项目的**安全设计基线明显高于同类原型**（Electron 安全三件套全开、IPC 纵深防御、HMAC 审批绑定广泛使用 `hmac.compare_digest`、DPAPI 密钥加密、审计哈希链、审批原子消费），但存在四个**系统性短板**：

1. **同步阻塞遍布 async 事件循环**——工具执行、SQLite、ONNX 推理、OCR 都在事件循环线程同步执行，这是全局性能问题的根源
2. **SQLite 使用方式原始**——每操作新建连接、无 WAL、`init_db()` 在 18 处热路径被反复全量执行、关键索引缺失
3. **前端"轮询 + 推送"双通道驱动单体根组件**——已降级为 WS 主通道 + 10s/30s 兜底轮询；`App.tsx` 仍偏大，AbortController 已贯通 IPC `abortGroup`
4. **Python 侧静态检查链完全空白**——无 ruff/mypy/pre-commit，代码里的 `# noqa` 全是死注释

vision 工具路径授权绕过（3-H1）已在第一批修复；剩余风险见 4-H5 desktop token 契约等待独立方案。

---

## 一、后端编排与 Agent 层

### 🔴 高严重度

#### 1-H1. 同步工具执行直接阻塞事件循环

- **位置**：`backend/app/orchestration/tool_runtime.py:977-983`、`backend/app/orchestration/resource_state.py:112-117`
- **问题**：非并行批次（`threaded=False`）且非 browser 工具时，`tool.execute()` 在事件循环线程内同步执行。文件复制/移动、Excel 操作、网络搜索等工具都是同步 IO/CPU 密集，单步执行期间整个 FastAPI 服务（SSE 推送、审批 API、其他任务）全部冻结。同函数上方 `capture_tool_resource_state()` → `sha256_file()` 会同步全量读取并哈希文件，大文件时秒级阻塞。
- **修复**：默认 `await asyncio.to_thread(tool.execute, args, context)`，只对显式声明 `async_safe=True` 的轻量工具走同步快路径；`sha256_file`、`validate_write_preconditions` 一并放入 `to_thread`。若工具非线程安全，配合 `asyncio.Semaphore` 或单工具 `concurrency_key` 锁限流。

#### 1-H2. ONNX 本地推理在 async 中同步执行整个生成循环

- **位置**：`backend/app/llm/onnx_provider.py:149-181`
- **问题**：`OnnxProvider.chat()` 是 `async def` 但直接调用 `_generate_text`，本地 NPU/CPU 推理（可能数十秒）期间持有 `threading.RLock` 并完全阻塞事件循环——隐私模式下后端等于单线程串行。模型加载 `_ensure_genai_model()`（冷启动可达分钟级）同样阻塞。
- **修复**：`return await asyncio.to_thread(self._generate_text, prompt, temperature=...)`；模型加载也走 `to_thread`，外层用 `asyncio.Lock` 防止重复加载风暴。

#### 1-H3. `init_db()` 无幂等守卫 + `get_effective_settings()` 每次全量重建

- **位置**：`backend/app/orchestration/agent_bus.py:25-29`、`backend/app/llm/registry.py:24-28`
- **问题**：
  1. `db.init_db()` 每次执行完整 `executescript`（30+ 张表的 CREATE TABLE/INDEX），无任何 `_initialized` 标志。`AgentBus.publish` 每条消息触发一次——一个步骤发布 6-10 条消息，每条都跑全量建表脚本。
  2. `get_effective_settings()` 每次都 `init_db + 读 settings 表`，被 `_tool_context()`、provider 构建、planner、`OSExecutionEngine.__init__` 等每步多次调用。
- **修复**：`init_db` 加模块级守卫（按 `db_path()` 缓存已初始化集合）；`get_base_settings` 加 `functools.lru_cache`；settings 加 `cachetools.TTLCache`（ttl≈2s）+ 写入端主动失效；热路径 DB 调用统一包 `asyncio.to_thread`。

#### 1-H4. `asyncio.gather` 结果处理漏掉 `CancelledError` → 取消时崩溃

- **位置**：`backend/app/orchestration/handlers/step_scheduler_handler.py:106-117`
- **问题**：`gather(return_exceptions=True)` 会把 `CancelledError`（BaseException 子类）作为结果返回，`isinstance(outcome, Exception)` 匹配不到，下一行 `outcome.kind` 直接抛 `AttributeError`，把取消变成崩溃。另外 `zip(done, outcomes)` 依赖对同一 set 两次迭代顺序一致——CPython 实现细节，脆弱。
- **修复**：改为 `isinstance(outcome, BaseException)`，并对 `CancelledError` 显式 re-raise 传播取消；把 `done` 先固化为 `list` 再 `gather`/`zip`。

#### 1-H5. `cancel_run` 不取消在途 asyncio 任务

- **位置**：`backend/app/orchestration/os_execution_engine.py:108-120`
- **问题**：取消只更新 DB/Store 状态；`asyncio.create_task(... name=f"os-step-{step.id}")` 创建的任务没有被记录与取消，正在跑的工具（甚至写操作）会继续执行到完成。
- **修复**：维护 `run_id -> set[asyncio.Task]` 映射，`cancel_run` 时 `task.cancel()` 并 `await asyncio.gather(..., return_exceptions=True)` 收尾；每回合开头检查取消标志。

#### 1-H6. 上下文投影每次 LLM 调用做 3+ 次全量 deepcopy 与 4+ 次 token 重计

- **位置**：`backend/app/context_management.py:227-257`
- **问题**：`project_messages_for_llm` 整体 deepcopy 一次，`_micro_compact_messages_with_metadata` 内部又 deepcopy，多个压缩函数逐条 deepcopy；`count_messages_tokens` 同一次投影至少调用 4 次。长对话下每次 LLM 调用前的纯 Python 开销可达数百毫秒，且都在事件循环线程。
- **修复**：写时复制（未修改的消息直接引用，仅对被改写的 dict 浅拷贝）；token 计数按消息 id/content hash 做 memo 缓存；`repair_tool_message_invariants` 仅在检测到孤儿 tool 消息时才复制。

#### 1-H7. CJK token 估算失真 3-5 倍

- **位置**：`backend/app/context_management.py:26`（`CHARS_PER_TOKEN = 4`）
- **问题**：中文约 1-1.5 字符/token，`len/4` 会把中文 token 数低估 3-5 倍。后果：`auto_compact_threshold` 永远触发不了，全靠 `PromptTooLongError` 反应式重试兜底（每次浪费一轮完整 API 调用），前端剩余上下文百分比也是错的。对中文优先产品是正确性问题。
- **修复**：按字符类别分段估算（CJK 计 ~1.6 字符/token，ASCII 计 ~4），或接入 `tiktoken`（cloud 模式）/本地 tokenizer（onnx 模式已有 `state.tokenizer`），结果按消息缓存。

#### 1-H8. 监督机制 N+1 式 DB 轮询

- **位置**：`backend/app/agents/orchestrator_agent.py:364-384`
- **问题**：每个步骤在 5-8 个 stage 各调用一次 `_supervise_new_agent_messages`，每次都是 SQLite 查询 + 全量 Pydantic 校验 + Python 排序，首次还会拉全部消息 bootstrap。
- **修复**：改推送驱动——`AgentBus.publish` 时把消息追加到进程内 per-task ring buffer，监督只消费内存队列，DB 仅作持久化；或至少跳过 Pydantic 全量校验（用 `model_construct`）。

### 🟡 中严重度

| # | 问题 | 位置 | 修复方法 |
|---|---|---|---|
| 1-M1 | 跨任务字典只增不减（`_retry_counts`、`_supervised`、`_orchestrators_by_run`、`_TASK_READ_STATES`、`_SHARED_PATH_LOCKS`）→ 长驻进程内存泄漏 | 多处 | 任务终态统一清理，或换 `cachetools.TTLCache/LRUCache` |
| 1-M2 | 每个 run 重建完整 OrchestratorAgent + 全量工具重注册 | `orchestrator_agent.py:79-88` | 工具 registry 进程级单例（settings 变更时重建）；Orchestrator 无状态化或对象池 |
| 1-M3 | `httpx.AsyncClient` 每请求新建（每次 LLM 调用重做 TCP+TLS 握手）；重试逻辑手写 150 行 | `openai_compatible.py:132-137` | 模块级共享 `AsyncClient` + `httpx.Limits`；重试换 `tenacity.AsyncRetrying`（保留熔断器） |
| 1-M4 | 基于 `TypeError` 字符串的签名兼容回退链（4 层嵌套 try/except），会把真实 bug 误判为旧版签名并静默吞掉 | `planning_handler.py:118-169` | 启动时 `inspect.signature` 一次性探测并 `lru_cache`；或直接删兼容层 |
| 1-M5 | PlannerAgent 的 Mock 回退逻辑三处复制粘贴；payload 手写逐字段转换 | `planner_agent.py:181-245` | 提取 `_structured_with_fallback` helper；用 pydantic `PlanPayload` 模型替代手写转换 |
| 1-M6 | `ContextAwareProvider.chat_result` 与 `structured_chat` 各 ~80 行重试代码完全重复 | `context_management.py:1056-1217` | 提取 `_call_with_reactive_compaction(call, projection)` 泛型 helper |
| 1-M7 | 并行批次的失败恢复串行化，抵消并行收益 | `os_execution_engine.py:499-517` | 失败步骤的 recovery 收集为 coroutine 后 `asyncio.gather` |
| 1-M8 | 每回合全量 `model_copy(deep=True)`（10+ 处）+ 全 plan 序列化 + 历史观测全量重 validate，O(turns × steps) | `os_execution_engine.py:863-949` | observations 在 run 生命周期内存缓存（增量 append）；deep=False；plan snapshot 仅在变更时重建 |
| 1-M9 | 生命周期 hook 对 context（含 settings/registry/runtime 大对象）递归 deepcopy | `tool_runtime.py:562-570` | hook 只暴露浅层 `MappingProxyType` 只读视图，排除已知大对象 |
| 1-M10 | `_accepted_review_tool_call_keywords` 每个工具调用跑一次 `inspect.signature` | `tool_runtime.py:275-286` | `functools.lru_cache(maxsize=64)` |
| 1-M11 | 5 处静默吞错无日志（`os_reflection.py:191/277`、`supervisor_agent.py:131`、`context_management.py:1625`、`os_execution_engine.py:870`） | 多处 | 统一 `logger.warning(exc_info=True)` 或复用已有 `record()` 审计 |
| 1-M12 | AgentBus 用类属性做全局可变状态（隐式单例，测试易串扰） | `agent_bus.py:20-23` | 显式 `get_agent_bus()` 单例工厂 + 实例属性 |
| 1-M13 | 超大文件：`context_management.py` 1760 行、`tool_runtime.py` 1053 行、`os_execution_engine.py` 1047 行；Orchestrator 与引擎双向依赖 | 多处 | context_management 拆 token_budget/projection/compaction/provider 四模块；step graph 提为纯函数；定义 `OrchestratorServices` Protocol 打断循环引用 |
| 1-M14 | 双调度路径并存：`StepSchedulerHandler.process_steps` 与 `OSExecutionEngine.run_plan_turn` 两套几乎等价逻辑 | 两文件 | 确认前者是否死代码，删除或标注 deprecated |
| 1-M15 | `execute_approved_step`/`execute_step` 巨型多分支函数（135 行、9 个早退分支） | `step_execution_handler.py:74-356` | 拆小函数或显式状态机 |

### 🟢 低严重度（摘要）

- `_execute_tool_under_locks` 递归获取锁 → `contextlib.AsyncExitStack` 平铺（`tool_runtime.py:949`）
- onnx 的 JSON 裸截取无 schema 校验 → 与 `openai_compatible._parse_structured_json` 收敛到共享 util（`onnx_provider.py:134`）
- `registry.py` 110 行手写 env→field 映射 → 迁 `pydantic-settings`（`llm/registry.py:31-146`）
- 三处独立维护的中英文意图关键词正则（planner/supervisor/engine_router）→ 统一 `intent_rules.py` 数据驱动
- `load_prompt` OSError 静默返回空字符串 → 加 warning 日志（`prompts/__init__.py:41`）
- `_phase_for_task_plan` 解析人类可读 summary 字符串推断相位（中文 summary 匹配不到）→ 终态原因改结构化 enum（`os_execution_engine.py:985`）
- `turns_remaining = (len(steps)+1)*4+32` 魔法数；`background_tasks.py` 双线程写 status 无锁；`allowed_tools` 每次全量遍历 registry

### ✅ 亮点（保持现状）

- 写锁按 `sorted(keys)` 顺序获取，正确避免死锁
- `claim_approval_for_execution` 用 `BEGIN IMMEDIATE` + 条件 UPDATE 原子消费审批
- OpenAI tool-call 配对不变量修复（`repair_tool_message_invariants`）细致
- 熔断器 + `Retry-After` 解析 + prompt-too-long 与熔断解耦
- per-run/per-step 反思上限防止无限反思循环

---

## 二、数据层、服务层与 API

### 🔴 高严重度

#### 2-H1. 每操作新建 SQLite 连接，无 WAL/busy_timeout

- **位置**：`backend/app/core/db.py:111-120`（全仓库唯一连接入口，已核实无 `journal_mode=WAL`、`busy_timeout`、`synchronous` 配置）
- **问题**：每次 fetch/upsert 都 `sqlite3.connect()`；默认 DELETE journal 模式下写者阻塞读者，后台线程（run_service、scheduler、file watcher）与请求并发写极易 `database is locked`。
- **修复**：`PRAGMA journal_mode=WAL; synchronous=NORMAL; busy_timeout=5000; cache_size=-32000`；`threading.local` 长连接或写操作集中单写者队列；更彻底迁 `aiosqlite`。

#### 2-H2. 审计链每条事件 = 新连接 + `BEGIN IMMEDIATE` + 磁盘重读 secret + 查链头

- **位置**：`db.py:1456-1474`（链头查询）、`db.py:1486-1496`（每条事件重读 HMAC secret 文件）、`db.py:1039-1051`（run_event 每条 `SELECT MAX(sequence)`）
- **修复**：`_audit_hmac_secret` 加 `lru_cache`；进程内缓存 `(last_sequence, last_hash)`（单写者前提下安全，插入失败回退查询）；run_event sequence 在内存按 run_id 维护计数器。

#### 2-H3. 向量检索纯 Python 暴力扫描 + JSON TEXT 存 embedding

- **位置**：`backend/app/indexer/vector_index.py:59-66, 297-305`、`fts_index.py:185`
- **修复**：embedding 改 `float32 BLOB`（`np.asarray(vec, dtype=np.float32).tobytes()` / `np.frombuffer`）；批量 numpy 矩阵点积（已 L2 归一化时余弦=点积）；规模上来后引入 `sqlite-vec` 扩展或 `hnswlib` 内存索引。

#### 2-H4. OCR 每张图重建推理会话

- **位置**：`backend/app/indexer/ocr_service.py:442`（每图 `create_inference_session`）、`:379-381`（每图重新实例化 `PaddleOCR`，秒级模型加载）
- **修复**：参照 `local_embedding_provider.py:145-163` 的 `_CACHED_PROVIDER` 模式，按 backend cache_key 缓存 session（注意加锁）。

#### 2-H5. 关键查询列缺索引

- **位置**：`db.py:131-423` schema。缺失：`tool_calls(task_id)`、`tool_results(tool_call_id)`、`plans(task_id)`、`approvals(task_id, status)`、`document_chunks(file_id)`、`chat_messages(created_at)`、`memories(created_at)`
- **修复**：`init_db` 补 `CREATE INDEX IF NOT EXISTS`。

#### 2-H6. `GET timeline` 触发 `reconcile_task_runs` N+1 风暴

- **位置**：`backend/app/services/run_service.py:118-124, 299-332, 481-488`
- **问题**：每次读 timeline 对该 task 的每个 run（上限 100）重读 1000 条 agent_messages + 重放 5000 条 run_events。
- **修复**：seen-ids 改 SQL `json_extract`；消息查一次按 run 复用；reconcile 移出读路径（移到写侧或 `updated_at` 水位线增量）。

#### 2-H7. FTS5 对中文无效

- **位置**：`db.py:426`（默认 `unicode61` tokenizer）、`vector_index.py:125-135, 290-294`
- **问题**：中文不分词，MATCH 几乎必失败，静默退化到 `LIKE '%q%'` 全表扫描。
- **修复**：建表改 `tokenize='trigram'`（SQLite ≥ 3.34）或写入/查询前 `jieba` 预分词；LIKE fallback 加告警日志。

### 🟡 中严重度

| # | 问题 | 位置 | 修复方法 |
|---|---|---|---|
| 2-M1 | 无版本化迁移机制（仅 `CREATE TABLE IF NOT EXISTS` + ad-hoc 加列） | `db.py:424-477` | `PRAGMA user_version` 顺序迁移列表，或 Alembic |
| 2-M2 | API 普遍缺分页与 `response_model`；`/tasks` 每任务聚合 3 张表；`POST /settings` 入参裸 dict | `routes_tasks.py:285`、`routes_audit.py:12`、`routes_settings.py:56` | keyset 分页（`created_at,id`）+ Pydantic 模型 + OpenAPI 契约 |
| 2-M3 | 内存缓存/集合无上限：`task_pool._completed`、`scheduler._fired_ids`；`create_task` 未存引用可能被 GC | `task_pool.py:19`、`scheduler_service.py:64,169` | 上限淘汰；`self._inflight.add(task)` + done_callback |
| 2-M4 | FTS 重建期全量 DELETE（搜索不可用）+ 逐行 INSERT 无批量 | `fts_index.py:105-235` | `executemany` 批量；影子表 + RENAME；rebuild 放后台并暴露进度 |
| 2-M5 | `_monitor_task_to_terminal` 100ms 间隔同步轮询 600 次 | `run_service.py:395-424` | 改事件驱动（订阅 AgentBus） |
| 2-M6 | 诊断/审计校验全量载入内存：`verify_audit_log(limit=None)` fetchall 整表 | `db.py:1095-1259`、`routes_system.py:276` | SQL 聚合（GROUP BY/json_extract）；流式校验 + 已验证水位线缓存 |
| 2-M7 | 移动配对过期清理逐行回写（500 条逐条 UPDATE） | `mobile_pairing_service.py:481-488` | 单条 `UPDATE ... WHERE status='pending' AND expires_at <= ?` |
| 2-M8 | 远程桌面 WS：10ms busy-poll 控制消息 + JPEG→base64→JSON（体积 +33%） | `routes_remote.py:117-157` | 独立 reader task + `asyncio.Queue`；`send_bytes` 二进制帧 |
| 2-M9 | `embed_texts_sync` 每次新建 ThreadPoolExecutor + 新事件循环 | `embedding_service.py:46-53` | 模块级共享 executor；或提供纯同步 embed 入口 |
| 2-M10 | 单例风格混乱 + 十余处函数内 import 规避循环依赖 + API 层互相 import 私有函数 | `run_service.py:37`、`routes_mobile.py:30` 等 | lifespan 创建实例 + `app.state`/`Depends` 注入；审批执行下沉 service 层 |

### 🟢 低严重度（摘要）

- `upsert_model` 700 行 if/elif 巨型分发 → `TABLE_WRITERS` 注册表字典（`db.py:480-702`）
- `duplicates()` 全表载入 Python 分组 → `GROUP BY sha256 HAVING COUNT(*)>1`（`fts_index.py:445`）
- `asyncio.get_event_loop()` 已弃用 → `get_running_loop()`（`task_service.py:145`）
- `init_db()` 在 `create_app` 与 lifespan 各调一次；lifespan 子系统启动无异常保护（`main.py:73,110`）
- mobile JWT 中间件与依赖各 decode 一次 → 写入 `request.state.claims` 复用（`main.py:144`）
- `_lan_ip()` 每次配对向 8.8.8.8 发 UDP → 缓存 30-60s（`mobile_pairing_service.py:1163`）
- croniter 缺失时 `_next_run` 返回"现在"导致每 tick 重复触发（`scheduler_service.py:25`）
- timeline/replay/progress 同请求重复拉同一批 agent_messages（`routes_tasks.py:190,1045`）
- `db_path()` 每次调用 `mkdir`（`db.py:91`）

---

## 三、工具与安全策略层

### 🔴 高严重度

#### 3-H1. vision 工具路径授权绕过（已核实确认）

- **位置**：`backend/app/tools/vision_tools.py:116-127`

```python
try:
    path = resolve_authorized(raw, allowed)
except Exception:
    path = Path(raw)   # 授权失败时直接使用原始路径！
```

- **问题**：`resolve_authorized` 抛出 `SecurityError`（路径在授权目录之外）时被 `except Exception` 吞掉并直接使用原始路径。`describe_image`、`ocr_image`、`embed_image`、`compare_images` 全部经过该函数——vision 类工具完全绕过路径沙盒，可读取磁盘上任意图片；`_resolve_image_batch`（130-148 行）还会对目录 `rglob("*")` 递归展开，放大成全盘图片枚举。
- **修复**：删除回退分支，授权失败 `return None` 或向上抛 `SecurityError`；确需兼容内部调用时用显式 `allow_unauthorized=True` 参数，默认拒绝。

#### 3-H2. PolicyEngine 子串匹配做风险分类，未知工具默认宽松（fail-open）

- **位置**：`backend/app/policy/policy_engine.py`（工具名包含 `"password"/"cookie"/"token"/"shell"` 等关键词即归类，未匹配落入最宽松级别）
- **修复**：风险声明前移到工具注册——`ToolSpec` 增加必填 `risk_level: RiskLevel`，注册缺失即抛错（fail-closed）；PolicyEngine 只消费声明值，关键词匹配降级为审计期一致性告警。

#### 3-H3. 浏览器工具 URL 校验不拦截私网/环回地址（SSRF）

- **位置**：`backend/app/services/browser_activity_runtime.py` 的 `_validate_url`（仅检查 http/https scheme 与 netloc 非空）
- **问题**：prompt 注入可指挥 agent 访问 `http://127.0.0.1:8000/api/...`（后端自身 API）、路由器管理页、内网服务。
- **修复**：解析 host 后用 `ipaddress` 拦截 `is_private/is_loopback/is_link_local/is_reserved`（域名先 `getaddrinfo` 解析再校验）；提供显式白名单配置供局域网场景开启。

### 🟡 中严重度

| # | 问题 | 位置 | 修复方法 |
|---|---|---|---|
| 3-M1 | Playwright 每次调用冷启动完整 Chromium（1-3s），且动作间无状态——多步写流程（navigate→click→fill）每步都在全新浏览器中执行 | `browser_activity_runtime.py:113-212` | per-task/session 持久化 `BrowserContext`（懒启动 + 空闲回收 + lifespan 兜底关闭）；迁 `async_playwright` |
| 3-M2 | `evaluate_permission_policy` 无规则匹配时默认放行（fail-open，与 3-H2 叠加） | `policy/permissions.py` | 默认 deny 或降级"需审批"；结果加 `matched_rule` 进审计 |
| 3-M3 | `_is_test_environment` 把 `APP_ENV/LENGRVIS_ENV` 的 `"1"/"true"/"yes"/"on"` 也当测试环境，配合 `LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL` 可关 token 校验 | `security/desktop_api.py` | 仅精确匹配 `{"test","testing"}`；启用时写醒目审计事件 |
| 3-M4 | vision/Excel/browser 工具 `input_schema` 空缺，统一参数校验形同虚设 | 各工具注册处 | 按 `tools/schemas.py` 模式补全 JSON Schema |
| 3-M5 | Excel COM 缺线程级 `pythoncom.CoInitialize`；`_quit_excel` 失败仅 debug 日志（EXCEL.EXE 僵尸无人发现） | `app_excel.py:127-130, 295-299` | 操作入口 CoInitialize/CoUninitialize 配对；Quit 失败升级 warning + 记录 PID 可选 taskkill 兜底。（现有 try/finally、DisplayAlerts=False、AutomationSecurity=3 禁宏均正确） |
| 3-M6 | mobile JWT 未强制要求 `exp` 声明（PyJWT 默认只在 exp 存在时才校验过期） | `security/mobile_jwt.py` | `jwt.decode(..., options={"require": ["exp","aud","iss"]})`。算法固定 HS256、aud/iss 校验、secrets 生成密钥均已正确 |
| 3-M7 | local_secret 回退路径先写明文再 chmod（窗口期），且 Windows 上 chmod 无效 | `security/local_secret.py` | `os.open(path, O_CREAT\|O_WRONLY\|O_EXCL, 0o600)` 原子创建；Windows 配 `icacls` 收紧 ACL。（DPAPI 加密主路径是亮点） |

### 🟢 低严重度

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| 3-L1 | `core/paths.py:63-68` | 符号链接检查只针对叶子节点；ADS（`file.txt:stream`）与 8.3 短文件名未显式处理 | 写入类操作 `resolve(strict=True)` 验证父目录；文件名含 `:` 拒绝；`GetLongPathNameW` 归一短名 |
| 3-L2 | `policy/redaction.py` | 主 pattern 已预编译（初步"重复编译"指控不成立）；真实问题是每段文本顺序跑 ~10 个正则 | 合并 alternation 大正则 + 命名分组；或关键词粗筛再跑正则 |
| 3-L3 | `vision_tools.py:159` 等 | vision 失败统一变字符串塞进结果，无法区分配置缺失/网络失败 | 收窄异常类型 + `error_kind` 结构化字段 |
| 3-L4 | `browser_tools.py:117-118` | `use_system_browser=True` 时 `webbrowser.open(url)` 不经 SSRF 校验 | 与 3-H3 共用同一校验入口 |

### ✅ 核实后撤销/确认良好的项

- **"审批 HMAC 用普通等号比较"不成立**：`routes_browser.py`、`routes_ui_automation.py`、`desktop_api.py`、`db.py` 链校验、`rollback_tools.py` 均使用 `hmac.compare_digest`（已全局 grep 复核）
- **skills/sandbox.py 与 developer_tools.py 的 subprocess 用法良好**：`shell=False`、显式 timeout、净化 env、`CREATE_NO_WINDOW`、本地 skill 执行默认禁用
- **rollback_tools 审批链严谨**：approved 标志 + 存在性 + 绑定校验 + 原子消费 + 二次绑定复核，教科书式实现
- **mobile_jwt 主体实现正确**（仅差 3-M6 的 require exp）

---

## 四、桌面端 Electron + React

### 🔴 高严重度

#### 4-H1. Windows 下后端子进程不杀进程树（孤儿进程）

- **位置**：`desktop/src/main/main.ts:363-370`、`desktop/src/main/backendProcess.ts:216-228`
- **问题**：① `app.on("before-quit", async ...)`——Electron 不等待异步监听器，`await` 之后的清理无保障；② `child.kill()` 在 Windows 上是 `TerminateProcess`，不杀进程树——PyInstaller onefile 引导进程的子进程会变孤儿，继续占用 8000 端口和显存；③ `stop()` 不等待 exit 事件、无超时强杀兜底；主进程崩溃时子进程也无人收尾。
- **修复**：`before-quit` 改 `event.preventDefault()` → 清理 → `app.exit()`；Windows 用 `execFile("taskkill", ["/PID", pid, "/T", "/F"])` 杀树（可先优雅 POST shutdown 再强杀）；`stop()` 中 `await once(child, "exit")` 配 3-5s 超时；后端加 `--parent-pid` watchdog 覆盖主进程崩溃场景。

#### 4-H2. App.tsx 1781 行单体根组件订阅全部 store

- **位置**：`desktop/src/renderer/App.tsx:156-1289`（约 50 个 selector 集中在 158-213 行）
- **问题**：任务运行时每条 WS 事件 + 轮询都更新 `tasks/agentConversations/messages`，导致整个 App（含全部面板）重渲染；子组件无 `React.memo`，且 props 全是内联箭头函数（即使加 memo 也失效）。
- **修复**：视图拆为自取数据的容器组件（直接 `useLengrvisStore(selector)`）；业务动作移为 store action 或独立 service 模块（zustand 的 `set/get` 在组件外可用）；`useShallow` + `memo` + `useCallback`。

#### 4-H3. 双通道驱动的高频全量轮询 ✅ 已解决

- **位置**：`App.tsx` polling / `apiClient.ts` / `ipc.ts` `abortGroup`
- **问题**：长任务期间高频全量轮询触发全树重渲染；慢响应可能乱序覆盖新数据。
- **修复**：WS 增量 merge 为主数据源；轮询降为 WS 连通 30s / 断开 10s 兜底；`beginBatch` + `abortGroup` 贯通 IPC 取消在途 fetch；renderer `AbortController` 阻止过期 batch 写回 state。

#### 4-H4. `refreshWorkspace` 依赖循环：mode 变化重拉 16 个接口

- **位置**：`App.tsx:325-448`（`useCallback` 依赖 `[activeBrowserSessionId, api, mode]` + `useEffect(..., [refreshWorkspace])`）
- **修复**：`mode`、`activeBrowserSessionId` 改 `useRef`/`getState()` 即时读取，让回调身份稳定；启动加载改 `useEffect(..., [])` 一次性触发。

#### 4-H5. desktop API token 明文落盘，Windows 上 chmod 无效

- **位置**：`desktop/src/main/desktopApiToken.ts:99-133`
- **问题**：同机任意进程可读 `desktop_api.secret` 后调用全部受保护后端 API（含文件清理、命令执行）。
- **修复**：Electron `safeStorage.encryptString()`（Windows 走 DPAPI）；或 `icacls` 收紧 ACL 并移出项目目录；确保 `.lengrvis_data/*.secret` 在 `.gitignore`。

### 🟡 中严重度

| # | 问题 | 位置 | 修复方法 |
|---|---|---|---|
| 4-M1 | WS 重连固定 2.5s 永久重试无退避；`reconnecting` 状态不断往聊天流塞消息 | `apiClient.ts:95, 1890-2009, 2103` | 指数退避 + 抖动 `min(2500·2^n, 60s)·(0.5+rand/2)`；`error` 封顶后转手动重试按钮 |
| 4-M2 | `apiClient.ts` 6070 行单文件（传输层 + 重连 + 几十个手写 Backend 类型）；与 `shared/types.ts`（1709 行）大量语义重复 | 全文件 | 按域拆 `api/chat.ts`、`api/runs.ts` 等 + 独立 `transport.ts`；用 `openapi-typescript` 从 FastAPI `/openapi.json` 生成 wire 类型 |
| 4-M3 | 聊天/时间线大列表无虚拟化；messages 无上限；`appendUniqueMessage` O(n) 全量去重 | `ChatPanel.tsx:99`、`TaskTimeline.tsx:158`、`App.tsx:1767` | `@tanstack/react-virtual` 虚拟化；保留最近 500 条 + 分页拉取；`Set` 去重 |
| 4-M4 | 每次发消息/进前台串行跑 5 个 `sc.exe` 服务探测（最坏 ~7.5s 延迟） | `backendProcess.ts:134-154, 378-413`、`ipc.ts:585` | 探测结果 30s TTL 缓存；`Promise.all` 并行；健康检查通过即跳过 |
| 4-M5 | 收进托盘即 `browserHost.destroy()`——agent 浏览器会话被销毁，任务可能中断 | `main.ts:139-155` | 托盘隐藏只 `hide`，`destroy` 留给真正退出；或仅在无活跃 run 时销毁 |
| 4-M6 | 超大组件：SettingsPanel 3362 行、OfficeScene 2090 行、FileSearchPanel 1945 行；styles.css 10323 行单文件 | 各文件 | 按 Tab/区块拆子组件 + memo；CSS Modules 随 chunk 分割 |
| 4-M7 | `verifyUpdateCodeSignature: false` + autoDownload——发布渠道被攻破即静默换包（已知临时措施） | `electron-builder.yml:41` | 签名落地后恢复 true + `publisherName`；CI 对 `backend.exe` 单独 signtool 签名 |
| 4-M8 | 手写裸 TCP WebSocket 客户端：不校验 `Sec-WebSocket-Accept`、不回 Ping、不处理分片帧 | `notifications.ts:245-423` | 删除 fallback 直接用 Node 22 原生 `WebSocket`；或换 `ws` 包 |
| 4-M9 | 通知重连固定 5s 无退避；socket 路径探测索引只进不退 | `notifications.ts:14, 139-147` | 指数退避；成功后重置 `socketPathIndex` |

### 🟢 低严重度（摘要）

- CSP 可收紧：补 `object-src 'none'; base-uri 'none'`，img/connect-src 收敛固定端口（`index.html:5-8`）
- dev 模式 `will-navigate` 用全等比较 dev server URL → 改 origin 比较（`main.ts:80-91`）
- preload 版本号打包后失真（`npm_package_version` 不存在）→ IPC 提供 `app.getVersion()`（`preload.ts:174`）
- `ChatPanel` 的 `initialDraft` effect 会覆盖用户正在输入的草稿（`ChatPanel.tsx:40-42`）
- `isPlainRecord`/`isLoopbackHostname`/`parseResponseBody` 多处复制粘贴 → 沉到 `src/shared/`
- WS token 经 `Sec-WebSocket-Protocol` 头传输（本机抓包工具可见）→ 可改握手后首帧鉴权
- 托盘 60s 轮询与前台 UI 状态刷新重复；OfficeScene 动画 interval 依赖业务状态频繁重建
- 构建配置整体良好（manualChunks + 路由懒加载 + asar），可加 `rollup-plugin-visualizer` 监控体积回归

### ✅ 亮点（保持现状）

- Electron 安全三件套全开（contextIsolation/sandbox 开、nodeIntegration 关），嵌入页同样加固
- IPC 纵深防御：preload 侧原型污染/accessor/大小限制清洗 + 主进程二次校验 + 来源校验 + 敏感端点 deny-list 强制原生确认对话框
- 外链统一协议白名单；后端 URL 强制 loopback；日志全量脱敏
- TypeScript 纪律好，业务代码几乎零 `any`

---

## 五、测试与工程化体系

### 🔴 高严重度

| # | 问题 | 位置 | 修复方法 |
|---|---|---|---|
| 5-H1 | Python 侧零 lint/format/typecheck，`# noqa` 全是死注释 | 全仓库 | `ruff`（select `E,F,W,I,B,UP,BLE,S`——S 即 bandit 安全规则）+ `mypy`（`app.policy.*`、`app.security.*` 先行 strict）+ `pre-commit` |
| 5-H2 | `.gitignore` 只单列 `approval_hmac.secret`，缺 `*.secret` 通配；git status 与 ignore 规则存在矛盾，需核实 secret/db 是否曾被 track | `.gitignore` | `git ls-files \| grep -E "secret\|\.db$"` 核实；追加 `*.secret`、`*.key`、`.ruff_cache/` 等；若曾提交需 `git filter-repo` + 轮换密钥 |
| 5-H3 | `config.py` 700+ 行手写四级配置合并器：yaml key 跨段顺序查找会串味（key 不绑定段）；向上 5 层父目录自动搜 config.yaml（conftest 专门写 fixture 对抗它即是危险性证据） | `backend/app/config.py:114-141, 304-312` | 迁 `pydantic-settings`（嵌套模型绑定 yaml 段 + `env_nested_delimiter`）；搜索范围收紧到 cwd + 显式 env |

### 🟡 中严重度

| # | 问题 | 位置 | 修复方法 |
|---|---|---|---|
| 5-M1 | slow 测试用 nodeid 硬编码清单标记，重命名即静默失效 | `backend/tests/conftest.py:22-43` | 内联 `@pytest.mark.slow` + `--strict-markers` + `pytest-timeout` |
| 5-M2 | 契约测试 skip 模式可能掩盖回归（模块误删时整组静默变 skip） | `conftest.py:94-117` | CI 监控 skip 基线数，新增 skip 即失败；稳定模块改硬 import |
| 5-M3 | pytest 配置双份重复（根 `pytest.ini` 与 `backend/pyproject.toml`，已现差异） | 两文件 | 只保留一处 |
| 5-M4 | 140 个测试文件串行 + `--maxfail=1`（90 分钟预算）；CI 仅 windows-latest 单平台、仅 Python 3.12 | `.github/workflows/ci.yml:50` | `pytest-xdist -n auto`；matrix 加 macOS 与 3.11/3.12；`--maxfail=10`；`time.sleep` 改条件轮询 |
| 5-M5 | 无覆盖率门禁、无 hypothesis 属性测试 | `requirements-dev.txt` | `--cov-fail-under`（基线起步逐步上调）；给路径沙盒/PolicyEngine 写属性测试（"任意构造路径不能逃出 workspace root"） |
| 5-M6 | 依赖三处重复声明（requirements.txt / pyproject / 两份 requirements-dev），dev 依赖未锁定 | `backend/` | 统一迁 `uv`：pyproject 唯一来源 + `uv.lock` + CI `uv sync --locked`；180 行手写校验脚本换 `uv lock --check` |
| 5-M7 | hygiene 脚本阻断列表不含 `*.secret/*.db/__pycache__`，硬编码个人机器路径 | `scripts/check_repo_hygiene.ps1:20-33` | 扩充 `$blockedPathspecs` |
| 5-M8 | 无打包产物 CI 校验（capability manifest 设计很好但 CI 从不执行） | `.github/workflows/` | 加 tag 触发的 `package.yml`：构建 → 校验 manifest → upload-artifact |
| 5-M9 | `config.example.yaml` 与 AppSettings 无同步校验 | 两文件 | 加测试断言 example 所有叶子 key 可映射到已知字段（迁 pydantic 后 `extra="forbid"` 一行实现） |

### 🟢 低严重度（摘要）

- 前端缺 eslint/prettier（已有 tsc --noEmit）→ flat config + typescript-eslint 接入 CI
- 依赖安全审计仅周一 cron → 加 PR paths 触发 + Dependabot
- CI 缺 junitxml/coverage 工件上传 → `dorny/test-reporter`
- eval harness 分轨设计正确（契约测试不调真实 LLM），可补历史基线趋势对比

### ✅ 亮点（保持现状）

- `local_secret.py` DPAPI 加密 + 明文迁移设计
- `build_backend.py` capability manifest 设计
- `isolate_local_runtime_config` autouse fixture 防本地配置泄入测试
- uv 生成的依赖锁、PyInstaller 版本固定

---

## 六、优先级路线图

### 第一批（改动小、收益立竿见影，约 1-2 天）

1. **3-H1 vision 授权绕过**——删除回退分支即可关闭，建议立即处理
2. **2-H1/1-H3/2-H5**：`connect()` 加 WAL + busy_timeout；`init_db()` 幂等短路；补缺失索引
3. **1-H1/1-H2/2-H4**：同步工具执行、ONNX 推理、OCR 包 `asyncio.to_thread`；OCR session 缓存
4. **1-H4/1-H5**：`isinstance(..., BaseException)` 修取消崩溃 + `cancel_run` 真正取消在途任务
5. **3-H3 浏览器 SSRF**：`ipaddress` 校验约 20 行
6. **5-H2**：`.gitignore` 加 `*.secret` 通配并核实 git 历史；hygiene 脚本扩拦截
7. **4-H1**：Windows 后端进程树清理（`taskkill /T`）
8. **3-M6/3-M3**：JWT require exp、测试环境判定收紧——各一行级

### 第二批（中等工作量，约 1 周）

9. **1-H3/2-H2**：settings TTL 缓存、audit secret/链头缓存；httpx 共享客户端（1-M3）
10. **1-H7**：CJK token 估算修正（中文产品正确性问题）
11. **4-H3/4-H4**：轮询降级为 WS 兜底 + `refreshWorkspace` 依赖修复 + AbortController
12. **5-H1**：ruff + pre-commit + pytest-xdist 落地
13. **3-H2/3-M2**：工具注册声明 `risk_level` + 权限默认 deny（fail-closed 化）
14. ✅ **2-H6**：reconcile 移出 timeline 读路径（approval 写路径仍 reconcile；其他完成路径可能短暂陈旧，靠 WS 校准）

### 第三批（结构性重构，分项立项）

15. **2-H3/2-H7**：embedding 改 BLOB + numpy / sqlite-vec；FTS5 trigram 中文支持
16. **4-H2/4-M2/4-M3**：App.tsx store 化拆分 + 列表虚拟化 + apiClient 按域拆分 + openapi-typescript
17. **2-M1/2-M2**：`user_version` 迁移机制；API 分页 + response_model
18. **3-M1**：Playwright 持久会话（兼具性能与多步写操作正确性）
19. **5-H3**：config.py 迁 pydantic-settings
20. **1-M13**：context_management/tool_runtime 拆模块；**4-H5** token 用 safeStorage

---

## 附：审查方法说明

- 五个方向由独立深度审查完成，关键高危结论（SQLite 连接模式、HMAC 比较方式、vision 路径回退、Electron 安全配置）经过主审交叉验证
- "审批 HMAC 使用普通等号比较"的初步指控经全局复核**不成立**，已撤销
- 行号为审查时点的近似定位，代码变更后请以符号搜索为准
