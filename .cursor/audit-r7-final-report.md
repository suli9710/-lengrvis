# Lengrvis/mavris Round 7 审计终报 — 全量审计 + 全量测试

**审计日期：** 2026-06-12
**仓库路径：** c:\Users\Suli\Desktop\mavris(工作树,基线提交 `4be1077c`)
**审计类型：** 四视角静态审计(安全/可靠性/架构/逻辑)+ 后端全量 pytest + Desktop vitest
**基线：** `.cursor/audit-r5-final-report.md`(代码质量 74/C+)、`.cursor/audit-r6-final-report.md`(能力 68/C)+ R6 修复冲刺 P1–P3

---

## 1. 中文执行摘要

R7 是 R6 修复冲刺(agent_hint 全链贯通)落地后的第一次全量复检。**安全面继续保持优秀(88,无 Critical/High,历轮修复零回退);可靠性与架构在主路径上扎实,但旁路(线程回退、并行 stop 排空、内存无界增长)和分层债(agents↔orchestration 双向依赖、双 RunPhase 枚举)拉低分数。**

**全量测试不是绿的:后端 35 failed / 1774 passed / 3 skipped。** 35 个失败归并为 4 个独立失败簇,其中 1 簇(4 个用例)是真实产品代码缺陷(Planner 兼容降级逻辑破损),1 簇(7 个用例)暴露后台任务调度对事件循环生命周期的依赖,其余 2 簇为测试桩过期与发布工具链契约失配。

**综合评分:74 / 100(C+)** — 与 R5 持平:工程质量上行被测试回归抵消。

| 维度 | 得分 | 一句话 |
|------|------|--------|
| 安全 Security | **88** | 历轮 SSRF/认证/路径/命令加固全部未回退;仅 webhook 未统一 IP-pinning(Medium,默认未激活) |
| 可靠性 Reliability | **72** | 主循环生命周期扎实;线程回退路径取消失效、并行 stop 丢已完成结果、2 处内存无界增长 |
| 架构 Architecture | **71** | agent_hint 已收敛单一漏斗;但 agents↔orchestration 双向依赖、core 依赖倒置、4 份重复关键词路由表 |
| 逻辑 Logic | **78** | hint 硬校验方向正确;retry 无反馈回灌、空 plan 语义冲突、CREATED→FAILED 迁移缺失 |
| 测试通过率 | **98.1%** | 35/1812 失败,详见 §3 失败簇归因 |

---

## 2. 测试执行结果

### 2.1 后端全量 pytest

| 项 | 值 |
|----|-----|
| 命令 | `python -m pytest -q --timeout=180 --timeout-method=thread`(独立 LENGRVIS_DATA_DIR) |
| 结果 | **35 failed / 1774 passed / 3 skipped**,768s(12:48) |
| 超时挂死 | 0(本轮加 pytest-timeout 防护;上一会话遗留的 2 个挂死 pytest 进程仍在,建议手动清理 PID 28656 / 111808) |
| 日志 | `.cursor/r7-fullsuite.log` |

### 2.2 Desktop vitest

**31 passed / 0 failed**(2 个文件,242ms)。日志:`.cursor/r7-desktop-vitest.log`。

### 2.3 Mobile

无单元测试脚本(仅 smoke 脚本,本轮未执行真机链路)。

---

## 3. 测试失败簇归因(35 个失败 → 4 个根因)

### 簇 B【产品缺陷|P1】planning_handler 兼容降级逻辑破损 — 4 个用例

- 用例:`test_lengrvis_parity_e2e.py` ×2、`test_perception_integration.py::test_planning_handler_falls_back_for_legacy_create_plan_signature`、`test_session_context.py::test_planning_handler_passes_session_context_to_planner`
- 根因:`planning_handler.py:177-235` `_create_plan_legacy_fallback` 的降级分支**仍把它声称要剥离的参数发回去**——第 221 行在"perception_context 不被支持"分支中再次传 `perception_context=`;且全部三层降级都无条件传 `agent_hint=`,任何不接受 `agent_hint` 的旧签名 planner 都无法降级成功。
- 修复:用 `inspect.signature` 按 planner 实际形参过滤 kwargs,替代脆弱的 TypeError 字符串嗅探(架构视角 A9 同时建议:同仓库内签名已知,降级层可整体删除并修测试桩)。

### 簇 C【产品脆弱性|P1】后台 run 引擎循环绑定请求级事件循环 — 7 个用例

- 用例:`test_privacy_mode_offline_eval.py` 全部 7 个(run 永不进入终态,单测隔离复现)
- 根因:`run_service.py:991` `_schedule_background` 在**有运行中 loop 时**用 `loop.create_task`。TestClient 场景下该 loop 是请求级的,响应返回后 loop 关闭,`_run_engine_loop` 协程被丢弃 → run 永久停留在初始 phase(这正是全量日志中 `coroutine '_run_engine_loop' was never awaited` RuntimeWarning 的来源)。生产 uvicorn 主 loop 常驻所以不触发,但任何"宿主 loop 非常驻"的嵌入场景(桌面打包、脚本调用)都会复现。
- 修复:`_schedule_background` 不应信任"当前恰好有 loop",应将 run 引擎循环投递到**进程级常驻 loop/线程**(自有事件循环线程或 task_pool),与请求生命周期解耦。

### 簇 A【测试桩过期|P2】router/engine `task_metadata` 签名升级未同步测试替身 — 4 个用例

- 用例:`test_runs_api.py` 的 4 个(redacts_error / perception_suggestion / cancelled / paused)
- 根因:R6-P3 给 `EngineRouter.start_run` / 引擎 `start_run` 增加了 `task_metadata` 关键字参数(`engine_router.py:173`),`test_runs_api.py` 内多个 stub Router/Engine 未补该形参 → stub 抛 `TypeError` 被失败路径吞掉,断言看到的是签名错误而非预期行为(其中 redacts 用例的报错信息断言直接暴露了 `unexpected keyword argument 'task_metadata'`)。
- 修复:测试桩签名补 `*, task_metadata=None`。产品代码本身一致、无需改动。

### 簇 D【发布工具链契约失配|P2】release evidence packet 源码契约指向已重构文件 — 20 个用例

- 用例:`test_start_app_script.py` 的 20 个(全部 `Status: source_contract_failure`)
- 根因:`mobile/src/api/client.ts` 已被重构为 facade(实现移入 `mobile/src/api/client/` 子目录:types/http/endpoints/security),而 `scripts/collect_release_evidence_packet.ps1:615-624` 的 `$mobileRemoteInputClientNeedles` 仍然在旧文件里逐字 grep `assertRemoteInputApprovalMatchesSession` 等 9 个标记 → 9/9 全部 miss → 整包 fail-closed 退出非零 → 20 个测试连锁失败。(UI 与 smoke 两组标记均仍命中,fail-closed 行为本身符合设计。)
- 修复:契约路径改为扫描 `mobile/src/api/client/endpoints.ts`、`client/security.ts` 等新位置(或对 `client/` 目录聚合扫描)。

### 遗留失败测试根因结论(R6-P3 尾巴)

`test_supervisor_chat_flow.py::test_executable_turn_uses_local_provider_when_available` 上一会话失败(calls==2),当前工作树已把断言放宽为 `1 <= calls <= 2`,本轮通过。逻辑视角判定:**放宽是遮蔽性的** —— 历史上的第 2 次调用是 hint-retry 正确拦截当时越界的 mock payload;但 `planning_handler.py:133-146` 的 retry **不回灌任何越界反馈、两轮入参完全相同**,对确定性 provider 是纯重复调用。建议恢复 `calls == 1` 断言,并给 retry 注入违规工具反馈(详见逻辑报告第一部分)。

---

## 4. 四视角发现摘要(详表见各 lens 文件)

### 4.1 安全(88/100,`.cursor/r7-lens-security.md`)

| 严重度 | 发现 |
|--------|------|
| Medium | `adapters/webhook.py:39` 仅 `validate_outbound_http_url`,未与 LLM/MCP 统一用 `pin_outbound_http_url` → DNS-rebinding TOCTOU(默认无注入 client,路径未激活) |
| Low ×3 | `198.18/15` fake-IP 无条件放行;`lan.py:21` 生产路径硬编码接受 `"testclient"`;HTTP skill 仅字面量判断 loopback |
| 已验证良好 | outbound_url 双层校验+pin、openai_compatible 每 attempt 重 pin、mobile JWT require-claims、配对单次兑换 `BEGIN IMMEDIATE`、permissions deny 优先、paths 符号链接逃逸校验、developer_tools 白名单 shell=False、mock provider 生产门控 等 17 项 |

### 4.2 可靠性(72/100,`.cursor/r7-lens-reliability.md`)

| ID | 严重度 | 发现 |
|----|--------|------|
| R7-H1 | 高 | `browser_activity_runtime.py:247,549` 会话与事件 dict/list 只增不删,内存无界增长 |
| R7-H2 | 高 | `os_execution_engine.py:687-691` 并行批次 stop 时丢弃**已完成**兄弟步骤结果且不 write_back → 恢复后重跑已执行副作用(scheduler handler 的 `_drain_running_after_stop` 是正确范本) |
| R7-M1–M7 | 中 | 线程回退路径 run 无法中途取消(`concurrent.futures.Future.cancel` 对运行中 no-op);shutdown 不排空线程 run;路径写锁 per-loop 跨 loop 失效;routes_approvals/scheduler 绕过 orchestrator_registry(实时流丢失);run_event_bus 在 loop 线程同步写 SQLite;`router.cancel_run` 后台任务无强引用;InMemoryRunStore 无淘汰 |
| R7-L1–L8 | 低 | RuntimeWarning 来源确认(teardown 良性,但暴露三入口行为不一致);`_fired_ids` 无界;崩溃恢复只覆盖 RUNNING 等 |

### 4.3 架构(71/100,`.cursor/r7-lens-architecture.md`)

| ID | 严重度 | 发现 |
|----|--------|------|
| A1 | 高 | `delegation_metadata.py:6` agents→orchestration 反向导入,与 orchestration→agents 形成包级双向耦合 |
| A2 | 高 | `core/schemas.py:10-12` 核心层导入 orchestration 枚举,分层倒置(建议三枚举下沉 core) |
| A3 | 高 | `route_engine` 的人类可读 `reason` 字符串被 2 处当机器契约(`"system diagnostics" in route.reason`) |
| A4–A10 | 中 | 关键词路由表 4 处重复;Windows 路径正则 4 份;run→engine 绑定只存内存(重启后 cancel 静默失效);双 RunPhase 枚举手工互转;MockProvider 反解析生产提示词;TypeError 字符串嗅探降级(=簇 B);run_service 1036 行上帝模块 |
| A11–A16 | 低 | 跨对象私有方法调用、metadata key 裸字符串、default engine 双配置通道等 |
| 做得好 | agent_hint 归一化单一漏斗(9 处统一调用 `normalize_supervisor_agent_hint`)、route_engine 纯函数、plan_snapshot/orchestrator_registry 职责教科书级、routes_runs 薄路由层 |

### 4.4 逻辑(78/100,`.cursor/r7-lens-logic.md`)

| # | 严重度 | 发现 |
|---|--------|------|
| 1 | 中 | hint-retry 无反馈回灌,确定性 provider 下恒等重复调用(见 §3 遗留失败结论) |
| 2 | 中 | 空 plan 语义冲突:matcher 视为匹配(True),guard 却抛 `SupervisorHintPlanError` → 带 hint 的合法澄清式 plan 必然硬失败 |
| 3 | 中 | `task_phase.py:20` CREATED 缺 `→FAILED` 迁移,planning 前异常产生永久 created 僵尸任务 |
| 4–5 | 中低 | `infer_supervisor_agent_hint` 英文动词缺失(`delete/remove/copy/move/open` 不路由 FileAgent);MockProvider 关键词优先级与 hint 路由冲突丢失 organize 审批语义 |
| 6–9 | 低 | `plan_matches_supervisor_hint` 恒真冗余;`is_transition_allowed` 死参数;非严格模式仍可抛异常;DocumentAgent fallback 缺参 |
| 10 | 低 | golden `_assert_output_spec` 对未知 spec 键静默跳过(typo 即 no-op) |
| 11–12 | 信息 | golden 断言整体扎实无恒真;skipped/cancelled 语义自洽 |

---

## 5. 优先修复建议

| 优先级 | 项 | 动作 |
|--------|-----|------|
| **P1** | 簇 B | `_create_plan_legacy_fallback` 改签名内省过滤 kwargs(或删除降级层+修测试桩),修复 4 个失败用例 |
| **P1** | 簇 C | `_schedule_background` 的 run 引擎循环改投递常驻 loop/线程,与请求生命周期解耦,修复 7 个失败用例 |
| **P1** | R7-H2 | 并行 stop 排空对已完成兄弟步骤先 write_back 再 cancel pending,消除重复副作用风险 |
| **P2** | 簇 A | `test_runs_api.py` 测试桩补 `task_metadata=None` 形参(4 个用例) |
| **P2** | 簇 D | `collect_release_evidence_packet.ps1` 客户端契约标记改指向 `mobile/src/api/client/` 新路径(20 个用例) |
| **P2** | R7-H1 | browser_activity 会话 TTL 清理 + 事件 deque(maxlen) |
| **P2** | 逻辑#2/#3 | 空 plan 走澄清路径;CREATED 增加 →FAILED 迁移 |
| **P3** | A1/A2/A3 | 三枚举下沉 core、delegation_metadata 解除对 route_engine 依赖、EngineRouteDecision 加结构化 rule 字段 |
| **P3** | R7-M1/M4 | 线程路径取消封送;审批/调度走 orchestrator_registry |
| **P4** | 逻辑#1 | 恢复 `calls==1` 断言 + retry 回灌越界反馈;A4–A5 重复表合并 |

---

## 6. 发布就绪门槛(R7 视角)

| 门槛 | 状态 | 说明 |
|------|------|------|
| 安全基线(SSRF/认证/沙箱) | ✅ | 无 Critical/High,历轮修复零回退 |
| 后端测试全绿 | ❌ | 35 失败(4 簇),其中 2 簇为产品侧问题 |
| Desktop 测试 | ✅ | 31/31 |
| 长稳内存(浏览器/事件) | ⚠️ | R7-H1/M7 无界增长未修 |
| 并行执行副作用安全 | ⚠️ | R7-H2 重复执行隐患 |
| 发布证据工具链 | ❌ | 簇 D 契约失配,packet 恒 fail-closed |

**结论:** 修完 P1 三项 + 簇 A/D 两项测试侧收口(预计一个 fix sprint 体量)后,后端可回到全绿并恢复 RC 评估;安全面当前即可支撑内测。

---

## 7. 附件索引

| 文件 | 说明 |
|------|------|
| `.cursor/audit-r7-final-report.md` | 本终报 |
| `.cursor/r7-lens-security.md` | 安全视角详表(88) |
| `.cursor/r7-lens-reliability.md` | 可靠性视角详表(72) |
| `.cursor/r7-lens-architecture.md` | 架构视角详表(71) |
| `.cursor/r7-lens-logic.md` | 逻辑视角详表(78)+ 遗留失败测试根因 |
| `.cursor/r7-fullsuite.log` | 后端全量 pytest 日志 |
| `.cursor/r7-desktop-vitest.log` | Desktop vitest 日志 |

---

## 8. 轮次关系

| 轮次 | 回答的问题 | 总评 |
|------|------------|------|
| R5 | 代码是否可靠、安全、可维护? | 74 / C+ |
| R6 | Agent 实际能做什么、与表述是否一致? | 68 / C |
| **R7** | **R6 修复落地后,全量审计 + 全量测试现状如何?** | **74 / C+(测试 98.1% 通过,4 失败簇已归因)** |

---

*Round 7 全量审计与测试 | 2026-06-12*
