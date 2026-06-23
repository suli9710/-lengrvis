# 强化模型执行能力操作方法(Model Execution Capability Playbook)

Last reviewed: 2026-06-10

本文面向项目维护者,给出提升 Lengrvis 模型执行能力的具体操作方法,覆盖四个方向:规划与任务理解、执行成功率与纠错、工具调用准确性、上下文与模型配置。每条方法均标注改动位置、成本与验证方式。

## 0. 读前须知:度量与证据口径

任何"执行能力"改进都必须可度量。本项目有两级度量,口径不同,不可混用:

| 度量 | 命令 | 证明什么 | 不证明什么 |
|---|---|---|---|
| 黄金任务门禁 | `npm run golden:gate` | 编排、路由、风险分级、审批与脱敏契约不回归(MockProvider 离线运行,≥95% 通过率) | 真实 LLM 下的结果质量 |
| 真实 LLM 评测轨道 B | `npm run eval:real-llm` | 真实 Provider 下的意图分类正确率、计划参数缺失率、任务成功率、风险标注一致率(机器测得,报告在 `.tmp/qa-evidence/real-llm-eval/`) | 可读性/返工率等真人评分;不是 result-quality 签收 |
| 真人结果质量基线 | `npm run evidence:result-quality-review` | 真实任务的成功率(≥90%)、可读性、返工率(≤10%) | 版本间契约回归 |

**规则**:

- 改 prompt / 模型配置 → 必须跑真人基线重放;golden:gate 只作为不回归的兜底
- 改编排 / 工具契约 / 配置加载 → golden:gate + 对应专项 pytest
- 改动前后各跑一轮,留存 `.tmp/qa-evidence/golden-tasks/golden-tasks-report.json` 对比

详见 [docs/qa/golden-tasks.md](../qa/golden-tasks.md) 与 [docs/qa/agentic-product-evals.md](../qa/agentic-product-evals.md)。

## 1. 执行链路速览(改哪里影响什么)

```
用户目标
  → OrchestratorAgent (backend/app/agents/orchestrator_agent.py)
    → PlannerAgent.plan (planner_agent.py, LLM 或确定性规划)          ← 第 2 章
      → Domain Agent.act (file/document/computer/app/browser/search)  ← 第 3、4 章
        → SafetyReviewAgent (R0-R4 风险分级审批)
          → ToolRuntime.execute_tool (orchestration/tool_runtime.py)  ← 第 4 章
            → 失败? → RecoveryHandler / OS Reflection                 ← 第 3 章
  全程: ContextAwareProvider + ContextManager (context_management.py) ← 第 5 章
```

**三层确定性兜底**(模型不确定性的对冲,扩大它们就是在提升执行稳定性):

1. **确定性规划**:`backend/app/agents/planner_agent.py` 内 4 个零 LLM 计划(`_deterministic_cleanup_plan` / `_deterministic_file_plan` / `_deterministic_uninstall_plan` / `_deterministic_system_check_plan`)
2. **确定性 fast path**:`backend/app/agents/base.py` 的 `_deterministic_action`,规划好的工具调用参数齐全时直接执行,跳过一次 LLM
3. **恢复与反射**:`backend/app/orchestration/handlers/recovery_handler.py`(步骤级,≤N 次重试)+ `backend/app/orchestration/os_reflection.py`(任务级)

**配置三入口与优先级**(低 → 高):

1. `config.yaml`(模板见 `config.example.yaml`)/ `.env`
2. DB 覆盖(设置页,经 `settings_service`)
3. 进程环境变量 `LENGRVIS_*`(最高,映射表见 `backend/app/llm/registry.py`)

## 2. 方向一:规划与任务理解

### P1. 充实 Planner 系统提示【P0,纯 prompt】 ✅ 2026-06-10

`backend/app/llm/prompts/planner_agent.md` 当前仅 8 行,缺少关键约束。补充:

- 步骤粒度准则(何时拆多步、何时单步)
- 1-2 个常见任务的 few-shot JSON 计划示例
- `expected_observation` 和 `rollback_strategy` 的写法要求

prompt loader(`backend/app/llm/prompts/__init__.py`)在 dev 模式按 mtime 热加载,改完即生效,无需重启。

**验证**:`pytest backend/tests/test_prompt_loader.py backend/tests/test_prompt_reload.py`;真人基线重放;golden:gate 不回归。

### P2. 给规划上下文注入工具描述与必填参数【P1,约 10 行代码】 ✅ 2026-06-10

`PlanningHandler._create_plan`(`backend/app/orchestration/handlers/planning_handler.py:80-81`)目前只传 `tool.name` 列表,`planner_user.md` 渲染成 `- file.trash` 这样的裸名,模型不知道每个工具吃什么参数。改为:

```
- {name}: {description} (required: {schema.required})
```

需配合 T1(先把 description 写好),两者叠加是规划选型准确率的最大杠杆。

**验证**:golden:gate 的 `plan_tools` / `pending_approvals` 断言;观察 DB `plans` 表中 LLM 计划的参数缺失率。

### P3. 扩展确定性规划覆盖高频意图【P2,中等代码】 ✅ 2026-06-11

仿照原有 4 个 `_deterministic_*_plan` 模板新增 2 个零 LLM 计划,并把 `create_plan` 的分发重构为模板元组循环(顺序:cleanup → file → uninstall → system_check → open_app → search,旧意图保持优先):

- `_deterministic_open_app_plan`("打开记事本"/"open notepad"):AppAgent + `app.launch_installed`,R1_OPEN_ONLY 免审批;常见中文名走别名映射命中允许列表(记事本→notepad、计算器→calculator)。守卫:含 Windows 路径/网页词(网站、http、.com 等)/文件文件夹词/删除/卸载/体检意图时让位 LLM。
- `_deterministic_search_plan`("帮我找一下文件:季度报告"/"find file X"):FileAgent + `file.search_by_name`,R0 只读;支持冒号后取查询词与动词剥离两种提取。守卫:重复文件(duplicate)、删除/清理/卸载意图、显式路径、空查询时让位 LLM。

**验证**:新增 `backend/tests/test_planner_deterministic.py`(27 项:参数提取正例、含糊意图让位、工具未注册让位、分发优先级回归);黄金任务新增 `gt-run-open-app-missing-fails-closed`(未允许应用失败关闭、零审批、无 GUI 副作用、恢复链兜底)并强化 `gt-run-search-name`(断言确定性查询提取真实命中 fixture 文件——此前 MockProvider 整句查询搜不到任何文件);golden:gate 全量 35 passed。

### P4. 用好已接好的上下文管道【P0-P1,改常量】 ✅ 2026-06-11

`create_plan` 已支持 `memory_context`(含 `tags=["lesson"]` 教训召回)、`goal_context`、`session_context`,但截断很紧:每条记忆 200 字符、session 各字段 240/360 字符。已在 `planner_agent.py` 顶部引入命名常量并放宽:记忆 200→600、session 字段 240→600、note 180→360、会话摘要 360→1200、目标描述 240→480、屏幕描述 240→480(C1 已校准大窗口模型,token 节省不抵规划质量损失)。同时在上下文块非空时注入一段使用指引(背景信号 vs. User goal 真源、lesson 表示不可重复的失败)。

**验证**:`pytest backend/tests/test_session_context.py backend/tests/test_goal_stack.py backend/tests/test_memory_agent.py`。

### P5. 监控计划质量指标【P3,只读分析】 ✅ 2026-06-11

每次 LLM 规划的 `model_action` 信封(`planning_handler._annotate_plan_tool_contracts`)记录了 `model_supplied_risk_level` vs `derived_risk_level`。已落地只读聚合:`app/services/plan_quality_service.py` 的 `risk_annotation_consistency()` 统计一致率与按工具的失配明细,经 `GET /api/audit/plan-quality/risk-consistency` 暴露——不一致率下降说明模型对任务风险的理解在变准。

**验证**:`pytest backend/tests/test_plan_quality_service.py`。

## 3. 方向二:执行成功率与纠错

### R1. 调重试预算【P0,纯配置】 ✅ 2026-06-10

`RecoveryHandler` 默认 `max_retries=3`(取自 `AppSettings.recovery_max_retries`,`backend/app/config.py:264`)。三种调法任选:`.env` 设 `LENGRVIS_RECOVERY_MAX_RETRIES`、config.yaml `orchestration:` 段、或设置页(`settings_service.py:144` 已白名单)。本地小模型场景建议升到 4-5,用重试换成功率。

**验证**:`pytest backend/tests/test_recovery_handler.py backend/tests/test_resilience_settings.py`;审计事件 `task.recovery_step_created` 计数变化。

### R2. 调优恢复推理 prompt【P0,纯 prompt】 ✅ 2026-06-10

恢复步骤由 `orchestrator._consult_subagent → BaseAgent.act`(`backend/app/agents/base.py:53`)生成,user 模板是 `agent_act_user.md`,system 是各 domain agent 的 `file_agent.md` / `app_agent.md` 等。在模板中加入"常见错误 → 替代工具"映射(例:路径不存在 → 先 `file.search_by_name` 再重试),可直接提高恢复命中率。

**验证**:`pytest backend/tests/test_subagent_reasoning.py backend/tests/test_recovery_handler.py`;真人基线。

### R3. 反射上限可配置化【P2,中等代码】 ✅ 2026-06-11

`os_reflection.py:35-36` 的 `MAX_REFLECTIONS_PER_RUN=2`、`MAX_REFLECTIONS_PER_STEP=1` 原为硬编码常量(与 recovery_max_retries 不同,没有 settings 入口)。已仿照 `recovery_max_retries` 增加 `os_reflection_max_per_run` / `os_reflection_max_per_step` 两个配置项:`config.py`(字段 + `from_sources` int_value)、`llm/registry.py` env 映射(`LENGRVIS_OS_REFLECTION_MAX_PER_RUN` / `_PER_STEP`)、`settings_service.py` 白名单(min=0)、`config.example.yaml` `orchestration:` 段。`OSReflectionDecider` 现接受 `max_per_run`/`max_per_step` 参数(默认回退到模块常量,行为不变),`OSExecutionEngine.__init__` 用 `get_effective_settings()` 注入。长任务可调大 per-run 多反射几轮,也可设 0 关闭反射。

**验证**:`pytest backend/tests/test_execution_engines.py backend/tests/test_resilience_settings.py`(新增 decider 参数化单测 + yaml/env 覆盖测试)。

### R4. 消灭低信息失败【P3,逐工具打磨】 ✅ 2026-06-11(运行时兜底)

`os_reflection._is_low_information_failure`(`os_reflection.py:299`)会把 `error/observation` 为空或等于 "failed"/"unknown error" 的失败直接降级为 `ask_user`——打断自动化去问用户。已在 `tool_runtime.py` 运行时统一兜底,覆盖全部工具而非逐个打磨:(1) 未捕获异常路径产出 `TypeError: detail (tool=xxx)` 形态的 typed error + 指向 args 的 observation,空异常消息也不会再产生空 error;(2) 工具自报的低信息 error("failed" 等)经 `_actionable_error_text` 追加工具名/参数键/建议。新工具仍应自带可操作错误细节(路径、原因、建议下一步),兜底只是保证反射层永远有信息可推理。

**验证**:`pytest backend/tests/test_tool_runtime.py`(新增 `test_low_information_tool_errors_are_enriched`);golden:gate;审计中 `ask_user` 反射决策占比下降。

### R5. 新工具遵守资源态契约【P0,写入规范】 ✅ 2026-06-10(本节即规范)

工具输出含 `resource_state_error` 或 `error_code ∈ {STALE_RESOURCE_STATE, READ_STATE_REQUIRED}` 时,反射层走确定性 read-before-retry(自动插入 `file.read_text` + 重试步,见 `os_reflection._read_before_retry_decision`)。新写工具时按 `backend/app/orchestration/resource_state.py` 契约输出这些字段,即可免费获得该恢复路径。

## 4. 方向三:工具调用准确性

### T1. 补齐工具 description / search_hint【P1,数据性改动,最高性价比】 ✅ 2026-06-10

**现状**:`backend/app/tools/file_tools.py:808` 的工具描述是 `name.replace(".", " ")` 生成的占位文本——`file.trash` 的描述就是 "file trash";`search_hint` 默认空串。而 `tool.search` 评分(`registry.py:58-66`)和 P2 的规划注入都依赖这两个字段。

**做法**:逐工具在 `register()` 中写 1 句清晰描述(说明做什么、关键参数)+ 同义词 search_hint。

**验证**:`pytest backend/tests/test_tool_search.py backend/tests/test_tool_protocol.py`;golden:gate。

### T2. 收紧 input_schema 以扩大 fast path【P2,中等代码】 ✅ 2026-06-11

`BaseAgent._deterministic_action`(`base.py:99`)零 LLM 直接执行的条件:工具已注册 + owner 匹配 + `required` 参数齐全 +(`fast_path_eligible` 且有显式 object schema,或 R2/R3 带 dry_run)。审查全部 125 个工具后发现 13 个 `document.*` 只读工具 `fast_path_eligible=True` 但 `input_schema={}`(`_has_explicit_object_schema` 不通过),每次都落回多一次 LLM hop。已在 `document_tools.py` 为这 13 个工具补齐显式 object schema(按各函数真实读取的 args 声明 `properties`,`required` 只列函数硬取的键如 `path`/`content`,可选键如 `question`/`top_k`/`max_chars` 进 properties 但不强制)。现 51 个 fast_path_eligible 工具 schema gap 从 13 → 0。

**验证**:`pytest backend/tests/test_policy_fast_path.py backend/tests/test_tool_protocol.py backend/tests/test_subagent_reasoning.py`;新增 `test_fast_path_eligible_tools_declare_explicit_object_schema` 回归门禁(防止新工具再引入 gap)+ DocumentAgent fast-path 正例/缺参 request_revision 两个行为测试。

### T3. 扩大 fast_path_eligible 覆盖【P3,改标志但需安全评审】 ✅ 2026-06-11

目前 file 工具按 `read_only` 自动标记(`file_tools.py:819`),app 工具按 R0/R1 标记,browser/adapters/mcp/skills 全部 `False`(维持不变——第三方/外网面不进 fast path)。本轮安全评审后扩面:`file.cluster_by_content`、`app.cluster_installed`、`file.suggest_folder_structure` 三个纯本地只读聚类工具开启 `fast_path_eligible=True` 并补齐显式 object schema + effects/trust_tier 元数据;`image.cluster*` 因 describe_image 可能走视觉模型而保持 False。附带正确性修复:`search.*` 三个工具实际访问公网但未声明 `external_network`,已补 `external_network=True`,确保 policy fast path 排除逻辑与审批指纹反映真实网络面。

**验证**:`pytest backend/tests/test_policy_fast_path.py backend/tests/test_tool_decision_cache.py backend/tests/test_tool_protocol.py`。

### T4. 用 validate_input 钩子前置拦截坏参数【P2,中等代码】 ✅ 2026-06-11

`ToolRuntime._validate_input`(`tool_runtime.py:766`)只在工具声明 `validate_input` 回调时才校验,失败即 `fatal_failed`(不浪费重试预算)并记 `tool.validation_failed` 审计事件。已给三个文档类高频出错工具补回调(均表达 JSON schema 无法表达的语义约束):`document.compare`(必须有两个文档路径——`left_path`+`right_path` 或两元素 `paths` 列表;此前缺路径会在 `resolve_authorized(None)` 抛裸 `TypeError` 低信息失败)、`document.qa` / `document.ask_with_citations`(必须有非空 `question`,否则原会静默回答空串得到无用结果)。坏参数现在变成"调用前清晰报错"(与 R4 配合)。

**验证**:`pytest backend/tests/test_document_ai.py backend/tests/test_tool_runtime.py`;新增验证器单测(compare 缺路径/类型错、question 空白拒绝 + 正例通过)与注册接线断言;通用 `fatal_failed`+`tool.validation_failed` 机制已由 `test_tool_runtime_validation_failure_blocks_execution` 覆盖。

### T5. 维护 R2/R3 dry_run 契约【P0,写入检查单】 ✅ 2026-06-10(本节即检查单)

`ToolDefinition.contract_errors`(`backend/app/core/schemas.py:57`)强制 R2/R3 工具必须 `supports_dry_run`,否则工具对模型**静默不可见**(`is_model_visible`),导致规划莫名失败。新工具上线检查单加一条:对照 `to_public_dict()['contract_errors']` 自检,或查 `/api` 工具列表的 `contract_valid` 字段。

## 5. 方向四:上下文与模型配置

### C1. 按模型实际窗口校准 token 预算【P0,纯配置,本地模型必做】 ✅ 2026-06-10(云端 1M 窗口/900k 压缩阈值)

默认值假设大窗口模型:`model_context_window=128000`、`model_auto_compact_token_limit=96000`、warning/error buffer 各 20000(`config.py:191-194`)。本地小窗口模型(如 Qwen2.5-3B)必须同步调小(如 32768/24000),**否则压缩永不触发,直到 prompt-too-long 才被动走 reactive retry**。入口:`LENGRVIS_MODEL_CONTEXT_WINDOW` / `LENGRVIS_MODEL_AUTO_COMPACT_TOKEN_LIMIT` 或 config.yaml `llm:` 段。

**验证**:`pytest backend/tests/test_context_management.py backend/tests/test_context_compaction.py backend/tests/test_context_usage.py`;审计事件 `context.reactive_retry` 次数下降。

### C2. 细调三级压缩参数【P1,纯配置】 ✅ 2026-06-10(云端值随 .env 迁移生效;长任务遇问题再按下表微调)

长任务卡顿/丢上下文时对症调参(均有 `LENGRVIS_CONTEXT_*` 环境变量,映射见 `llm/registry.py:48-61`):

| 机制 | 参数 | 默认 | 调整方向 |
|---|---|---|---|
| micro-compact | `context_micro_compact_age` / `_tool_result_chars` | 8 条 / 1200 字符 | 丢早期工具结果 → 调大 |
| history-snip | 触发阈值 / 保留条数 | 160 / 80 | 长任务历史太长 → 调小阈值 |
| session-memory | `summary_limit` | 12000 | 摘要丢关键信息 → 调大 |

**验证**:同 C1,另看 run timeline 中 `context_projection.strategy` 字段(micro/snip/session/auto)。

### C3. 调优压缩 prompt 模板【P1,纯 prompt】 ✅ 2026-06-10(并修复 `{{var}}` 模板语法导致摘要丢失的 bug)

auto-compact 摘要、history-snip 占位、reactive 压缩的文案分别在 `backend/app/llm/prompts/context_auto_compaction.md` / `context_history_snip.md` / `context_reactive_compaction.md`。可中文化,并要求摘要必须保留:未完成步骤、已批准的审批、关键文件路径等任务态信息。

**验证**:`pytest backend/tests/test_context_compaction.py`;长任务真人重放。

### C4. 任务级模型路由【P0,纯配置】 ✅ 2026-06-10(纯云端,efficiency 模式确认)

`get_provider_for_mode`(`llm/registry.py:215`)支持按任务路由:`task ∈ {planner, supervisor, subagent, embed, vision, ocr, default}`。

- "规划质量差但要隐私" → `LENGRVIS_MODE=hybrid`(planner/supervisor 走云,其余走本地)
- 纯本地 → 优先 ONNX(`detect_onnx_backend`)→ Ollama 自动探测(默认 `qwen2.5:3b-instruct`)
- `allow_mock_fallback` 默认 False,**生产环境勿开**

**验证**:`pytest backend/tests/test_mode_routing.py backend/tests/test_privacy_mode_offline_eval.py backend/tests/test_provider_fallback.py`。

### C5. 韧性与采样参数【P1,纯配置】 ✅ 2026-06-10(timeout 180s、max_tokens 32768、重试 3 次/退避 1.0s)

`config.py:236-242`,均有 `LENGRVIS_*` 入口:

- `temperature=0.2`:规划类任务保持低温,勿调高
- `max_tokens=1600`:长计划被截断时升到 2400-4000
- `timeout=30`:本地慢模型需同步加大
- `llm_api_max_retries=2` / backoff 0.25s;熔断 threshold=5 / cooldown=30s

**验证**:`pytest backend/tests/test_resilience_settings.py backend/tests/test_openai_compatible_resilience.py`;`usage.py` 记录的失败率。

## 6. 优先级矩阵与推荐实施顺序

| 优先级 | 改进项 | 成本 | 预期收益 | 状态 |
|---|---|---|---|---|
| **P0 立即** | P1 planner prompt、R2 恢复 prompt、C1 token 预算、C4 hybrid 路由、R1 重试预算、R5/T5 写入规范 | 纯 prompt/配置,热加载即生效 | 高:规划与恢复是成功率两大瓶颈,零代码风险 | ✅ 2026-06-10 完成(含 .env 前缀迁移 MARVIS_→LENGRVIS_) |
| **P1 一周内** | T1 工具描述、P2 规划注入工具信息、C2/C3 压缩调优、C5 韧性参数 | 数据性改动 / 约 10 行代码 | 高:T1+P2 叠加是被低估的最大杠杆 | ✅ 2026-06-10 完成(T1:新增 `app/tools/tool_catalog.py`,125 个工具全部有描述+中英 search_hint;P2:planner prompt 注入描述与必填参数;C3:修复压缩模板 `{{var}}` 不替换导致摘要丢失的 bug) |
| **P2 按需** | T2 schema 收紧、T4 validate_input、P3 确定性规划扩展、R3 反射上限配置化 | 代码改动 + 新增测试 | 中-高:扩大零 LLM 确定性路径 | ✅ 2026-06-11 完成(详见各节与实施日志第四轮) |
| **P3 长期** | R4 消灭低信息失败、T3 fast_path 扩面、P5 风险标注监控 | 逐工具迭代 | 中:渐进收益,排进日常维护 | ✅ 2026-06-11 完成(R4 运行时兜底、T3 聚类工具扩面+search 网络面修正、P4 截断常量、P5 一致率端点) |

## 7. 改动验证清单

| 改动类型 | 必跑 |
|---|---|
| 任何改动 | `npm run golden:gate`(报告:`.tmp/qa-evidence/golden-tasks/golden-tasks-report.json`,≥95%) |
| prompt / 模型配置 | 上条 + 真人基线重放,经 `npm run evidence:result-quality-review` 归档 |
| 规划相关 | `pytest backend/tests/test_prompt_loader.py test_session_context.py test_goal_stack.py` |
| 恢复/反射相关 | `pytest backend/tests/test_recovery_handler.py test_resilience_settings.py test_subagent_reasoning.py` |
| 工具相关 | `pytest backend/tests/test_tool_protocol.py test_tool_search.py test_policy_fast_path.py test_tool_runtime.py` |
| 上下文/模型相关 | `pytest backend/tests/test_context_management.py test_context_compaction.py test_mode_routing.py test_provider_fallback.py` |

> 再次强调:golden:gate 跑在 MockProvider 上,只证明契约不回归,**不能证明真实 LLM 质量**。涉及模型行为的改进,以真人基线为准。

## 8. 实施日志

> 本节随每轮实施实时更新。状态标记同时打在第 2-5 章各方法标题上(✅ 完成 / ⬜ 待办)。

### 2026-06-10 第一轮(P0:P1 + R2)

- **P1**:重写 `backend/app/llm/prompts/planner_agent.md`(8 行 → 完整版):输出契约(agent_name 与工具前缀对应表、禁止占位参数)、步骤粒度准则(先发现后修改)、depends_on 规则、expected_observation/rollback_strategy 写法、两个 few-shot JSON 示例(真实工具名与参数)。
- **R2**:`agent_act_user.md` 新增失败决策指引(禁止重复同样调用 + 常见错误→恢复动作映射);6 个 domain agent prompt 各加领域专属 Failure recovery 段。
- 约束核实:渲染器为 `string.Template`(`$var`),新增内容无裸 `$`;测试要求的关键词(authorized directories / privacy / uninstall / efficiency / citation)全部保留。
- 验证:prompt/恢复/规划专项 54 项通过;golden:gate 34/34。

### 2026-06-10 第二轮(P0 配置:R1 + C1 + C4 + C5,云端 API)

- **关键修复**:`.env` 使用项目改名前的死前缀 `MARVIS_*`/`MAVRIS_*`(commit e20edcf2 删除了旧别名后无任何代码读取),云端配置一直被静默忽略、后端实际跑默认值。已全量迁移为 `LENGRVIS_*`(29 键),DPAPI 加密 key 验证可解密加载。
- **C1**:auto-compact 阈值 950000 → 900000(原值与 error 阈值重合,压缩会迟至硬报错才触发)。
- **R1/C5**:显式 `LENGRVIS_RECOVERY_MAX_RETRIES=3`;`LLM_API_MAX_RETRIES=3`、退避 1.0s(公网抗抖动)。
- **C4**:纯云端确认 `LENGRVIS_MODE=efficiency`,无需 hybrid。
- **测试隔离加固**:`backend/tests/conftest.py` 新增 autouse fixture `isolate_local_runtime_config`——此前套件依赖".env 是死前缀"的偶然事实,真实配置生效即打破 2 个测试;现在契约测试不读开发者真实 config.yaml/.env。
- 验证:历史全量回归通过;golden:gate 34/34。注意:迁移前启动的后端进程需重启才读到新 `.env`。

### 2026-06-10 第三轮(P1:T1 + P2 + C3)

- **T1**:新增 `backend/app/tools/tool_catalog.py` 中央目录(name → description + 中英双语 search_hint),11 个工具模块注册点统一接线(`tool_description(name)` / `tool_search_hint(name)`),并删除 developer_tools 原有的共用通用 hint。运行时自检:125 个工具,占位描述 0、空 hint 0。中文查询现可命中 `tool.search` 评分。
- **P2**:`PlannerAgent.create_plan` 新增可选 `tool_specs` 参数(仅 prompt 渲染用,`tools` 纯名称列表保留供确定性规划成员判断);`planning_handler._planner_tool_spec` 生成 `name: description (required: args)` 行;对老签名 planner 先单独去掉 `tool_specs` 重试,不牺牲 session_context。
- **C3 + bug 修复**:三个压缩模板(`context_auto_compaction.md`/`context_history_snip.md`/`context_reactive_compaction.md`)原用 `{{var}}` mustache 语法,渲染器只认 `$var`——**自动压缩摘要从未进入对话,模型压缩后看到字面量 `{{summary_text}}`**。已改为 `$var` 并强化文案(摘要视为权威历史、缺细节重读文件)。补 2 个回归测试;原有 2 个测试此前因该 bug 才通过(摘要被丢弃使压缩后更小),已将夹具改为长消息(`chars=900`)使断言语义成立。
- 验证:全量 1571 通过(+2 回归测试);golden:gate 34/34。

### 2026-06-11 第四轮(P2 批次:R3 + T2 + T4 + P3)

- **R3**:`os_reflection_max_per_run`(默认 2)/ `os_reflection_max_per_step`(默认 1)新配置项,走 `recovery_max_retries` 同款三入口(`LENGRVIS_OS_REFLECTION_MAX_PER_RUN`/`_PER_STEP` env、config.yaml `orchestration:` 段、设置页白名单 min=0);`OSReflectionDecider` 参数化,`OSExecutionEngine` 构造时从 `get_effective_settings()` 注入。`config.example.yaml` 同步示例。
- **T2**:审查 125 个工具发现 13 个 `document.*` 工具 `fast_path_eligible=True` 但 `input_schema={}`,每次执行多一次 LLM hop;已全部补显式 object schema(`required` 只含函数硬取键)。fast-path schema gap 13 → 0,并加 `test_fast_path_eligible_tools_declare_explicit_object_schema` 回归门禁防止新工具再引入。
- **T4**:`document.compare`(两路径必填,原缺路径报裸 `TypeError`)、`document.qa`/`document.ask_with_citations`(question 非空)补 `validate_input` 回调,坏参数前置为 `fatal_failed`+清晰文案+`tool.validation_failed` 审计,不烧重试预算。
- **P3**:新增 `_deterministic_open_app_plan`(打开应用,R1 免审批,中文名别名映射)与 `_deterministic_search_plan`(按文件名搜索,R0,冒号/动词剥离两种查询提取),`create_plan` 分发重构为模板循环,旧意图优先;守卫覆盖路径/网页/文件夹/删除/清理/卸载/重复文件冲突。黄金任务 33 → 34(`gt-run-open-app-missing-fails-closed`),`gt-run-search-name` 强化为断言确定性提取真实命中 fixture(此前 Mock 整句查询零命中)。
- 验证:`test_planner_deterministic.py` 新增 27 项;执行引擎/韧性/工具协议/文档/子代理推理/规划相关定向套件全绿;`npm run golden:gate` 35/35(报告已归档 `.tmp/qa-evidence/golden-tasks/`);全量 backend pytest 结果为历史 dirty-worktree 证据，不作为当前 release evidence。
- 已知 flake(非本轮引入):`test_cleanup_planner.py` 的 hash 稳定性与 trash 审批两测在高 IO 负载下偶发——`CleanupItem` 哈希含 `mtime_ns`,NTFS 延迟时间戳刷新会使同一文件两次 stat 返回不同值;隔离与复跑均稳定通过。后续可考虑把哈希里的 `mtime_ns` 降精度到毫秒。

### 2026-06-11 第五轮(P3 批次:R4 + T3 + P4 + P5,附 Computer Use 视觉 grounding)

- **R4**:`tool_runtime.py` 增加 `_LOW_INFORMATION_ERRORS` / `_actionable_error_text` / `_exception_error_text` 运行时兜底——未捕获异常产出 typed error(类名+消息+工具名)与指向 args 键的 observation,工具自报的 "failed" 类低信息 error 统一追加工具名/参数键/建议,保证反射层永远有信息可推理而非降级 `ask_user`。
- **T3**:`cluster_tools` 4 个本地只读工具补显式 schema 与完整元数据(`read_only`/`concurrency_safe`/`capabilities`/`effects`/`resource_kinds`),本地两个标 `fast_path_eligible=True`;`search_tools` 全部补 `external_network=True`(修正口径:公网工具不进 fast path,策略层正确感知网络访问)。
- **P4**:`planner_agent.py` 截断常量化并放宽(记忆 200→600、session 字段 240→600、会话摘要 360→1200 等),上下文块非空时注入使用指引(背景信号 vs. User goal 真源、lesson 语义)。
- **P5**:新增 `plan_quality_service.risk_annotation_consistency()` 聚合 model_supplied vs derived 风险标注一致率,`GET /api/audit/plan-quality/risk-consistency` 暴露,按工具列出失配明细。
- **Computer Use 视觉 grounding 回退链**(对标 Operator,优化方案 Phase 1):新增 `ui_automation.locate_on_screen` 工具——先走 UIA 语义查找,失败时截图 → 视觉模型 grounding(新 prompt 模板 `vision_locate_element.md`,返回归一化坐标比例)→ 换算屏幕坐标,供 `ui_automation.click_at`(仍走 dry-run + 审批)使用;`computer_agent.md` 失败恢复段同步教 Agent 这条链。
- 验证(静态完成,shell 恢复后执行):`pytest backend/tests/test_tool_runtime.py backend/tests/test_plan_quality_service.py backend/tests/test_ui_grounding.py backend/tests/test_session_context.py`;`npm run golden:gate` 不回归。

### 待办队列

- **真人基线**:prompt/配置改动后按第 0 章口径跑 `npm run evidence:result-quality-review` 归档(待执行)
- **第五轮验证**:本机 shell 恢复后补跑第五轮全部定向 pytest 与 golden:gate,并归档证据
