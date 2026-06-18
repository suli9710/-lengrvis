# Round 4 审计报告 — mavris（Lengrvis）

审查对象：磁盘当前工作树（含未提交 R3 修复）。所有行数为本次实测。

---

## 第一节：架构（Architecture）

### R3 遗留核实

**ARCH-R4-01｜guardian 路由/审批逻辑镜像 —— R3遗留-PARTIAL（结构性收敛开始，镜像仍在且文件变大）**
- 证据：`backend/app/api/routes_guardian.py` 现为 **642 行**（R3 时 521，**+121**）；`routes_approvals.py` 现为 **212 行**（R3 时 179）。
- 改善面：guardian 已改为复用 full backend 的审批帮助函数 —— `routes_guardian.py:13` `from app.api.routes_approvals import _deny_rejected_step, _reconcile_runs, approval_execution_response`；reject 路径（`routes_guardian.py:117–122` vs `routes_approvals.py:34–39`）现在语义一致。
- 仍镜像：
  - 配对七端点逐行镜像 `routes_pair.py`（89 行）：`routes_guardian.py:65–102`；
  - `_load_approval_for_guardian_execution`（`routes_guardian.py:509–527`）是 `routes_approvals.approval_for_execution`（`routes_approvals.py:97–115`）的**逐行拷贝（约 19 行）**；
  - schedules / wakeups / mobile-wakeups 端点（`routes_guardian.py:171–271`，约 100 行）只存在于 guardian，无 full-backend 对应（`grep wakeup backend/app/api` 仅命中 guardian）；
  - 审批执行路径依旧双轨：guardian 走 `_wake_full_backend_for_approval`（HTTP 回调 `/api/runtime/approvals/{id}/continue`，`routes_guardian.py:424–446`），full backend 走 `OrchestratorAgent().execute_approved_step`（`routes_approvals.py:42–67`）。
- 量化：残余有效镜像约 **120–150 行**（R3 估 ~180–220），未做 router factory。
- 影响：双轨审批执行的行为漂移风险仍在，guardian 持续吸收新端点使重复面缓慢增长。

**ARCH-R4-02｜InMemoryRunStore 易失 —— R3遗留-PARTIAL（实质性改善：run 状态已落 SQLite 快照）**
- 仍无 `SqliteRunStore` 类；`InMemoryRunStore` + 全局 `default_run_store` 依旧（`backend/app/orchestration/execution_engine.py:35, 66`）。
- 但持久化路径已建立：每个 engine turn 将 `RunState` 序列化进 `runs.state` JSON 并 `db.upsert_model("runs", run)`（`run_service.py:391` → `_update_run_from_state` `:602–609` → `_update_run` `:708–716`）；`_state_from_run`（`:612–628`）优先从 DB 重建 `RunState`，内存 store 退化为 write-through 缓存；`resume_runs_for_task` / `_schedule_resume`（`:209–239`）可从持久态恢复 PAUSED / AWAITING_APPROVAL / 审批续跑的 run。
- plan 持久化已验证：`backend/tests/test_plan_persistence.py:25–50` 证明 `plans` 表 upsert 保留 `created_at`。
- 残留缺口：崩溃时 `RUNNING` 的 run **没有启动时自动恢复/标记**路径（恢复只覆盖 paused/awaiting）；`default_run_store` 全局单例仍阻碍多实例。
- 影响：重启不再"全部丢"，但 in-flight run 崩溃后将停留在陈旧 RUNNING 态，需人工触发恢复。

**ARCH-R4-03｜上帝模块 —— R3遗留-OPEN（恶化）**
- 实测行数（vs R3）：
  - `desktop/src/renderer/lib/apiClient.ts`：**6242 行**（5765，**+477**）；`renderer/lib/` 仅 5 个文件，未做任何域拆分；
  - `backend/app/core/db.py`：**1880 行**（1693，**+187**），无模块 docstring，schema/CRUD/审计 HMAC/settings hook 仍混居一文件（`db.py:1–60`）；
  - `backend/app/services/run_service.py`：**912 行**（787）；
  - `mobile/src/api/client.ts`：**1514 行**（1384）。
- 影响：两个极端模块持续膨胀，所有新功能（run 持久化、wakeup API）都在加剧而非缓解。

**ARCH-R4-04｜前端测试真空 —— R3遗留-OPEN**
- desktop：`*.test.ts(x)` 共 **0 个**；`desktop/package.json` 无 vitest/jest，`@playwright/test`（devDep L48）仍未被任何 script 引用——死依赖；测试形态仍是 15 个 smoke script（L15–29），且 **smoke 全家桶不在 CI**（`ci.yml` desktop job 只跑 typecheck，L78–79）。
- mobile：测试文件 **0 个**；新增的 `mobile/scripts/wakeup-contract-smoke.cjs` **没有接进 `mobile/package.json` scripts，也不在 CI**（ci.yml L96–101 只跑 token/task-companion/remote-input-grant 三个 smoke）——新代码新盲区。
- 影响：6242 行 apiClient + 1514 行 mobile client 的回归全靠 tsc 与人工。

**ARCH-R4-05（新发现）｜模块边界：services ↔ orchestration 双向依赖 + 私有符号跨模块导入**
- `orchestration → services` 模块级边：`orchestration/tool_runtime.py:53` 导入 `services.approval_event_service`（该模块本身不回导 orchestration，未成环，但打破了层方向）。
- `services → orchestration` 是主方向（`run_service.py:13–24`、`task_service.py:13–16` 等 6 处）。
- 规避环的代价可见：`routes_approvals.py:165–171, 192–209` 在函数体内延迟导入 `run_service` —— 潜在环的典型信号。
- `api` 层内部互导私有符号：`routes_runtime.py:5` 导入 `routes_approvals._execute_approved_step`（下划线私有函数跨文件复用）。
- 影响：层边界靠惯例与 lazy import 维持，重构任一模块都可能引爆隐式环。

**ARCH-R4-06（新发现）｜WS 路由双挂载未清理**
- `backend/app/main.py:244–254`：5 个 ws_router 各注册根路径 + `/api` 前缀共 **10 次** `include_router`；兼容垫片 `routes_pairing.py`（仍存在，re-export）。R3 已点名，无弃用时间表。

**ARCH-R4-07（新发现）｜配置管理：env 直读蔓延**
- `config.py` **733 行**；`LENGRVIS_*` 引用：`config.py` 约 100 处、`llm/registry.py` 约 100 处、`integrations/lengrvis_code.py` 75 处，散布 **35+ 模块** 直接 `get_env`/`os.environ`，绕过统一 settings 对象。`verify_release_safety.ps1:35` 甚至从外部 import `config.py` 的私有函数（`_configured, _find_config_file, _load_dotenv`）做发布检查。
- 影响：配置真值来源（env/.env/yaml/DB settings）四轨并存，发布安全检查与运行时取值逻辑可能漂移。

**ARCH-R4-08（新发现，正面）｜mobile WakeupsScreen 集成质量合格**
- `WakeupsScreen.tsx` 433 行：接入 `App.tsx:10,151–158,189`（独立 screen 状态）；`AuthExpiredError` → `onSessionExpired` 重新配对（`WakeupsScreen.tsx:56–65`）、刷新去抖、`safeDisplayText` 脱敏渲染。质量高于 R3 平均水位，扣分项仅是手写 screen 状态机（无导航框架）与零测试。

---

## 第二节：生产就绪（Production Readiness）

**PROD-R4-01｜desktop shell-quote critical —— R3遗留-OPEN**
- `desktop/package-lock.json:2675`：`shell-quote: 1.8.3`（经 `concurrently ^9.1.0`，devDep L54）。漏洞区间 ≤1.8.3，仍命中 critical。
- 加重因素：`.github/workflows/security-audit.yml`（每周一 cron，L6–7）声明 fail-closed（"any high-severity finding fails"）跑 `npm run audit:deps` —— **该定时任务当前必红**，等于安全门禁长期处于报警麻木状态。
- 影响：生产可达性低（仅 dev script），但供应链门禁信誉受损。

**PROD-R4-02｜Electron 关闭链 —— R3遗留-PARTIAL（小幅改善：kill 有超时了）**
- `desktop/src/main/main.ts:365–391`：`before-quit` 仍是 `preventDefault` + 异步 `backend.stop()` 后 `app.quit()`；**仍不调用** `enterBackground` / 后端 `prepare_for_background`（`run_service.py:150–167` 的 pause+drain 逻辑存在但退出路径不经过）。
- 改善：`terminateProcessTree` 现有 `PROCESS_EXIT_TIMEOUT_MS = 5000` 超时 + SIGKILL 兜底（`backendProcess.ts:427–475`），R3 报告的"taskkill 挂起则无限阻塞"已解除。
- 残留：Windows Service 托管后端退出时显式不停止（`backendProcess.ts:214–219`）；杀进程与后端 lifespan drain 的竞态仍在。

**PROD-R4-03（新发现）｜CI 门禁结构良好但有三个洞**
- `ci.yml`：hygiene + 依赖锁校验（L20–32）、全量 pytest `--maxfail=1` + golden task ≥95% 门（L53–56）、desktop/mobile typecheck、actions 按 SHA 固定（好）。
- 洞：① desktop 15 个 smoke 不在 CI；② mobile wakeup smoke 未接线（见 ARCH-R4-04）；③ npm audit 只在周度 scheduled job，PR 不挡——配合 PROD-R4-01，critical 漏洞可长期带病合并。无 lint（ruff/eslint）与覆盖率门。

**PROD-R4-04｜发布/签名链 —— R3遗留-PARTIAL（不变）**
- `electron-builder.yml:51` `verifyUpdateCodeSignature: true` + Fuses 全套（L18–24）保持；但 `backend.exe` 仍属 extraResources、不被自动签名，依赖"CI 先 signtool 再打包"的人工约定（L48–49 注释），仓库内**没有**对应 CI job 落实。`publish.repo: "-lengrvis"`（L36）与 README 仓库名一致，确认非笔误。
- `verify_release_safety.ps1`：检查 `LENGRVIS_ALLOW_MOCK_FALLBACK` / `LENGRVIS_STRICT_STATE_MACHINE` 双源（env+yaml）——门禁有效但面窄，未覆盖签名/audit。

**PROD-R4-05（新发现）｜测试金字塔：后端健康、跨栈倒置**
- `backend/tests/` **149 个测试文件**；其中约 **45 个**使用 `TestClient`（API/集成层），其余为单元/契约层——后端内部金字塔结构合理，并非过度依赖 e2e；关键路径（SSRF、取消排水、超时、lifespan、plan 持久化、orchestrator registry、run router registry）均有定向文件。
- 跨栈视角：backend 149 vs desktop 0 vs mobile 0（单测），倒置未变。

**PROD-R4-06（新发现，正面）｜文档可维护性中上**
- `README.md` 497 行，含平台支持矩阵、诚实的限制声明（"不是任务结果完成签收"等措辞，L18–19）、用户/诊断/隐私章节；`docs/user-guide.md`、合规清单被引用。扣分：核心模块（`db.py`、`config.py`、`run_service.py`）无模块级 docstring，新人导航依赖审计报告而非代码自述。

---

## 评分

### 架构评分：61 / 100（R3：58，+3）

| 维度 | 得分 | 理由 |
|------|------|------|
| 模块分解 | 13/30 | 上帝模块全线增长（apiClient +477 → 6242；db.py → 1880），零拆分动作 |
| API/路由层 | 12/20 | guardian 开始复用审批 helpers（+2），但 ~120–150 行镜像、双轨执行、WS 双挂载仍在 |
| 状态与数据 | 15/20 | **本轮最大进步**：RunState 逐 turn 落 `runs.state` + 可恢复路径 + plan 持久化测试；扣崩溃 RUNNING 无自动恢复、全局单例 store |
| 前端结构 | 7/15 | 0 单测、apiClient 继续膨胀；WakeupsScreen 集成质量良好（+1） |
| 跨切面一致性 | 14/15 | 层方向基本成立；扣 orchestration↔services 双向边与私有符号跨模块导入 |

加分主因是 run 持久化裂缝实质收窄；封顶因素是上帝模块**负增长趋势**——架构债不是停滞而是在复利。

### 生产评分：67 / 100（R3：64，+3）

| 维度 | 得分 | 理由 |
|------|------|------|
| 打包/发布 | 18/25 | Fuses + 更新签名校验保持；backend.exe 签名仍是纸面约定，无 CI 落实 |
| 生命周期/关闭 | 13/20 | kill 超时兜底落地（+2）；退出仍跳过 prepare_for_background drain |
| 依赖/供应链 | 11/20 | shell-quote critical 未修，且 fail-closed 周扫必红 = 门禁失能 |
| CI/质量门 | 16/20 | 全量 pytest + golden gate + 锁校验 + SHA 固定 actions 是真实门禁；扣 smoke 不进 CI、audit 不挡 PR |
| 可观测/交付 | 9/15 | run 状态可从 DB 排查（改善）；前端回归仍黑箱；wakeup smoke 未接线 |

**一句话总判：** R4 的真实进步集中在数据持久化与 CI 骨架，足以支撑内测扩大；但"上帝模块持续增肥 + 前端零单测 + 必红的安全周扫"三件事不解决，公开发布级（架构 ≥75 / 生产 ≥80）仍差一个专项重构 sprint。优先序建议：① `npm audit fix`（一小时级，恢复门禁信誉）→ ② mobile wakeup smoke 接入 CI → ③ apiClient.ts 域拆分 + Vitest 起步 → ④ guardian router factory → ⑤ 崩溃 RUNNING run 的启动恢复。
