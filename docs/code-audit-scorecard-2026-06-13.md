# Lengrvis 全项目代码审计评分卡与优化路线图（2026-06-13）

> 审计日期：2026-06-13
> 范围：`backend/app`（66,129 行 Python / 392 文件）、`desktop/src`（31,834 行 TS/TSX）、`mobile`（Expo）、CI/依赖/测试体系
> 方法：在 2026-06-11 性能/质量评审（`docs/code-review-2026-06-11.md`）与 2026-06-12 安全审计（`docs/code-audit-2026-06-12.md`，含本轮 SEC-001~012 修复）基础上，补充全局度量并跨维度打分
> 评分口径：每维度 0–10；总分按"安全/正确性/测试"加权（OS Agent 产品的高危属性）

---

## 总评分卡

| 维度 | 评分 | 等级 | 一句话依据 |
| --- | --- | --- | --- |
| 安全 (Security) | 8.5 | A- | HMAC 审批绑定 + R0–R4 + model boundary + DPAPI + 审计哈希链 + SSRF IP 钉死 + 移动 scope 隔离；本轮 SEC-001~012 进一步加固；仍属发布前、未经外部渗透。 |
| 正确性/可靠性 | 7.0 | B | 1,562 后端测试 + 黄金任务门禁 + 状态机；但同步阻塞事件循环、崩溃/更新管线未闭环、若干 open follow-up（ORCH-002/003）。 |
| 性能/可伸缩性 | 5.5 | C+ | **最大短板**：工具执行 / SQLite / ONNX / OCR 在 async 事件循环线程内同步阻塞；SQLite 每操作建连、无 WAL、`init_db()` 热路径重复执行。 |
| 架构/可维护性 | 7.0 | B | 分层清晰、38 个外置 prompt、能力解耦良好；但存在巨型文件（`db.py` 1919、`context_management.py` 1780、`SettingsPanel.tsx` 3362、`App.tsx` 1835）。 |
| 代码卫生 (Quality) | 6.0 | C+ | 无 mypy；ruff 未做强制门禁，存量 **1,024** 项（790 E501、383 BLE001、54 F401 未用导入）、158 处 `noqa`。 |
| 测试 (Testing) | 8.0 | A- | 后端 1,562 用例 + 黄金任务 + desktop Vitest/Playwright + mobile smokes + CI 矩阵；缺真机/clean-machine/性能测试与覆盖率门禁。 |
| 供应链/依赖 | 7.5 | B+ | `requirements-lock.txt` 锁定 + CI `npm audit --audit-level=high` 阻断 + 每周 `security-audit.yml` + pip-audit；`requirements.txt` 用 `>=`（由 lock 收敛）。 |
| 文档/工程化 | 7.5 | B+ | README/SECURITY/QA 矩阵/证据 helper/合规清单非常详尽；但仅 Windows 开发路径，非 Windows（如本审计环境）大量用例因平台能力失败。 |
| 前端 (Desktop/Mobile) | 7.0 | B | Electron 三件套 + 严格 CSP + IPC 白名单一流；但渲染层巨型组件、轮询+推送双通道残留。 |

### 综合得分：**7.3 / 10（等级 B+）**

**总评**：项目的**安全设计基线明显高于同类发布前原型**，测试与工程化体系扎实；本轮安全加固后高危面进一步收敛。制约总分的是两类**系统性、跨文件**的工程债：(1) **异步事件循环被同步阻塞**导致的吞吐/可靠性风险；(2) **静态检查链缺失**（无 mypy、ruff 未门禁）导致的存量卫生债与潜在隐性 bug（383 处 blind-except 会吞错误）。这两项是后续提分的最高 ROI 抓手。

---

## 各维度详评（含证据）

### 1. 安全 8.5 / A-
- **强项**：`policy/model_boundary` 递归拦截注入控制字段；`approval_binding` HMAC 绑定 (task/step/tool/args) + 原子 claim；`dynamic_risk` 不下调 R4；`outbound_url.pin_outbound_http_url` IP 钉死防 DNS 重绑定；`mobile_jwt` 三 scope 隔离 + 每消息实时校验；`security/local_secret` DPAPI 加密；审计哈希链。
- **本轮加固**：SEC-001（run 时间线脱敏）、SEC-002（执行标记下沉）、SEC-003（审计路径脱敏）、SEC-004（配对码 64 位）、SEC-005（会话 epoch 吊销）、SEC-006（grant 防重放）、SEC-007（zip 炸弹）、SEC-008（httpx SSRF 钉死）、SEC-009（敏感字段语义判定）、SEC-012（录屏访问控制回归）。
- **扣分**：发布前、无外部渗透测试；残留 SEC-008（Playwright 主路径 DNS 钉死）、SEC-011（Android user-CA / 应用层 pinning）需运行时方案；SEC-010 桌面 IPC 确认仅部分落地。

### 2. 正确性/可靠性 7.0 / B
- **强项**：1,562 后端测试、黄金任务回归（≥95% 门禁）、严格/审计两态状态机、崩溃恢复 `recover_interrupted_runs`、审批原子消费防重放。
- **扣分**：`AGENT_REVIEW_ISSUES.md` 中 ORCH-002（时间线事件回填，已接受）、ORCH-003（同步 resume 线程无法被 `Future.cancel` 中断）、UI-001 仍 open；崩溃/在线更新管线未闭环（README 自述）；异步用例存在 full-suite 计时抖动。

### 3. 性能/可伸缩性 5.5 / C+（最高优先修复）
- **问题（引自 2026-06-11 评审，复核仍在）**：
  - `orchestration/tool_runtime.py`：非并行批次时 `tool.execute()` 在事件循环线程内**同步执行**，文件复制/Excel/搜索等同步 IO/CPU 期间整个 FastAPI（SSE 推送、审批 API、其它任务）冻结；`sha256_file` 大文件同步哈希秒级阻塞。
  - `llm/onnx_provider.py`：`async def chat()` 内同步跑完整生成循环并持 `RLock`，隐私模式下后端等于单线程串行；冷启动模型加载分钟级阻塞。
  - `core/db.py`（1,919 行）：每操作新建 SQLite 连接、无 WAL、关键索引缺失；`init_db()` 在多处热路径反复全量执行。
- **影响边界**：本地单用户桌面后端，绝对并发低，故未致命；但任一长任务都会卡住 UI/审批，是体验与可靠性的根因。

### 4. 架构/可维护性 7.0 / B
- 分层（agents / policy / orchestration / services / api / perception / indexer）职责清晰；prompt 外置；能力卡解耦。
- 巨型文件需拆分：`db.py`(1919)、`context_management.py`(1780)、`ollama_service.py`(1557)、`perception/ui_automation.py`(1488)、`policy_engine.py`(1229)；前端 `SettingsPanel.tsx`(3362)、`App.tsx`(1835)、`mappers.ts`(2433)。

### 5. 代码卫生 6.0 / C+（次高优先修复）
- **无 mypy**（`backend/pyproject.toml` 仅 ruff，且 select 不含类型检查）；**ruff 非 CI 门禁**（仅 pre-commit `--fix` 本地钩子）。
- 存量 **1,024** 项 ruff 违规：790 `E501`（行长，cosmetic）、**383 `BLE001`（blind `except Exception` 会吞错误，可能掩盖真实 bug）**、244 `I001`、**54 `F401`（未使用导入）**、130 `UP038`、74 `B008`、`S112/S603`（安全相关：try-except-continue / subprocess）。158 处 `noqa`（部分为死注释）。

### 6. 测试 8.0 / A-
- 后端 1,562 用例 + 黄金任务 + `desktop` Vitest/14 项 Playwright smoke + mobile token/companion/remote-input/wakeup smoke；CI 三端矩阵 + 每周 SCA。
- 缺：覆盖率门禁（无 `--cov-fail-under`）、性能/负载测试、真机 LAN/WSS 与 clean-machine 验收（已知 open）。

### 7. 供应链/依赖 7.5 / B+
- `requirements-lock.txt` 直接锁定 + CI 用 lock 安装；`npm audit --audit-level=high` 在 desktop/mobile 阻断 PR；`security-audit.yml` 每周；pip-audit。GitHub Actions 全部用 commit SHA 钉死（供应链最佳实践）。

### 8. 文档/工程化 7.5 / B+
- README/SECURITY/`docs/qa/*`/证据 helper/合规自查极其详尽（信息密度高，偏冗长）。
- 仅 Windows 开发链路：非 Windows 环境下 file.trash/回收站、UIAutomation、屏幕捕获、`C:\Windows` 路径判定等用例直接失败（本审计环境复现 12 项 env 失败，均非代码缺陷）。

### 9. 前端 7.0 / B
- Electron：`contextIsolation/sandbox` 开、`nodeIntegration` 关、严格 CSP、IPC `assertTrustedRenderer` 白名单、token 不下发渲染层。Mobile：SecureStore + 失败即拒明文。
- 扣分：巨型单体组件、`轮询 + WS 推送` 双通道残留、`App.tsx` 仍偏大。

---

## 详细优化解决方案（分阶段路线图）

> 标注：影响（攻击面/可靠性/可维护性）、改动范围、风险、验收标准。**P0 = 高 ROI 应尽快**，**P1 = 中期**，**P2 = 体验/长期**。

### P0 — 立即（最高 ROI）

**P0-1　消除事件循环阻塞（性能根因）**
- 改动：默认 `await asyncio.to_thread(tool.execute, args, context)`，仅对显式声明 `async_safe=True` 的轻量工具走同步快路径；`sha256_file`、`validate_write_preconditions` 一并入 `to_thread`；`onnx_provider.chat()`/模型加载改 `to_thread` + 外层 `asyncio.Lock` 防重复加载风暴。非线程安全工具配 `asyncio.Semaphore` 或单工具 `concurrency_key` 锁。
- 范围：`orchestration/tool_runtime.py`、`orchestration/resource_state.py`、`llm/onnx_provider.py`。中等改动、可被现有执行/引擎测试覆盖。
- 风险：线程安全（共享可变状态）；需逐工具确认。**验收**：长任务执行期间 `/api/health`、审批 API、SSE 推送保持 P95 < 200ms；新增"执行期间并发请求不被阻塞"集成测试。

**P0-2　SQLite 连接与并发治理**
- 改动：进程内连接池或线程局部连接 + `PRAGMA journal_mode=WAL`、`busy_timeout`、`synchronous=NORMAL`；`init_db()` 加幂等守卫（已执行即跳过），从热路径移到启动一次；补关键索引（按 `task_id`/`status`/`created_at` 查询）。
- 范围：`core/db.py`（建议同时按领域拆分该 1919 行文件）。
- 风险：迁移/并发回归。**验收**：黄金任务 + 全量 pytest 全绿；WAL 下并发读写不抛 `database is locked`；`init_db` 调用计数从热路径降为 1。

**P0-3　引入 mypy + 把 ruff 设为 CI 门禁**
- 改动：`backend/pyproject.toml` 增 `[tool.mypy]`（先 `ignore_missing_imports=true` + 渐进 strict）；CI `backend` job 增 `ruff check`（先对**新增/改动文件**门禁，存量豁免清单）；先 `ruff check --fix` 收掉 261 项可自动修复（E501/I001/UP*），再人工清 **383 BLE001**（blind-except → 指定异常或 `logger.exception` + 重抛）与 **54 F401**。
- 范围：CI + 全后端（分批）。
- 风险：BLE001 修复可能改变错误传播语义（需逐处判断"吞错误"是否有意）。**验收**：CI 对改动文件 ruff/mypy 零错误；BLE001/F401 清零或显式豁免并注明理由。

### P1 — 中期

**P1-1　完成本轮残留安全项**
- SEC-008 Playwright 预连接 DNS 钉死：为 `chromium.launch` 注入 `--host-resolver-rules=MAP <host> <pinned-ip>`（用 `pin_outbound_http_url` 解析），对 fake-IP 代理场景 fail-open；需真实浏览器 e2e 验证。
- SEC-011 移动端：以**应用层证书指纹钉扎**替代 `network-security-config` 的全局 `user` CA 信任（在 fetch/WSS 层校验已确认指纹），再移除 user-CA；需真机/模拟器验证 LAN 配对不被破坏。
- SEC-010：为 `cleanupExecute`/`commandsExecute` 评估原生确认（commands 可能高频，建议按风险分级而非一刀切弹窗）；非 Windows 平台桌面 token 加密或 ACL 限制。
- **验收**：补 desktop Playwright + mobile 真机证据；更新 `docs/code-audit-2026-06-12.md` 状态。

**P1-2　拆分巨型模块**
- 后端：`db.py` → `db/{schema,runs,approvals,recordings,audit,...}.py`；`context_management.py`、`policy_engine.py` 按职责拆。前端：`SettingsPanel.tsx`/`App.tsx`/`mappers.ts` 拆为子组件 + hooks，状态下沉。
- 风险：纯重构，靠测试护栏。**验收**：单文件 < ~600 行；测试/typecheck 全绿；无行为变化。

**P1-3　可靠性收尾**
- ORCH-003：引擎线程内**协作式取消**（检查点轮询 cancel 标志），替代无效的 `Future.cancel()`。
- 异步测试抖动：为 `test_supervisor_chat_flow`/`test_golden_tasks` 等加显式状态轮询/超时上调，消除 full-suite 计时 flake。
- **验收**：取消 R 操作能在 N 秒内停住；全量 pytest 连续 3 次无 flake。

### P2 — 体验/长期

**P2-1　覆盖率与性能门禁**：pytest 增 `--cov-fail-under=<阈值>`；新增长任务吞吐/事件循环阻塞回归基准（P0-1 的护栏）。
**P2-2　跨平台开发体验**：为非 Windows 贡献者提供平台能力打桩/`@pytest.mark.skipif(win32)` 归类，使非 Windows 也能跑核心套件（当前 12 项 env 失败应被标 skip 而非 fail）。
**P2-3　前端单通道化**：以 WS 为主通道，轮询仅作兜底并在无活动任务时拆除计时器（UI-001）。
**P2-4　文档收敛**：README 拆分为"用户快速开始 / 开发者 / 发布门禁"三册，降低信息过载。

---

## 附：本轮（2026-06-12-13）已落地修复回顾

- 安全：SEC-001~009 + SEC-012 已修复并带后端回归；SEC-010 部分（桌面 `skillsImport` 原生确认）；SEC-011/SEC-008-Playwright 据实标注待运行时验证。详见 `docs/code-audit-2026-06-12.md`。
- 文档：README 产品概述语气专业化。
- 验证：全量 `pytest backend/tests` = **1757 passed / 68 skipped**，12 项失败经 `origin/main` 对照确认均为既有 Linux 环境限制；`desktop typecheck` 通过。

> 本评分卡为只读审计产物。除已在 PR #1 落地的 SEC 修复与 README 改动外，本文件不含进一步代码改动；上述路线图为可执行的后续整改建议，建议按 P0→P1→P2 分批落地并补回归。
