# Lengrvis/mavris Round 4 全项目审计终报

**审计日期：** 2026-06-12
**仓库路径：** c:\Users\Suli\Desktop\mavris
**审计对象：** 工作树当前磁盘代码（含约 1900 行未提交的 R3 修复冲刺产物，57 文件）
**方法：** 三透镜（安全 / 可靠性 / 架构·生产）+ 逐文件隔离 pytest 实测 + 最小复现验证 + typecheck + npm audit
**基线：** `.cursor/audit-r3-final-report.md`（2026-06-12，总评 65/C+）

---

## 1. 中文执行摘要

Round 4 的核心结论：**R3 报告对自身修复的验证结论不成立**。R3 把 5 个挂起的测试文件归因于"并发 pytest 的 SQLite 锁竞争"，本轮逐文件隔离实测 + 最小复现证明那是一个 **R3 修复冲刺自己引入的 Critical 自死锁回归**（R4-C1）：未设置 `LENGRVIS_AUDIT_HMAC_SECRET` 时，**任何进程的第一条审计写入都会永久死锁**，且死锁线程持有 SQLite `BEGIN IMMEDIATE` 写事务，锁死整库。Desktop 生产启动链不设置该环境变量 → **按当前工作树打包的应用，后端会在首次审计事件时整体冻结**。

同时，即便绕开死锁（设置环境变量），仍有 **3 个守护 R3 P0 修复的测试确定性失败**（`database is locked`）——R3 标记为 FIXED 的"取消排水 / 并行 fatal 取消 / skipped 完成语义"实际处于**未验证状态**。

正面进展是真实存在的：安全侧 R3 的全部 High（SSRF 基础校验、DPAPI token、配对熵、脱敏）经独立复核确认 FIXED，无 OPEN High/Critical；run 级 engine registry、scheduler 排水落地并有测试；RunState 逐 turn 落 SQLite，"重启全丢"实质收窄；CI 门禁骨架（全量 pytest + golden gate + SHA 固定 actions）成形。

**综合等级（严格计分）：加权 63.5，OPEN Critical 封顶 60 → 总评 60 / 100（C），较 R3 的 65 回落。** 回落不是因为代码整体变差，而是 R4 实证推翻了 R3 评分中"测试偶发失败可忽略"的前提。

---

## 2. 实测验证结果（Round 4 Gate）

| 命令 | 结果 |
|------|------|
| 定向 pytest（15 文件，逐文件隔离，无环境变量） | **3 文件失败**（database is locked）+ **5 文件挂起**（test_lifespan_shutdown、test_permission_policy、test_state_machine_nonstrict、test_mobile_pairing、test_lan_api_guard，120s 超时）+ 7 文件通过 |
| 同套件，设置 `LENGRVIS_AUDIT_HMAC_SECRET` 后 | **138 passed / 3 failed**（挂起全部消失 → 死锁根因确认） |
| 最小复现（init_db + 首条审计写入，无环境变量） | **DEADLOCK CONFIRMED**（8s 无返回） |
| faulthandler 堆栈转储 | 挂在 `db.py:1618 _audit_hmac_secret` ← `db.py:1583 _prepare_audit_event_locked`（同一把锁） |
| desktop `npm run typecheck` | **PASS** |
| mobile `npm run typecheck` | **PASS** |
| desktop `npm audit` | 生产依赖 **0**；开发链 **2 critical**（shell-quote via concurrently，R3 遗留） |

仍确定性失败的 3 个测试（设置环境变量后）：

- `test_parallel_context_isolation.py::test_scheduler_fatal_outcome_cancels_parallel_siblings`
- `test_cancel_run_drains_tasks.py::test_cancel_run_cancels_registered_parallel_step_tasks`
- `test_skipped_completion_semantics.py::test_finalize_marks_failed_when_success_coexists_with_blocked_skips`

失败堆栈一致：取消路径上 `step_phase.py:75` 同步写 `step.invalid_transition_audited` 审计事件 → `db.py:1168 BEGIN IMMEDIATE` 撞上被另一连接持有超过 busy_timeout(5s) 的写事务。

---

## 3. 本轮最高优先级发现

### R4-C1（Critical，新发现）：审计 HMAC 锁自死锁，锁死整库 —— R3 冲刺引入的回归

- **证据：** `backend/app/core/db.py:1565`（`_prepare_audit_event_locked` 获取非重入 `_AUDIT_CACHE_LOCK`，threading.Lock，`db.py:28`）→ `:1583` 在持锁状态调用 `_audit_event_hmac` → `_audit_hmac_secret` 在 `:1618` 再次请求同一把锁 → **永久自死锁**。
- **触发条件：** 进程内 `_AUDIT_SECRET_CACHE` 为空（即每个新进程的第一条审计写入）且未设置 `LENGRVIS_AUDIT_HMAC_SECRET`。与秘密文件是否已存在**无关**——死锁发生在读缓存那一行。
- **爆炸半径：** 死锁线程在 `db.py:1168` 已持有 `BEGIN IMMEDIATE` 写事务 → 其他所有写连接 5s 后报 `database is locked`；`audit.record()` 是同步调用，发生在事件循环线程时**整个后端冻结**。
- **生产可达性：** `desktop/src/main/backendProcess.ts` 启动后端时只注入 TOKEN/CONFIG_DIR/DATA_DIR，**不设置** `LENGRVIS_AUDIT_HMAC_SECRET`；`ci.yml` 同样不设置（全量 pytest 在此工作树上会挂到 90min 超时）。
- **回归定位：** git HEAD 的 `_prepare_audit_event_locked` 在**锁外**计算 HMAC（无死锁，CI 绿是真的）；工作树版本为修复"审计 sequence 竞态"把锁作用域扩大到覆盖 HMAC 计算，引入死锁。**即 R3 修复冲刺以一个 Critical 换掉了一个 Medium。**
- **修复（一行级）：** 在进入 `_AUDIT_CACHE_LOCK` 前预取 `secret = _audit_hmac_secret()`，或将该锁改为 `threading.RLock()`。修复后必须把 5 个挂起文件重新跑绿。

### R4-C2（High，新发现）：取消/并行路径审计写入与长持写事务冲突，R3 三项 P0 修复处于未验证状态

- **证据：** 上节 3 个确定性失败；`step_phase.py:75` 在取消风暴中同步逐条写审计，而另一连接的写事务持续 >5s。
- **影响：** 不仅是测试红——生产中取消并行 run 时同样会丢审计事件（抛 OperationalError），且这 3 个测试正是 R3 标记 FIXED 的 P0-03/P0-04/skipped 语义的守护测试。**在它们跑绿之前，R3 的这三项 FIXED 不能采信。**
- **方向：** 单进程内审计/run_events 写串行化（写队列）或取消路径批量写；R3 报告 R4-M4 已建议，未落地。

---

## 4. 三透镜结论摘要（全文见附件）

### 4.1 安全（83/100，R3：84）

- R3 全部 High 确认 FIXED：MCP/LLM/Webhook SSRF 基础校验、DPAPI token（`localSecret.ts` + `local_secret.py` 原子写实现良好）、配对熵+限速、audit/诊断脱敏、中间件 fail-closed、Electron 三件套 + CSP + 更新签名校验、mobile SecureStore。**无 OPEN High/Critical。**
- 仍 OPEN（Medium×3）：① SSRF DNS TOCTOU 无 connect-time IP pin（`outbound_url.py:57-72`）；② 全局配对 confirm 桶可被 LAN 用"格式正确的错误码"灌满 grief（`mobile_pairing_service.py:1054-1074`，成功从不清全局桶）；③ 策略双轨（tool_runtime 自带判定 vs PolicyEngine/PermissionStore，无 bypass 但易漂移）。

### 4.2 可靠性（透镜原始 76 → 维度计分 50，OPEN Critical 封顶；R3：74/70）

- 本轮关闭：run 级 engine registry + cancel 复用同一 router（`run_service.py:423-438`，有测试）；scheduler stop 排水（30s）；工具/LLM 超时完备。
- 仍 OPEN High：**R4-H1** 并行 step 共享可变 Task/Plan/PlanStep（P0-01 连续三轮未动，唯一可直接导致状态损坏的项）；**R4-H3（新发现）** resume 路径 orchestrator/bus 失配——fresh engine 新建 orchestrator 用新 bus，bridge 仍订阅 registry 中旧 bus，**resume 后实时事件流静默断流**（DB 尾扫部分兜底）。
- PARTIAL：run_plan_turn 全链仍走 `self._orchestrator()` 单字段（生产被 per-run engine 缓解，无断言保护、无并发双 run 测试）；lifespan 不 drain run_service 引擎 loop（`loop.create_task` 不在 TaskPool）；desktop 退出硬杀不经 `prepare_for_background`。
- 新增 Medium：WS 早连 fallback bus 失配（`routes_chat.py:51`）、`orchestrator_registry._by_task` 永不释放（内存泄漏）、AgentBus 同步 DB 写堵事件循环。

### 4.3 架构（61/100，R3：58）与生产（67/100，R3：64）

- **实质进步：** RunState 逐 turn 落 `runs.state` + 可恢复路径 + plan 持久化测试（"InMemoryRunStore 易失"实质收窄）；guardian 开始复用审批 helpers；CI 骨架（全量 pytest --maxfail=1 + golden ≥95% 门 + 依赖锁校验 + SHA 固定 actions）。
- **恶化项：** 上帝模块复利增长——`apiClient.ts` 6242 行（+477）、`db.py` 1880 行（+187）、`run_service.py` 912 行、`mobile/client.ts` 1514 行，零拆分。
- **仍 OPEN：** guardian 残余镜像 ~120–150 行 + 审批双轨执行；前端 0 单测（desktop/mobile），新增 wakeup smoke 未接 CI；shell-quote critical 未修且**每周安全扫描 job 必红**（门禁报警麻木）；backend.exe 签名仍是纸面约定；崩溃 RUNNING run 无启动自动恢复；env 直读蔓延 35+ 模块。

---

## 5. R3 → R4 状态迁移表（关键项）

| 项目 | R3 结论 | R4 实证 |
|------|---------|---------|
| 审计写入路径 | "测试偶发锁竞争，可忽略" | **Critical 自死锁回归（R4-C1）**，最小复现确认 |
| P0-03 cancel drain | FIXED | **代码在、守护测试红 → 未验证** |
| P0-04 并行 fatal 取消 | FIXED | **守护测试红 → 未验证** |
| R3-014 run 级 engine registry | 建议 | **FIXED**（有绿测试） |
| P1-08 scheduler 排水 | OPEN | **FIXED** |
| P0-01 并行共享 Task/Plan | PARTIAL | **OPEN**（未动，R4-H1） |
| P0-02 run_plan_turn 全局 orchestrator | PARTIAL | **PARTIAL**（生产缓解，代码未收敛） |
| R3-012 SSRF TOCTOU | PARTIAL | **OPEN**（无 pin，降级评估为 Medium 维持） |
| R3-013 全局配对桶 grief | OPEN | **OPEN** |
| P0-18 策略双轨 | OPEN | **OPEN**（确认无直接 bypass，降为 Medium） |
| InMemoryRunStore 易失 | OPEN | **PARTIAL**（runs.state 快照 + 恢复路径） |
| shell-quote critical | OPEN | **OPEN**（且周扫门禁必红） |
| 桌面退出硬杀 | PARTIAL | **PARTIAL**（kill 有 5s 超时兜底，仍无 drain） |

---

## 6. 加权得分（严格）

| 维度 | 权重 | 透镜/实测分 | 应用规则后 | 说明 |
|------|------|-----------|-----------|------|
| **Security** | 25% | 83 | **83** | 无 OPEN High/Critical；剩余 3×Medium |
| **Reliability** | 25% | 76 | **50** | **OPEN Critical R4-C1 → 维度封顶 50** |
| **Architecture** | 15% | 61 | **61** | 持久化进步 vs 上帝模块复利 |
| **Production** | 15% | 67 | **67** | CI 骨架成形；当前树不可发布由总封顶体现 |
| **Tests** | 20% | — | **55** | 3 个 P0 守护测试确定性红；无环境变量时套件挂起不可跑；前端 0 单测 |

**加权合计：** 0.25×83 + 0.25×50 + 0.15×61 + 0.15×67 + 0.20×55 = **63.5**

**严格封顶：** 工作树存在 OPEN Critical（R4-C1，生产首条审计写入即冻结）→ **总评封顶 60**。

### **总评：60 / 100（C）**（R3：65/C+ → 回落 5 分）

> 回落解读：R3 的 65 分建立在"修复已被测试验证"的前提上；R4 实证表明验证链本身被 R4-C1 污染（5 文件挂起被误读为环境噪音），且 3 项 P0 修复的守护测试在干净环境下确定性失败。扣分扣在"已声称但未成立的可靠性"上。修复 R4-C1（一行级）+ 跑绿 3 个红测试后，预期可回到 70+。

---

## 7. 建议下一步（优先级排序）

1. **R4-C1（小时级）：** `_prepare_audit_event_locked` 进锁前预取 HMAC secret（或 `_AUDIT_CACHE_LOCK` 改 RLock）；随后**不设环境变量**重跑 5 个挂起文件确认全绿；CI 与 desktop 启动链显式注入/生成 audit secret 作为纵深。
2. **R4-C2：** 单进程审计/run_events 写串行化（asyncio 写队列或线程安全批量写），跑绿 3 个红测试，恢复对 P0-03/04/skipped 语义 FIXED 的采信。
3. **npm audit fix**（小时级）：解除 shell-quote critical，恢复每周安全扫描门禁的信号价值。
4. **R4-H3：** `_orchestrator_for_state` 接入全局 orchestrator_registry 并在 resume 时回绑 bus（改动小、止住 resume 后事件断流）。
5. **R4-H1（P0-01 收口）：** 并行 batch 改 plan 快照 + 串行写回，补 task/plan 隔离测试——这是连续三轮未动的最后一个状态损坏级 OPEN High。
6. **门禁补线：** mobile wakeup smoke + desktop smoke 接入 CI；npm audit 挡 PR。
7. **架构专项（R5 前置）：** apiClient.ts 域拆分 + Vitest 起步；guardian router factory；崩溃 RUNNING run 启动恢复。

---

## 8. 附件

- 安全透镜：`.cursor/audit-r4-lens-security.md`
- 可靠性透镜：`.cursor/audit-r4-lens-reliability.md`
- 架构/生产透镜：`.cursor/audit-r4-lens-architecture.md`
- 逐文件 pytest 输出：`.cursor/r4-test_*.out`；挂起堆栈：`.cursor/r4-hang-stack.err`
- 基线：`.cursor/audit-r3-final-report.md`

---

*Round 4 终报由验证 Agent 基于三透镜独立输出、逐文件隔离 pytest、最小死锁复现与 HEAD 对比分析合成。所有 Critical/High 结论均有会话内可复跑的实证命令支撑。*
