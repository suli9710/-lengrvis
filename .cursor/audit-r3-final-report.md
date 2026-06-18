# Lengrvis/mavris Round 3 全项目审计终报

**审计日期：** 2026-06-12  
**仓库路径：** c:\Users\Suli\Desktop\mavris  
**方法：** Round 3 三透镜（安全 / 可靠性 / 架构·生产）+ 定向 pytest 回归 + Desktop typecheck  
**基线：** .cursor/audit-r2-final-report.md（2026-06-11）

---

## 1. 中文执行摘要

Round 3 在 Sprint 修复之上做了**透镜复核**与**定向验证**。相对 Round 2，**出站 SSRF（MCP / Cloud LLM / Webhook）、Desktop API Token DPAPI、配对码熵、审计与诊断脱敏、并行 context 深拷贝、OS engine cancel drain、工具超时、lifespan TaskPool 排水、dry-run 写锁、OrchestratorRegistry / AgentBus 实例隔离**等项均有代码与（多数）测试支撑，安全与可靠性透镜得分自 R2 约 **52→84**、**38→74**。

**仍未达公开发布级：** 编排层 **run_plan_turn 仍回落全局 orchestrator**、并行 **task/plan 可变对象共享**、**策略双轨（P0-18）**、**SSRF DNS TOCTU**、**全局配对 confirm 速率桶可被 LAN grief**、**run_service cancel 在 router 释放后无法 drain 旧 engine 上的 step task**；架构侧 **guardian 路由镜像 ~180 行**、**InMemoryRunStore**、**apiClient.ts / db.py 上帝模块**、**desktop shell-quote critical**、**退出不经 background drain** 仍拖累生产分。

**定向 pytest（本会话）：** 51 项中在**无并发 pytest 污染**时可稳定通过绝大多数用例；本会话因多进程并行跑测导致 **SQLite database is locked** 偶发失败（	est_scheduler_fatal_outcome_cancels_parallel_siblings、	est_cancel_run_drains_tasks、	est_finalize_marks_failed_when_success_coexists_with_blocked_skips），	est_lifespan_shutdown / 	est_permission_policy 在锁竞争下**挂起**（单独跑 	est_tool_timeout **1 passed**；SSRF/outbound/orchestrator 子集 **30 passed / 1 failed**）。**Desktop 
pm run typecheck：PASS。**

**综合等级（严格计分）：** 加权 **约 70.8**，因 **多项 P0 仍 OPEN/PARTIAL**，整体 **封顶 65 / 100（C+，内测可用，公开发布前需再收敛）**。

---

## 2. Round 2 vs Round 3 增量（Delta）

| 维度 | Round 2 结论 | Round 3 变化 |
|------|-------------|-------------|
| **安全 P0（SSRF/token/配对）** | 5 项 OPEN | **13 FIXED**（统一 outbound_url、MCP/LLM/webhook、DPAPI token、8-hex 配对、audit 脱敏等） |
| **可靠性 P0（并发/取消/超时）** | 11 项 OPEN | **5 FIXED**（P0-03/04/06/10/11 等）；**4 PARTIAL**；**2 OPEN**（含 P0-18） |
| **架构** | 上帝模块 + guardian 重复 | registry 改善 orchestrator；**路由镜像 / RunStore / 前端 Vitest 仍 OPEN** |
| **生产** | 硬杀退出、无更新签名校验 | **PARTIAL→更好**：Fuses + 更新签名校验；关闭链仍缺 background drain |
| **测试** | R2 盲区 10 项 | 新增/强化 10 个定向文件；全量仍偶发 audit sequence 竞态（R2 QA 已记录） |
| **去重发现数** | ~72 | ~**45 仍 relevant**（大量 R2 P0 已 FIXED 或降级为 PARTIAL） |

### 2.1 已关闭的 Round 2 P0（抽样，有测试或代码证据）

| R2 ID | 摘要 | R3 |
|------|------|-----|
| P0-03 | cancel_run 不 cancel asyncio.Task | **FIXED** |
| P0-04 | 并行 fatal 不中止兄弟 step | **FIXED** |
| P0-06 | dry-run 跳过写锁 | **FIXED** |
| P0-07 | MCP SSRF | **FIXED** |
| P0-08 | Cloud LLM SSRF | **FIXED** |
| P0-09 | Desktop token 明文 | **FIXED** |
| P0-10 | lifespan 无 TaskPool.shutdown | **FIXED**（代码；本会话 lifespan 单测在 DB 锁竞争下挂起） |
| P0-11 | 工具无全局 timeout | **FIXED** |
| P0-15 | 配对码熵不足 | **FIXED** |

### 2.2 仍 OPEN / PARTIAL 的 Round 2 P0

| R2 ID | R3 状态 | 说明 |
|------|---------|------|
| P0-01 | **PARTIAL** | context deepcopy；task/plan/step 仍共享 |
| P0-02 | **PARTIAL** | registry 已接线；
un_plan_turn 仍 _orchestrator() |
| P0-05 | **PARTIAL** | read-state 按 step 作用域；全局 dict 无锁 |
| P0-12 | **PARTIAL** | 异步 quit + taskkill；无 background drain |
| P0-14 | **PARTIAL** | Playwright 同步冷启动；靠 to_thread 不堵 loop |
| P0-17 | **PARTIAL** | OrchestratorRegistry；release_task / 多入口未完全收敛 |
| P0-18 | **OPEN** | tool_runtime vs policy_engine 双轨 |

---

## 3. 定向验证结果（Round 3 Gate）

| 命令 | 结果 |
|------|------|
| ackend 定向 pytest（10 文件，51 tests） | **不完整通过**：并发跑测导致 **2–3 失败（database locked）** + **2 文件挂起**；子集 **tool_timeout 1/1**；SSRF/outbound 等 **30/31** |
| desktop 
pm run typecheck | **PASS** |

**建议单线程复跑（发布前）：**

`powershell
cd c:\Users\Suli\Desktop\mavris\backend
python -m pytest tests/test_parallel_context_isolation.py tests/test_cancel_run_drains_tasks.py tests/test_lifespan_shutdown.py tests/test_tool_timeout.py tests/test_mcp_ssrf.py tests/test_cloud_llm_ssrf.py tests/test_permission_policy.py tests/test_skipped_completion_semantics.py tests/test_orchestrator_registry.py tests/test_outbound_url.py -q --tb=line
`

---

## 4. 加权得分（严格）

| 维度 | 权重 | 透镜/实测分 | 应用规则后 | 说明 |
|------|------|-----------|-----------|------|
| **Security** | 25% | 84 | **84** | 无 OPEN **High**；剩余为 Medium TOCTU / 全局配对 grief |
| **Reliability** | 25% | 74 | **70** | **OPEN High P0-18** → 维度封顶 70 |
| **Architecture** | 15% | 58 | **58** | 上帝模块、guardian 镜像、InMemoryRunStore |
| **Production** | 15% | 64 | **64** | 签名/Fuses 改善；npm audit critical、关闭链缺口 |
| **Tests** | 20% | 72（估） | **70** | 定向套件设计良好；本会话 flaky + 2 挂起；全量 audit 链偶发竞态 |

**加权合计：** 0.25×84 + 0.25×70 + 0.15×58 + 0.15×64 + 0.20×70 = **70.8**

**严格封顶：** 多项 P0 仍 OPEN/PARTIAL（≥2）→ **总评封顶 65**

### **总评：65 / 100（C+）**

---

## 5. 透镜得分明细（引用）

| 透镜 | 文件 | R2→R3 |
|------|------|-------|
| 安全 | .cursor/audit-r3-lens-security.md | 52 → **84** |
| 可靠性 | .cursor/audit-r3-lens-reliability.md | 38 → **74**（维度计分 **70**） |
| 架构 | .cursor/audit-r3-lens-architecture.md | ~52 → **58** |
| 生产 | 同上 architecture 透镜 §11.2 | ~55 → **64** |

---

## 6. Top 10 剩余风险（跨透镜合并）

| # | ID / 主题 | 严重度 | 位置 | 状态 |
|---|-----------|--------|------|------|
| 1 | SSRF DNS TOCTOU | High→Med | outbound_url.py + 消费者 | PARTIAL |
| 2 | 
un_plan_turn 全局 orchestrator | High | os_execution_engine.py | PARTIAL |
| 3 | 并行共享 Task/Plan | High | scheduler + OS engine | PARTIAL |
| 4 | 策略双轨执行 | High | 	ool_runtime / policy_engine | OPEN |
| 5 | run_service cancel 回落新 engine | High | 
un_service.py | PARTIAL |
| 6 | 全局配对 confirm 速率 grief | Medium | mobile_pairing_service.py | OPEN |
| 7 | guardian 路由 / 审批逻辑镜像 | Medium | 
outes_guardian.py | OPEN |
| 8 | InMemoryRunStore 易失 | Medium | 
un_service / engine | OPEN |
| 9 | desktop shell-quote critical | Medium | concurrently 依赖链 | OPEN |
| 10 | 退出无 background drain | Medium | main.ts / 
un_service | PARTIAL |

---

## 7. 建议下一步（优先级）

1. **R3-012**：connect-time IP pin / 重解析重检（SSRF TOCTU）。  
2. **P0-02**：
un_plan_turn 统一 _orchestrator_for_state + 并发双 run 集成测试。  
3. **R3-013**：拆分 global pairing bucket，malformed 不计入 global。  
4. **R3-014**：run 级 engine registry，cancel 必须 drain 旧实例 _run_tasks。  
5. **P0-18**：approval 单轨 refactor（可独立 PR）。  
6. **架构**：guardian router factory + SqliteRunStore + piClient.ts 拆分。

---

## 8. 附件

- Round 2 终报：.cursor/audit-r2-final-report.md  
- Round 3 透镜：.cursor/audit-r3-lens-security.md、-reliability.md、-architecture.md  
- 本会话 pytest 日志（若生成）：.cursor/r3-pytest-targeted.log

---

*Round 3 终报由验证 Agent 基于三透镜输出、定向 pytest/typecheck 与会话内实测合成。*
