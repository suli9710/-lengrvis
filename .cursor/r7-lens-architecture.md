# Mavris Round 7 架构审计报告(Architecture Lens)

**架构分:71/100** — agent_hint 管道已收敛到单一 allowlist 与归一化漏斗、路由规则集中可测,但 agents↔orchestration 双向依赖、core→orchestration 倒置、四处重复的关键词路由表和把人类可读 reason 字符串当机器契约用,是本轮最需要偿还的结构债。

---

## 发现清单

### A1【高】agents → orchestration 反向依赖,形成包级双向耦合
- **文件**: `backend/app/agents/delegation_metadata.py:6`
- **问题**: agents 层模块导入 orchestration 层的 `route_engine`;同时 `orchestration/handlers/planning_handler.py:8-13`、`os_execution_engine.py:1019` 又反向导入 agents 的 `delegation_metadata`/`worker_agents`。包级 agents↔orchestration 互相依赖(目前靠模块粒度恰好不成环 + 一处函数内延迟导入兜底)。
- **证据**:

```5:6:backend/app/agents/delegation_metadata.py
from app.agents.worker_agents import normalize_supervisor_agent_hint
from app.orchestration.engine_router import route_engine
```

- **建议**: `infer_supervisor_agent_hint` 依赖 `route_engine` 只为判断"系统诊断"一种情形;把该判断换成独立谓词函数(见 A3),或把 `delegation_metadata` 下沉到 core/policy 层,使依赖恢复单向 services → orchestration → agents。

### A2【高】core/schemas.py 依赖 orchestration 子模块,核心层倒置
- **文件**: `backend/app/core/schemas.py:10-12`
- **问题**: 最底层的 core schema 导入 `app.orchestration.execution_stage/step_phase/task_phase`。core 应是无依赖的最底层;现在 orchestration 既被 core 依赖又依赖 core(`os_execution_engine.py:12` 导入 core.schemas),整个分层靠"子模块碰巧不相互导入"维持。
- **证据**:

```10:12:backend/app/core/schemas.py
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.step_phase import StepPhase
from app.orchestration.task_phase import TaskPhase
```

- **建议**: 把 `ExecutionStage/StepPhase/TaskPhase` 这三个纯枚举移入 `app/core/`(它们本身无 orchestration 逻辑),orchestration 反向引用。

### A3【高】路由 `reason` 人类可读字符串被两处当作机器契约
- **文件**: `backend/app/agents/delegation_metadata.py:54-56`;`backend/app/services/task_service.py:70-71`
- **问题**: `route_engine` 返回的 `reason` 是展示用文案,但 `infer_supervisor_agent_hint` 和 `handle_chat` 都用 `"system diagnostics" in route.reason` 做分支判定。改一句文案就会静默改变 Chat 委派与 hint 推断两条路径的行为。
- **证据**:

```70:71:backend/app/services/task_service.py
    route = route_engine(message, "auto")
    if route.selected_engine == "os" and "system diagnostics" in route.reason:
```

- **建议**: 给 `EngineRouteDecision` 加结构化字段(如 `rule: Literal["explicit","write_intent","system_diagnostics",...]`),`reason` 只做展示。

### A4【中】关键词路由表四处重复,与单一 allowlist 原则相悖
- **文件**: `backend/app/agents/supervisor_agent.py:39-70`(DELEGATION_RULES)、`backend/app/agents/delegation_metadata.py:48-75`(infer)、`backend/app/llm/mock_provider.py:236-271`(_supervisor_decision)、`backend/app/services/task_service.py:22-40`(FILE_ACTION_TERMS 覆盖)
- **问题**: Agent **名字**的事实来源已收敛到 `KNOWN_SUPERVISOR_WORKER_AGENTS`(好),但"哪些关键词路由到哪个 Agent"的规则在 Supervisor 启发式、infer 兜底、MockProvider、task_service 文件路径覆盖共四份,关键词集合相互不一致(如 SearchAgent 排除词、删除词表),漂移风险高。
- **建议**: 抽一份共享的 `DELEGATION_KEYWORD_RULES` 数据表放在 `worker_agents.py`/`delegation_metadata.py`,四个消费方引用同一数据。

### A5【中】Windows 路径正则/提取逻辑重复 4 份
- **文件**: `backend/app/agents/supervisor_agent.py:111`、`backend/app/agents/planner_agent.py:694-720`、`backend/app/llm/mock_provider.py:121-128`、`backend/app/services/task_service.py:21`
- **问题**: 同一个 `[A-Za-z]:[\\/][^...]+` 路径正则与"清洗候选路径"逻辑各写一份,planner 版还多了存在性探测和后缀剥离;四份语义近似但行为不等价。
- **建议**: 提取 `app/core/windows_path.py`(或 policy 层)统一 `extract_windows_path()`。

### A6【中】run→engine 绑定只存内存,跨重启 cancel 静默失效
- **文件**: `backend/app/orchestration/engine_router.py:216-222`;`backend/app/services/run_service.py:336-340`
- **问题**: `EngineRouter._run_engines` 是路由器实例内存字典;`cancel_run` 在路由器丢失后回退到**新建**的双引擎路由器,`_engine_for_run` 必然 `KeyError`,被 except 吞掉只留 warning——重启后取消 OS run 实际不会调用引擎的 cancel。`Run.engine` 明明已持久化在 DB,信息重复且没被利用。
- **证据**:

```216:222:backend/app/orchestration/engine_router.py
    def _engine_for_run(self, run_id: str) -> EngineName:
        engine = self._run_engines.get(run_id)
        if engine is not None:
            return engine
        if len(self.engines) == 1:
            return next(iter(self.engines))
        raise KeyError(f"Run has no registered engine in this router: {run_id}")
```

- **建议**: `EngineRouter.cancel_run/resume_run` 增加可选 `engine` 参数,run_service 用持久化的 `run.engine` 直接定位引擎。

### A7【中】双 `RunPhase` 枚举,服务层持续手工互转
- **文件**: `backend/app/core/schemas.py:142-159` 与 `app/orchestration/execution_models.RunPhase`;转换散布在 `backend/app/services/run_service.py:32, 703, 730, 747, 795, 813, 935`
- **问题**: 两套同名同值枚举靠 `RunPhase(state.phase.value)` 字符串互转,`TERMINAL_RUN_PHASES`/`TERMINAL_PHASES`/`ENGINE_TERMINAL_PHASES` 三个终态集合并存,任何一侧加值都可能 ValueError 或漏判。
- **建议**: 统一为一个枚举(core 定义,execution_models 复用),终态集合只留一份。

### A8【中】MockProvider 与 Planner 提示词格式硬耦合,测试语义渗入生产路径
- **文件**: `backend/app/llm/mock_provider.py:278-282`(解析 `"Supervisor routing hint:"`)、`42-46`(解析 `"User goal:"`);`backend/app/agents/planner_agent.py:14, 240, 267`
- **问题**: 注入边界本身是干净的(`allow_mock_fallback` 默认 False,registry 集中分发),但 MockProvider 通过**反解析生产提示词文本**来还原 hint/goal,与 `planner_user.md`、`format_supervisor_hint_block` 的文案强耦合——改提示词模板会静默破坏 mock 行为;且 PlannerAgent 在生产模块内直接 `MockProvider().structured_chat(...)` 兜底,fallback 决策(privacy/allow_mock 两轴 × 两种异常)重复写了两遍(`planner_agent.py:204-240` 与 `244-268`)。另有 `document_service.py:53-54` 用 `isinstance(provider, MockProvider)` 在生产分支上探测 mock。
- **建议**: hint 通过结构化参数传给 provider(或 MockProvider 改为读取 messages 元数据);fallback 决策抽成一个 `resolve_planner_fallback(settings, exc)` 单函数;mock 探测统一用 `provider.name == "mock"`。

### A9【中】planning_handler 用 TypeError 字符串嗅探做"同仓库内"签名兼容
- **文件**: `backend/app/orchestration/handlers/planning_handler.py:177-235`
- **问题**: `_create_plan_legacy_fallback` 靠 `"tool_specs" in str(exc)` 等字符串匹配做三层降级重试。Planner 与 handler 同仓库同版本,签名是已知的;这 59 行只为兼容测试中的旧签名 stub,属于测试脚手架渗入生产。同类 smell:`planner_agent.py:276-283` 用 `inspect.signature(get_provider)` 探测形参。
- **建议**: 删除降级链,测试 stub 对齐当前签名;`get_provider` 的调用约定固定下来。

### A10【中】run_service.py 上帝模块(约 1036 行)
- **文件**: `backend/app/services/run_service.py:1-1036`
- **问题**: 单文件承担 run CRUD、引擎循环驱动、bus→run 事件桥接、task↔run 相位 reconcile、审批过期、关停/恢复生命周期、后台调度 7 类职责;模块级可变全局 3 个(`_ACTIVE_RUN_TASKS`、`_RUN_ENGINE_ROUTERS`、`_ACCEPTING_NEW_RUNS`)。
- **建议**: 至少拆出 `run_event_bridge.py`(`_bridge_task_messages`/`_publish_translated_message`/`_publish_plan_events`)和 `run_lifecycle.py`(shutdown/recover/foreground 三件套)。

### A11【低】OrchestratorAgent 保留大量透传包装并访问 handler 私有方法
- **文件**: `backend/app/agents/orchestrator_agent.py:220-236`
- **问题**: `_build_step_graph` 等 6 个方法是对 `step_scheduler_handler._xxx` **私有**方法的逐一透传,既保留了上帝对象的表面积,又打破了 handler 的封装(下划线方法成了跨对象契约)。
- **建议**: 调用方直接依赖 scheduler handler 的公开方法,或把这些图算法提为模块级纯函数。

### A12【低】task_service 调用 SupervisorAgent 私有方法并旁路其决策
- **文件**: `backend/app/services/task_service.py:76-87`
- **问题**: `handle_chat` 在 Supervisor 拒绝委派后用服务层自己的正则二次覆盖,并调用 `supervisor._delegation_reply(...)` 私有方法拼回复;这套覆盖规则与 `SupervisorAgent._heuristic_decision`(`supervisor_agent.py:267-279`)语义重复。
- **建议**: 把"显式路径文件操作必须委派"规则下沉进 `SupervisorAgent.decide` 的 fallback 合成逻辑,服务层不再持有路由规则。

### A13【低】`worker_agents.py` 名不副实 + metadata key 无常量
- **文件**: `backend/app/agents/worker_agents.py:1-19`;`"supervisor_agent_hint"` 字面量散布于 `orchestrator_agent.py:114`、`planning_handler.py:74`、`run_service.py:720`、`os_execution_engine.py:1021`、`delegation_metadata.py:41-44`
- **问题**: 文件只含 allowlist 常量和归一化函数,真正的 worker agent 类在 `file_agent.py` 等;同时 task metadata 键 `supervisor_agent_hint` 以裸字符串出现 5+ 处。
- **建议**: 改名为 `delegation_registry.py`(或并入 `delegation_metadata.py`),并导出 `SUPERVISOR_AGENT_HINT_KEY` 常量。

### A14【低】default engine 配置双通道
- **文件**: `backend/app/orchestration/engine_router.py:22-23, 71-80` 与 `backend/app/config.py:281, 614`
- **问题**: `LENGRVIS_DEFAULT_ENGINE` 同时被 `configured_default_engine()`(直接读 os.environ + 两个 legacy 别名)和 `AppSettings.default_engine` 解析;run_service 传 settings 值覆盖前者,但裸 `EngineRouter()`(无参)走 env 通道,两条路径可能给出不同答案。
- **建议**: EngineRouter 只接收显式 `default_engine`,env 解析统一收敛到 config。

### A15【低】`plan_matches_supervisor_hint` 语义与名字不符
- **文件**: `backend/app/agents/delegation_metadata.py:26-32`
- **问题**: 函数名承诺"plan 是否匹配 hint",实际仅在传入 `visible_tool_names` 时检查工具面,`visible_tool_names=None` 时恒为 True;hint 本身(agent_name 维度)从未被校验。调用方(planning_handler:164, 258)总是同时调 `plan_tools_outside_visible`,两函数职责几乎重合。
- **建议**: 合并为单一 `validate_plan_against_hint(plan, hint, visible_tools) -> list[str]`,返回违规工具列表。

### A16【低】task_service 恢复路径两份近重复协程
- **文件**: `backend/app/services/task_service.py:230-247`
- **问题**: `_resume_task_through_orchestrator` 与 `_resume_task_background` 除返回值外逐行相同(try/except/final_summary/safe_transition/record)。
- **建议**: 后者包装前者即可。

---

## 各检查点结论速览

| 检查点 | 结论 |
|---|---|
| 1 依赖方向 | 总体 api→services→orchestration→agents/tools 成立,但 A1/A2 两处倒置;无运行时循环导入(靠延迟导入兜底) |
| 2 agent_hint 单一事实来源 | **Agent 名 allowlist 是单源**(`KNOWN_SUPERVISOR_WORKER_AGENTS` + `normalize` 漏斗,全链 9 处统一调用);但路由**关键词**规则四处重复(A4) |
| 3 Mock 注入边界 | 注入点干净(registry 集中、`allow_mock_fallback` 默认关);但 mock 反解析生产提示词、planner 直接 new MockProvider(A8) |
| 4 engine_router | `route_engine` 纯函数、规则集中、可独立测试 — 良好;`reason` 字符串契约(A3)与 run 绑定内存态(A6)是瑕疵 |
| 5 plan_snapshot / orchestrator_registry | 职责单一、文档清晰,与 state_machine(只管 Task 状态转移)无重叠 — 良好 |
| 6 schemas 契约 | Run/RunCreateRequest/RunStateResponse 定义集中;但双 RunPhase(A7)、`Run.state` 内嵌 `_runtime` 私有约定(run_service:820-829)靠下划线过滤,契约偏弱 |
| 7 重复/过长/上帝对象 | run_service 上帝模块(A10)、orchestrator 透传层(A11)、4 份路径正则(A5)、恢复协程重复(A16) |

## 做得好的

- **agent_hint 归一化漏斗**: 所有入口(routes_runs → run_service → merge_run_task_metadata → build_task_delegation_metadata → normalize)汇聚到 `normalize_supervisor_agent_hint` 一个函数,API 层不自带校验逻辑。
- **`route_engine` 设计**: 纯函数 + 显式 override 短路 + 集中正则表,`EngineRouteDecision` 数据类返回,单测友好。
- **`plan_snapshot.py`**: 30 行模块把并行步隔离契约写成文档化的 snapshot/write-back 两个纯函数,职责教科书级单一。
- **`orchestrator_registry`**: RLock 保护、task/run 双索引、release 语义清晰,并在 run_service 终态时主动释放避免泄漏(R4-M5 注释可追溯)。
- **planning_handler 的 hint 硬校验**: 工具面过滤 → 重试 → 剥离越界步骤 → `SupervisorHintPlanError` 失败闭环,每一步都有 audit record,管道可观测。
- **routes_runs**: 薄路由层,无业务逻辑,错误统一 404 翻译,WS 带 replay/sequence 去重。
- **每 orchestrator 独立 ToolRegistry**(orchestrator_agent.py:79-84)避免全局注册表被并发重建清空,且注释解释了为什么。