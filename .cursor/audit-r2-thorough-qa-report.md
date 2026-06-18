# 8-Agent 彻底 QA 报告

**日期:** 2026-06-11  
**范围:** Backend 全量 pytest + 用户流程 E2E + 安全域 + 编排并发 + Desktop/Mobile smoke + 工作区 gate  
**方法:** 8 并行 agent 分域执行 + 主线程复现/补跑

---

## 执行摘要

| 层级 | 结果 | 说明 |
|------|------|------|
| Backend 全量 pytest（顺序） | **1768 passed, 3 skipped** | 主线程复跑 ~4m17s，全绿 |
| Backend 用户流程 E2E（11 文件） | **96/96 passed** | 聊天/运行/WS/语音/工作流 |
| Backend 编排/并发（14 文件） | **85/85 passed** | 并行隔离、cancel、shutdown、FAILED 语义 |
| Backend 功能域（26 文件） | **482/482 passed**（顺序） | 文件/浏览器/LLM/技能/移动伴侣 |
| Backend 安全域（13+8 文件） | **隔离运行全绿**；合并跑 4 个 429 | 配对限流测试污染，非安全绕过 |
| Mobile typecheck + 4 smokes | **全部 PASS** | 配对/LAN/唤醒/远程输入 |
| Desktop typecheck | **PASS** | |
| Desktop smokes（14 项） | **10 PASS / 4 FAIL** | 见下文 |
| 工作区 hygiene / deps | **PASS** | |
| `qa:gate`（npm script） | **FAIL** | `run_tests.ps1` xdist 探测在 `$ErrorActionPreference=Stop` 下崩溃 |
| `verify_release_safety.ps1` | **FAIL** | `PYTHONPATH` 指向 repo 根，缺 `backend/` |
| `verify_mobile_lan_wss_preflight.ps1` | **FAIL（预期）** | 本机无 LAN TLS / 公网 https 源 |

**合并前结论:** 核心后端与移动端行为测试可靠；Desktop 有 4 项 smoke 需修复或更新断言；CI `qa:gate` 脚本在本环境会先挂再跑测试。

---

## Agent 分域结果

### Agent 1 — Backend 全量 pytest

- 首次跑：**1765 passed, 3 failed, 3 skipped**（~4m41s）
- 失败均为 `sqlite3.IntegrityError: UNIQUE constraint failed: audit_events.sequence`
  - `test_mcp_registry_adapts_to_tool_definitions`
  - `test_scheduler_fatal_outcome_cancels_parallel_siblings`
  - `test_finalize_marks_failed_when_success_coexists_with_blocked_skips`
- **主线程复跑：** 同上 3 项一起跑 **PASS**；全量顺序跑 **1768 passed**
- **判定:** 审计链 sequence 在高压/特定测试顺序下**偶发竞态**（进程内 `_store_audit_chain_head` 与共享 SQLite）；非稳定产品缺陷，但应在 CI 中监控

### Agent 2 — 用户流程 E2E（96 tests）

| 文件 | 通过 | 用户场景 |
|------|------|----------|
| `test_lengrvis_parity_e2e.py` | 3/3 | 记忆/规划/MCP/隐私写阻断 |
| `test_state_machine_integration.py` | 8/8 | 状态机双写、严格模式 |
| `test_phase0/2_integration.py` | 16/16 | 工具注册、DAG、编排记忆 |
| `test_runs_api.py` | 28/28 | 启停/取消/审批/诊断 |
| `test_commands_api.py` | 11/11 | 斜杠命令、resume/compact |
| `test_websocket_stream.py` | 5/5 | 任务流、重放、通知 |
| `test_task_resume.py` | 2/2 | 后台恢复 |
| `test_workflow.py` | 5/5 | DAG 校验 |
| `test_environment_stream.py` | 13/13 | 环境事件流 |
| `test_voice_api.py` | 5/5 | 语音健康/转写 |

### Agent 3 — 安全 / 认证 / 隐私

- 13 文件合并跑：**218 passed, 4 failed**（全为 pairing **HTTP 429**）
- 单文件隔离：**222/222 passed**
- 额外 8 文件：**47 passed, 1 skipped**（symlink Windows）
- **P0 安全断言：无失败**
- **P1 测试隔离：** 全量套件中配对限流状态共享 → 偶发 429

### Agent 4 — 编排 / 并发 / 生命周期（85 tests）

全部通过，覆盖审计 sprint 修复点：

- 并行 step 独立 `deepcopy(context)`、read state 按 step 隔离
- `TaskPool.shutdown()` / lifespan 排水
- `cancel_run` 取消并行兄弟任务 + router 复用
- 成功 + blocked skip → `FAILED` 终态
- orchestrator registry / run router registry 绑定

### Agent 5 — Desktop（14 smokes + typecheck）

**Typecheck:** PASS

| Smoke | 结果 | 说明 |
|-------|------|------|
| `smoke:first-launch` | **FAIL** | Playwright 已可用；断言 `task-record-only` 文案期望 `/系统检查已有记录/`，实际为「等待启动/等待只读快照」 |
| `smoke:system-diagnostics-ui` | PASS | 安装 Playwright 后通过 |
| `smoke:document-scope` | PASS | |
| `smoke:mobile-pairing-qr` | PASS | |
| `smoke:settings-local-model` | PASS | 截图已写入 `.tmp/qa-evidence/` |
| `smoke:remote-input-grant` | PASS | |
| `smoke:skill-manifest-ui` | PASS | |
| `smoke:preload-api` | PASS | |
| `smoke:desktop-ws` | PASS | |
| `smoke:desktop-token` | PASS | DPAPI token 生命周期 |
| `smoke:backend-log-redaction` | PASS | |
| `smoke:ipc` | **FAIL** | `ipc-security` 通过；`backend-ollama-env-smoke` 期望 `desktop_api.secret` 明文 hex，实际为 `dpapi:...`（DPAPI sprint 后未更新 smoke） |
| `smoke:browser-activity` | **FAIL** | `assertHomeQuickTemplates` TimeoutError（首页模板未在时限内出现） |
| `smoke:source-map-policy` | PASS | |

**用户场景覆盖缺口：**

- 首次启动「结果质量」文案与 smoke 不同步
- Ollama 子进程 env smoke 未适配 DPAPI 落盘格式
- 浏览器活动首页模板加载超时（可能 mock/时序或 UI 变更）

### Agent 6 — Mobile（4 smokes + typecheck）

**全部 PASS**

`mobile-token-smoke.cjs` 模拟场景：

- 8 位配对码、QR/粘贴、LAN HTTPS 阻断、loopback 阻断
- SecureStore 迁移、session 恢复/unpair 清理
- WS subprotocol `lengrvis.mobile.token.*`
- `wakeup-contract-smoke`：pending/approve/reject API 契约

### Agent 7 — 工作区 Gate

| Gate | 结果 |
|------|------|
| `npm run hygiene` | PASS |
| `npm run deps:verify` | PASS |
| `verify_release_safety.ps1` | FAIL — `ModuleNotFoundError: app`（PYTHONPATH） |
| `verify_mobile_lan_wss_preflight.ps1` | FAIL — 无 LAN TLS / 公网 URL（开发机正常） |
| `install_acceleration.ps1` 语法 | PASS |
| `model_manifest.json` | 部分 FAIL — Qwen 模型 `revision: ""`（已知，manual install） |

### Agent 8 — 功能域（482 tests）

| 域 | 模块数 | 结果 |
|----|--------|------|
| 文件/搜索/清理 | 5 | PASS |
| 浏览器 | 2 | PASS |
| Guardian | 1 | PASS |
| 远程桌面 | 1 | PASS |
| LLM/ONNX/OCR/文档 | 4 | PASS |
| 技能/开发工具 | 3 | PASS |
| 语音/视觉 | 2 | PASS |
| MCP | 1 | 全量偶发 FAIL，隔离 PASS |
| 审批/桌面 WS/移动配对 | 4 | PASS |
| 恢复/回滚 | 2 | PASS |

**缺口:** 无 `wakeup` 后端 pytest（仅有 mobile contract smoke）

---

## 模拟用户操作矩阵

| 用户旅程 | 验证手段 | 状态 |
|----------|----------|------|
| 桌面首次打开、看结果质量 | `smoke:first-launch` | **FAIL**（文案） |
| 设置里看系统诊断 | `smoke:system-diagnostics-ui` + `test_system_diagnostics.py` | PASS |
| 配置本地模型 | `smoke:settings-local-model` + ONNX tests | PASS |
| 手机扫码配对 8 位码 | mobile-token-smoke + `test_mobile_pairing.py` | PASS |
| 手机审批/远程/唤醒 | 3 mobile smokes + pairing tests | PASS |
| 发起 run、流式聊天、取消 | runs_api + websocket + cancel tests | PASS |
| 并行多步任务 | parallel_context + scheduler | PASS |
| 关应用 / 取消 run | lifespan + cancel_run | PASS |
| 隐私模式写阻断 | parity e2e + path sandbox | PASS |
| SSRF 出站 URL | outbound_url + mcp_ssrf + cloud_llm_ssrf | PASS |
| 文件搜索/语义搜索 | file_search + semantic_search | PASS |
| 浏览器自动化 | browser_activity + browser_writes | PASS（后端）；desktop browser smoke FAIL |
| Guardian 策略 | guardian_backend + permission_policy | PASS |
| 技能包管理 | skill_loader + skill-manifest-ui smoke | PASS |

---

## 待修项（按优先级）

### P0 — 合并阻断（若 CI 跑 desktop smoke bundle）

1. **`backend-ollama-env-smoke.cjs`** — 断言需适配 DPAPI：`desktop_api.secret` 读回为 `dpapi:` 前缀时需 `unprotectLocalSecret` 或与 `manager.getDesktopApiToken()` 只比 env 注入值
2. **`scripts/run_tests.ps1`** — `Test-PytestXdistAvailable` 在 `ErrorActionPreference=Stop` 下，`import xdist` 失败 traceback 会终止脚本 → **`qa:gate` 无法启动**

### P1 — 质量 / 稳定性

3. **`first-launch-smoke.cjs`** — `task-record-only` 模板的 outcome 文案与 renderer 不同步
4. **`browser-activity-smoke.cjs`** — 首页 quick templates 超时（调查 mock 时序或 selector）
5. **审计 sequence 偶发 UNIQUE** — 全量顺序跑大多绿；建议 fixture 级 DB 隔离或审计 head 按 worker 隔离
6. **配对限流 429** — 大套件合并跑时共享 rate limiter；建议 per-test reset 或独立 client IP fixture

### P2 — 发布 / 环境

7. **`verify_release_safety.ps1`** — `PYTHONPATH` 应含 `backend/`
8. **LAN WSS preflight** — 仅在有 TLS + 公网 URL 的发布机跑；开发机 FAIL 可接受
9. **Qwen manifest `revision: ""`** — 已文档化 manual install

---

## 推荐合并前命令（本机已验证）

```powershell
# Backend（顺序，最稳）
cd backend
python -m pytest tests -q --tb=line

# Mobile
cd ..\mobile
npm run typecheck
npm run smoke:token
npm run smoke:task-companion
npm run smoke:remote-input-grant
node scripts/wakeup-contract-smoke.cjs

# Desktop（需 npx playwright install chromium）
cd ..\desktop
npm run typecheck
npm run smoke:desktop-token
npm run smoke:desktop-ws
npm run smoke:mobile-pairing-qr
# 全量 smoke _bundle_ 仍有 4 项失败见上表
```

---

## 证据路径

- Desktop 截图: `.tmp/qa-evidence/settings-local-model-experience-smoke-*.png`, `skill-manifest-ui-smoke.png`
- 本报告: `.cursor/audit-r2-thorough-qa-report.md`
