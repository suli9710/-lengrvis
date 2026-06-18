# Round 3 透镜审计 — 安全

**日期：** 2026-06-12  
**透镜：** Security（严格）  
**源码范围（关键模块 + 调用链）：**

| 模块 | 路径 |
|------|------|
| 出站 URL 校验 | `backend/app/core/outbound_url.py` |
| MCP 客户端 | `backend/app/mcp/client.py` |
| Cloud LLM | `backend/app/llm/openai_compatible.py`, `backend/app/llm/registry.py` |
| Webhook 适配器 | `backend/app/adapters/webhook.py` |
| Desktop API 令牌 | `backend/app/security/desktop_api.py`, `desktop/src/main/desktopApiToken.ts` |
| 权限策略 | `backend/app/policy/permissions.py` |
| 移动配对 | `backend/app/services/mobile_pairing_service.py`, `backend/app/api/routes_pair.py` |
| 审计 / 诊断 API | `backend/app/api/routes_audit.py`, `routes_approvals.py`, `routes_system.py` |
| 并行 / 取消 / 工具 | `backend/app/orchestration/*`, `backend/app/services/run_service.py` |
| Dev Web 客户端 | `desktop/src/renderer/lib/apiClient.ts` |

**基线：** Round 2 P0 安全项（`.cursor/audit-r2-final-report.md` §3）+ Sprint B（SSRF / token / 配对 / 脱敏）  
**验证：** 对照本地 diff + 关键模块 auth/data flow 追踪；回归测试（`test_mcp_ssrf`, `test_cloud_llm_ssrf`, `test_outbound_url`, `test_permission_policy`, `test_mobile_pairing`, `test_cancel_run_drains_tasks`, `test_parallel_context_isolation`, `test_tool_timeout`）

---

## 1. 执行摘要

| 指标 | Round 2 | Round 3 |
|------|---------|---------|
| 本透镜相关 P0（SSRF / token / 配对 / 脱敏） | 8 | 8（同 ID 追踪） |
| **FIXED** | 3 | **13** |
| **PARTIAL** | 0 | **2** |
| **OPEN** | 5 | **3** |
| 透镜得分（0–100，严格） | **52** | **84** |

本次 diff 以**安全加固**为主：出站 SSRF 统一校验、audit/诊断脱敏、Desktop API token DPAPI 存储、并行 context 隔离、per-step 资源 read-state、dry-run 写锁序列化、工具超时、OS run cancel drain、配对码熵与速率限制。剩余风险主要为 **中等**：SSRF DNS TOCTU、LAN 全局配对 griefing、run_service cancel 回落不完整。

---

## 2. Findings 表（R3-001 — R3-018）

| ID | Severity | Location | Status | Confidence | Summary |
|----|----------|----------|--------|------------|---------|
| **R3-001** | High | `backend/app/adapters/webhook.py` | **FIXED** | High | Webhook URL 在 POST 前通过 `validate_outbound_http_url(..., allow_private=False)`。 |
| **R3-002** | High | `backend/app/mcp/client.py` | **FIXED** | High | MCP HTTP 客户端拦截私网/metadata URL；`follow_redirects=False`；可选 Bearer auth 已接线。 |
| **R3-003** | High | `backend/app/llm/openai_compatible.py`, `backend/app/llm/registry.py` | **FIXED** | High | Cloud LLM base URL 校验；共享 httpx 禁用重定向；本地 provider 仅在 `LOCAL_PROVIDERS` + `is_local_base_url` 时允许私网。 |
| **R3-004** | Medium | `backend/app/api/routes_audit.py` | **FIXED** | High | Audit list 端点在返回前经 `audit_core.sanitize_payload()` 清理 `payload`。 |
| **R3-005** | Medium | `backend/app/api/routes_approvals.py` | **FIXED** | High | Approval 执行错误使用 `redact_public_text()` 替代原始 `str(exc)`。 |
| **R3-006** | Medium | `backend/app/api/routes_system.py` | **FIXED** | High | GET diagnostics 敏感字段脱敏；`local_paths` 有意保留供本地 UI。 |
| **R3-007** | Medium | `backend/app/security/desktop_api.py`, `desktop/src/main/desktopApiToken.ts` | **FIXED** | High | Desktop API token 走 DPAPI `load_or_create_local_secret`；Electron 原子写 + DPAPI。 |
| **R3-008** | Medium | `step_scheduler_handler.py`, `os_execution_engine.py` | **FIXED** | High | 并行 step 使用 `copy.deepcopy(context)`，修复并发工具执行间可变 context 串扰。 |
| **R3-009** | Medium | `backend/app/orchestration/resource_state.py` | **FIXED** | High | Read-before-write 状态按 `(task_id, step_id)` 作用域，关闭并行写绕过另一 step read cache。 |
| **R3-010** | Medium | `backend/app/orchestration/tool_runtime.py` | **FIXED** | High | Dry-run preview 不再跳过写锁（`test_dry_run_preview_serializes_with_real_write_on_same_path`）；工具执行超时已加。 |
| **R3-011** | Medium | `mobile_pairing_service.py`, `routes_pair.py` | **FIXED** | High | 配对码 8 hex（32-bit）、严格 schema；per-IP + 全局 confirm 失败速率限制。 |
| **R3-012** | Medium | `backend/app/core/outbound_url.py` | **PARTIAL** | Medium | DNS 在**校验时**检查，httpx **连接时**无 IP pinning — 经典 SSRF TOCTU / rebinding 窗口（webhook/MCP/LLM）。 |
| **R3-013** | Medium | `mobile_pairing_service.py` (`GLOBAL_PAIR_CONFIRM_RATE_KEY`) | **OPEN** | High | 60s 内 32 次全局失败 confirm → 任意 LAN HTTPS 客户端可 grief 阻塞**全员**移动配对（422/401 均计数）。 |
| **R3-014** | Medium | `run_service.py` (`_router_for_run` fallback) | **PARTIAL** | Medium | 已释放 router 后 `cancel_run` 新建 `OSExecutionEngine`，其 `_run_tasks` 为空；旧实例上并行 step task 可能在用户 cancel 后继续运行。 |
| **R3-015** | Low | `outbound_url.py` (`198.18.0.0/15` 例外) | **OPEN** | Low | Benchmark 网段经 DNS 解析有意放行；小众 SSRF/代理可达性边缘。 |
| **R3-016** | Low | `orchestrator_registry.py` | **OPEN** | Low | `release_task()` 任务完成时未调用 — orchestrator/bus 条目常驻（内存/生命周期卫生，非跨用户）。 |
| **R3-017** | Info | `backend/app/policy/permissions.py` | **FIXED** | High | 默认放行路径未变；仅增加显式 `reason` 字符串（可读性，非行为变更）。 |
| **R3-018** | Info | `desktop/src/renderer/lib/apiClient.ts` | **FIXED** | High | Desktop token 仅在 web-only dev 经 `VITE_LENGRVIS_DESKTOP_API_TOKEN` 注入；生产 Electron 路径未变；diff 无新存储泄漏。 |

### Round 2 P0 安全对照（本透镜）

| R2 ID | 发现 | R3 映射 | R3 状态 |
|-------|------|---------|---------|
| P0-07 | MCP URL 无 SSRF | R3-002 | **FIXED** |
| P0-08 | Cloud LLM base_url SSRF | R3-003 | **FIXED** |
| P0-09 | Desktop token 明文 | R3-007 | **FIXED** |
| P0-15 | 配对码熵低 | R3-011 | **FIXED** |
| — | Webhook SSRF | R3-001 | **FIXED** |
| — | Audit/diagnostics 泄露 | R3-004, R3-005, R3-006 | **FIXED** |
| P0-13 | dev:web 无 token | R3-018 | **FIXED**（dev 显式 env；生产不变） |
| — | 出站 SSRF TOCTU | R3-012 | **PARTIAL** |

---

## 3. 关键文件快照

| 文件 | 结论 |
|------|------|
| `outbound_url.py` | 基线 SSRF 守卫扎实；TOCTU 残留 |
| `mcp/client.py` | SSRF + 无重定向 + auth header 强 |
| `openai_compatible.py` | Cloud/local 分流校验正确 |
| `webhook.py` | POST 前 URL 校验 |
| `desktop_api.py` | DPAPI 迁移改善本地 secret |
| `permissions.py` | diff 未削弱策略 |
| `mobile_pairing_service.py` | 更强配对码 + 限速；全局桶双刃剑 |
| `routes_audit.py` | 脱敏修复关闭 info disclosure；auth 仍 behind desktop token |
| `apiClient.ts` | Dev-only token 路径；无新生产暴露 |

---

## 4. 测试覆盖（透镜）

| 行为 | 测试 | 状态 |
|------|------|------|
| MCP SSRF | `test_mcp_ssrf.py` | ✅ |
| Cloud LLM SSRF | `test_cloud_llm_ssrf.py` | ✅ |
| outbound_url 规则 | `test_outbound_url.py` | ✅ |
| 权限策略 | `test_permission_policy.py` | ✅ |
| 移动配对 | `test_mobile_pairing.py` | ✅ |
| OS engine cancel drain | `test_cancel_run_drains_tasks.py` | ✅ |
| 并行 context 隔离 | `test_parallel_context_isolation.py` | ✅ |
| 工具超时 | `test_tool_timeout.py` | ✅ |
| SSRF connect-time IP pin | — | ❌ 缺失 |
| run_service cancel 回落 | — | ❌ 缺失 |
| 全局配对 griefing | — | ❌ 缺失 |

---

## 5. 透镜得分：**84 / 100**

| 维度 | 权重 | 分 | 说明 |
|------|------|-----|------|
| 出站 SSRF | 30% | 24/30 | 统一校验 + 禁重定向；TOCTU 未 pin IP |
| 认证 / 秘密 | 25% | 22/25 | DPAPI token、配对熵、dev:web 隔离 |
| 数据暴露 / 脱敏 | 20% | 18/20 | audit/approvals/diagnostics 改善 |
| 执行控制面 | 15% | 11/15 | cancel drain 在 engine 层好；run_service 回落弱 |
| 可用性 / 滥用 | 10% | 9/10 | 全局配对限速可被 LAN grief |

**较 R2（约 52）提升 +32：** Sprint 交付 SSRF、token、配对、脱敏等 R2 最高优先级项；未达 90+ 主因是 **R3-012 TOCTU** 与 **R3-013 全局配对 DoS**。

---

## 6. Top 5 OPEN（优先修复）

### 1. R3-012 — SSRF 校验 TOCTU（PARTIAL）

**位置：** `outbound_url.py` + 消费者（`webhook.py`, `mcp/client.py`, `openai_compatible.py`）

**影响：** 校验时解析为公网 IP 的主机名，连接时可能解析为私网/metadata — 出站请求可达内网服务。

**攻击路径：** 攻击者影响 URL（webhook payload、MCP 配置、cloud `base_url`）+ 在校验与 `httpx.post` 之间控制或污染 DNS。

**证据：** 校验仅用一次 `socket.getaddrinfo`；MCP/LLM/webhook 连接时未重校验或 pin 地址。重定向已禁（好），DNS rebinding 仍在。

**修复：** 连接时 pin 已解析 IP（自定义 transport），或每次请求前立即重解析并重检；任一解析地址被 block 则拒绝 hostname URL。

---

### 2. R3-013 — 全局配对 confirm 速率限制 → LAN 级 DoS（OPEN）

**位置：** `mobile_pairing_service.py` — `_raise_if_pairing_rate_limited`, `_record_pairing_failure`

**影响：** 临时拒绝 LAN 上**所有**用户的移动配对。

**攻击路径：** 任意 LAN HTTPS 客户端发送 32 次失败 `/api/pair/confirm`（无效码计入）；全员配对阻塞 ~60s；重复可持续 griefing。

**证据：** 新增 `GLOBAL_PAIR_CONFIRM_RATE_KEY` 上限 32；422 长度错误与 401 无效码均记失败；成功仅清 per-host key，不清 global。

**修复：** 分 subnet/设备预算；指数退避替代硬全局锁；confirm 加 desktop-token 门控； malformed-length 不计入 global bucket。

---

### 3. R3-014 — OS run cancel 可能无法 drain 在途 step task（PARTIAL）

**位置：** `run_service.py` `_router_for_run` + `os_execution_engine.py` `_run_tasks`

**影响：** 用户 cancel 后 run 标记已取消，工具/子进程可能继续 — agent 执行控制面缺口。

**攻击路径：** 更偏 **cancel fail-open**：`_release_run_router` 后 `cancel_run` 实例化新 router/engine；新 engine 上 `_run_tasks` 为空，旧 engine 实例仍持有运行中 asyncio step work。

**证据：** `_engine_router()` 总创建新 `OSExecutionEngine()`；`_run_tasks` 实例局部；`test_cancel_run_drains_tasks.py` 覆盖直接 engine cancel，未覆盖 run_service 回落路径。

**修复：** 按 `run_id` 单例 engine registry，或经 `orchestrator_registry.get_for_run()` / 共享 task registry cancel；router 释放前须 drain step tasks。

---

### 4. R3-002 后续 — MCP 一律 block 私网 URL（运营 / 配置）

**位置：** `mcp/client.py` — `allow_private=False` 恒为真

**影响：** 用户可能经公网 tunnel/ngrok 暴露本地 MCP 以绕过 block — **自伤式**更大暴露面。

**置信度：** 误配置中等；非跨租户。

**修复：** 设置中 opt-in `allow_private` 用于 loopback MCP（类似 LLM `LOCAL_PROVIDERS` 路径），需显式用户同意。

---

### 5. R3-004 残留 — Audit 路由 auth 未变（可接受）

**位置：** `routes_audit.py`

Audit 端点仍在 desktop API token 中间件之后（非 LAN 公开）。脱敏修复是 meaningful change；**本 diff 无新 auth 缺口**。

*(列为 #5 因其余 open 项低于 medium；此处为确认性备注，非新 medium 发现。)*

---

## 7. 中文摘要

**结论：** 相对 Round 2，安全 posture 有**实质性改善**。原先最危险的「MCP/Cloud LLM/Webhook SSRF、Desktop token 明文、配对码熵不足、audit/诊断泄露」均已 **FIXED** 并有测试或代码证据。并行 context 隔离、per-step read-state、dry-run 写锁、工具超时、OS engine 层 cancel drain 亦属正向加固。

**仍未关闭的核心：** （1）`outbound_url` 仅在校验时解析 DNS，连接阶段未 pin IP，存在 **SSRF TOCTU**（R3-012）；（2）移动配对新增 **全局失败计数器**，可被 LAN 上任意 HTTPS 客户端滥用，导致全员暂时无法配对（R3-013）；（3）`run_service` 在 engine router 已释放时用新 `OSExecutionEngine` 实例 cancel，**无法取消旧实例上仍在执行的并行 step task**（R3-014）。

**次要 open：** MCP 本地 loopback 一律拒绝可能促使用户走 tunnel 自暴露（运营项）；`198.18.0.0/15` benchmark 例外与 orchestrator registry 未 release 为低优先级卫生项。

**建议下一步：** 优先 R3-012（connect-time IP pin）与 R3-013（拆分 global bucket）；补 `run_service` cancel 集成测试并修复 R3-014；MCP local opt-in 可作为产品化 follow-up。

**透镜得分：84/100**（R2 同透镜约 52/100）。

---

*Round 3 Security 透镜 — 基于本地 diff、关键模块 auth/data flow 与 R2 P0 追踪生成。*
