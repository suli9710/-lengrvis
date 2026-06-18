# Lengrvis/mavris Round 8 复审终报 — R7 修复验证 + 全量测试

**审计日期：** 2026-06-12
**仓库路径：** c:\Users\Suli\Desktop\mavris（工作树，基线提交 `4be1077c`）
**审计类型：** R7 发现项逐项修复验证 + 后端全量 pytest + Desktop vitest
**基线：** `.cursor/audit-r7-final-report.md`（74/C+，35 个测试失败）

---

## 1. 中文执行摘要

R8 是 R7 修复落地后的复审。**R7 全部 P1/P2 项均已修复并通过代码核验与测试验证：后端失败用例从 35 → 3（且 3 个全部为测试侧过期，无产品缺陷），通过率 99.8%；Desktop 31/31 全绿。**

唯一的新增失败是 R7 逻辑#3 修复（`CREATED→FAILED` 迁移）的可预见连带效应：3 个状态机测试仍用 `CREATED→ROLLED_BACK` 当"非法迁移"的典型样例，而 `ROLLED_BACK` 在 phase 模型中本就是 `FAILED` 的别名（`schemas.py:73`），修复后该迁移变为合法。属测试债，非回归。

**综合评分：79 / 100（B-）** — 较 R7 +5：可靠性与逻辑面修复落地，测试基本回绿；架构债（P3/P4）按计划未动。

| 维度 | R7 | R8 | 变化说明 |
|------|----|----|----------|
| 安全 Security | 88 | **88** | 未触及，无回退 |
| 可靠性 Reliability | 72 | **80** | H1/H2/簇C/常驻 loop 全部落地 |
| 架构 Architecture | 71 | **71** | A1/A2/A3 等 P3 项未动（符合排期） |
| 逻辑 Logic | 78 | **82** | 空 plan 澄清路径、CREATED→FAILED 修复 |
| 测试通过率 | 98.1% | **99.8%** | 3/1809 失败，全部测试侧 |

---

## 2. 测试执行结果

### 2.1 后端全量 pytest

| 项 | 值 |
|----|-----|
| 命令 | `python -m pytest -q --timeout=180 --timeout-method=thread`（独立 LENGRVIS_DATA_DIR） |
| 结果 | **4 failed / 1805 passed / 3 skipped**，738s（12:17） |
| 复跑确认 | `test_runs_api.py::test_auto_routing_uses_os_for_write_intent_code_goal` 单独复跑**通过**（全量跑期间该测试文件被并行编辑，收集到旧版本断言；当前磁盘版本与产品行为一致）。**有效失败数：3** |
| 日志 | `.cursor/r8-fullsuite.log` |

### 2.2 Desktop vitest

**31 passed / 0 failed**（2 个文件，271ms）。日志：`.cursor/r8-desktop-vitest.log`。

---

## 3. R7 发现项修复验证（逐项核验代码）

### 3.1 已修复 ✅（全部 P1/P2）

| R7 项 | 验证位置 | 验证结论 |
|-------|----------|----------|
| 簇 B（P1）planner 降级逻辑破损 | `planning_handler.py:35-41` | 改为 `inspect.signature` 内省过滤 kwargs（`_filter_planner_kwargs`），TypeError 字符串嗅探已删除；4 个用例回绿 |
| 簇 C（P1）run 引擎绑定请求级 loop | `run_service.py:49-73,1035-1046` | 新增进程级常驻 loop（`_ensure_background_loop`，独立线程 `run-service-engine-loop`），`_schedule_background` 改 `run_coroutine_threadsafe`；7 个用例回绿 |
| R7-H2（P1）并行 stop 丢已完成结果 | `os_execution_engine.py:687-705` | stop 后按 `_drain_running_after_stop` 范本排空：已完成兄弟步骤先 `write_back_step` 并保留 observation，再处理 cancelled |
| 簇 A（P2）测试桩缺 `task_metadata` | `test_runs_api.py:264,854,1092,1220` | 所有 stub `start_run` 已补 `*, task_metadata=None`；4 个用例回绿 |
| 簇 D（P2）release 契约指向旧路径 | `collect_release_evidence_packet.ps1:637` | 客户端契约改扫 `mobile/src/api/client/endpoints.ts`；20 个用例回绿 |
| R7-H1（P2）browser_activity 内存无界 | `browser_activity_runtime.py:223-251` | 事件改 `deque(maxlen=2000)`；会话 TTL 清理（closed 1h / stale 24h）+ 200 上限溢出裁剪 |
| 逻辑#2（P2）空 plan 语义冲突 | `planning_handler.py:205-210` | 原生空 plan 视为合法澄清式回复直接放行；仅"剥离越界工具后变空"才抛 `SupervisorHintPlanError`，与 matcher 语义一致 |
| 逻辑#3（P2）CREATED 缺 →FAILED | `task_phase.py:20` | `CREATED → {GOAL_ANALYSIS, PLANNING, FAILED, CANCELLED}`，planning 前异常不再产生僵尸任务 |

### 3.2 未修复（原 P3/P4，符合排期，非回退）

| R7 项 | 现状 |
|-------|------|
| A1 agents↔orchestration 双向依赖 | `delegation_metadata.py:6` 仍 import `route_engine` |
| A2 core 依赖倒置 | `core/schemas.py:10-12` 仍 import orchestration 三枚举 |
| A3 reason 字符串当机器契约 | `delegation_metadata.py:55`、`task_service.py:71` 仍 `"system diagnostics" in route.reason` |
| 逻辑#1 hint-retry 无反馈回灌 | `planning_handler.py:163-176` 两轮入参仍相同；`test_supervisor_chat_flow.py:242` 仍 `1 <= calls <= 2` |
| 逻辑#4 英文动词不路由 FileAgent | `delegation_metadata.py:73` 仍只有中文触发词，`delete/remove/copy/move/open` 缺失 |
| R7-M4 审批/调度绕过 orchestrator_registry | `routes_approvals.py` 无 orchestrator_registry 引用 |
| R7-L4 `_fired_ids` 无界 | `scheduler_service.py:64` 仍为裸 set |
| A4 关键词路由表 4 处重复 | 未合并 |

---

## 4. 新增发现（R8 唯一新失败簇）

### R8-1【测试债|P2】状态机测试以 `CREATED→ROLLED_BACK` 为非法迁移样例 — 3 个用例

- 用例：`test_state_machine_integration.py::test_safe_transition_no_longer_forces_invalid_transition`、`::test_safe_transition_strict_raises_invalid_transition`、`test_state_machine_nonstrict.py::test_invalid_transition_nonstrict_does_not_persist_bad_status`
- 根因：`TaskStatus.ROLLED_BACK` 是 `TaskPhase.FAILED` 的别名（`schemas.py:73`，legacy 映射 `schemas.py:43`）。逻辑#3 修复合法化了 `CREATED→FAILED`，于是 `CREATED→ROLLED_BACK` 等价合法，三个测试的"非法迁移"前提失效。
- 定性：**测试侧过期，产品行为正确**（planning 前异常应能把任务置为 FAILED；状态机非严格模式审计、严格模式抛错的分支逻辑本身经核验无误，`state_machine.py:65-102`）。
- 修复建议：样例改用仍然非法的迁移，如 `CREATED→COMPLETED`（同文件 `test_transition_raises_typed_error_on_invalid_phase_transition` 用 `EXECUTING_STEP` 的写法可参照）。
- **修复状态（报告后追记）：已修复并验证。** 三个测试样例均已改为 `CREATED→COMPLETED`；复跑 `test_state_machine_integration.py + test_state_machine_nonstrict.py + test_runs_api.py` 共 37 passed / 0 failed。**后端全量有效失败数归零，"后端测试全绿"门槛达标，可进入 RC 评估。**

---

## 5. 优先修复建议（R8 视角）

| 优先级 | 项 | 动作 |
|--------|-----|------|
| **P2** | R8-1 | 3 个状态机测试换非法迁移样例（`CREATED→COMPLETED`），后端即全绿 |
| P3 | A1/A2/A3 | 三枚举下沉 core；delegation_metadata 解除 route_engine 依赖；EngineRouteDecision 加结构化 rule 字段 |
| P3 | 逻辑#4 + A4 | 意图触发词收敛单一注册表，补英文动词 |
| P3 | R7-M4 | 审批/调度走 orchestrator_registry，恢复实时流 |
| P4 | 逻辑#1 | retry 回灌越界反馈 + 恢复 `calls==1` 断言 |

---

## 6. 发布就绪门槛（R8 视角）

| 门槛 | R7 | R8 | 说明 |
|------|----|----|------|
| 安全基线（SSRF/认证/沙箱） | ✅ | ✅ | 无回退 |
| 后端测试全绿 | ❌ 35 失败 | ⚠️ 3 失败 | 全部测试侧，修样例即绿 |
| Desktop 测试 | ✅ | ✅ | 31/31 |
| 长稳内存（浏览器/事件） | ⚠️ | ✅ | H1 已修（deque+TTL） |
| 并行执行副作用安全 | ⚠️ | ✅ | H2 已修（先 write_back 再 cancel） |
| 发布证据工具链 | ❌ | ✅ | 簇 D 契约路径已更新 |

**结论：** 修掉 R8-1 的 3 个测试样例后后端全绿，**可进入 RC 评估**。剩余债务集中在架构分层（P3）与意图路由覆盖（P3），不阻塞内测发布。

---

## 7. 附件索引

| 文件 | 说明 |
|------|------|
| `.cursor/audit-r8-final-report.md` | 本终报 |
| `.cursor/r8-fullsuite.log` | 后端全量 pytest 日志 |
| `.cursor/r8-desktop-vitest.log` | Desktop vitest 日志 |

---

## 8. 轮次关系

| 轮次 | 回答的问题 | 总评 |
|------|------------|------|
| R5 | 代码是否可靠、安全、可维护？ | 74 / C+ |
| R6 | Agent 实际能做什么、与表述是否一致？ | 68 / C |
| R7 | R6 修复落地后，全量审计 + 全量测试现状如何？ | 74 / C+ |
| **R8** | **R7 的 P1/P2 修复是否真实落地？** | **79 / B-（修复全部验证通过，测试 99.8%）** |

---

*Round 8 复审 | 2026-06-12*
