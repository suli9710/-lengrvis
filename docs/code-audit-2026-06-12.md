# Lengrvis 全项目安全代码审计报告（2026-06-12）

> 审计日期：2026-06-12
> 审计类型：完整代码审计，安全优先（覆盖审批绕过、R4 禁区、移动配对/远程输入、路径沙盒、脱敏失效五大高危攻击面，并兼顾正确性与工程化）
> 审计范围：`backend/app`（392 个 Python 文件）、`desktop/src`（Electron 主/预加载/渲染桥接）、`mobile`（Expo 伴侣）
> 严重程度：🔴 高 = 可导致越权/数据泄露/审批绕过或需尽快处理；🟡 中 = 纵深防御缺口或受配置/前置条件限制的真实风险；🟢 低 = 局部加固与代码卫生
> 验证方式：核心结论均回溯到具体 `file:line`，并对头号发现做了人工源码核对。

---

## 总体结论

Lengrvis 的**安全设计基线显著高于同类原型**，本轮审计未发现可被远程未认证攻击者直接利用的严重漏洞。已验证的强防御包括：

- **审批不可伪造**：`model_boundary.py` 以 `extra="forbid"` 信封递归拦截模型注入的 `approved`/`approval_id`/`*_hmac` 等控制字段；真实审批走 DB 记录 + HMAC 绑定（task/step/tool/canonical args）+ 原子消费（`step_execution_handler.py` + `db.claim_approval_for_execution`），并对 settings/permission policy 做指纹绑定以防 TOCTOU。
- **R4 硬拒绝**：`dynamic_risk.py` 不会下调 R4，`policy_engine.py` 对未知/低信任工具与 MCP 工具 fail-closed。
- **移动通道作用域隔离扎实**：`mobile:approval` / `remote:view` / `remote:input` 三类 scope 在 decode 与路由层双重隔离；远程输入授权在每条 WS 消息上实时校验（`validate_mobile_claims_active`），撤销/过期即关闭连接。
- **Electron 三件套全开**：`contextIsolation/sandbox` 开启、`nodeIntegration` 关闭、严格 CSP、IPC `assertTrustedRenderer` 白名单、桌面 token 不下发渲染层、后端 token 文件 `0600` 原子写 + Windows DPAPI 加密。
- **MCP/浏览器 SSRF 防护**：连接期 IP 钉死 + 禁止重定向 + 私网/元数据 DNS fail-closed。

主要问题集中在**纵深防御一致性**与**受限场景下的真实风险**，没有发现默认配置下的越权执行或审批绕过。下面按严重程度列出。

---

## 🔴 高严重度

### SEC-001 — Run 引擎时间线/回放/进度/状态缺少公开脱敏层

- **位置**：`backend/app/services/run_service.py:192-197`（`get_timeline`）、`200-213`（`get_progress`）、`136-137`（`state._runtime.data_dir` 写入）、`backend/app/orchestration/run_event_bus.py:118-125`（`run_event_to_wire`）、`backend/app/api/routes_runs.py:40-53,80-116,130-144`
- **问题**：`get_timeline()` 直接返回 `run.model_dump()` + 原始 `RunEvent` 负载；`run_event_to_wire()` 是裸 `model_dump`；`_state_response()` 原样返回 `run.message`（用户任务正文）。这些负载包含：
  - 绝对本机路径 `state._runtime.data_dir`（`run_service.py:137`）；
  - 用户任务正文 `message`、`run.started`/`run.failed` 事件里的 `message`、`error`；
  - 桥接的 agent `content`/`structured_payload`/`tool_calls`、`plan.generated` 完整计划、`tool.result` 原始 `output`。
  - 与**任务**侧（`routes_tasks.py` 的 `_public_*` 系列做了正文/工具参数/录屏/审查理由脱敏）形成鲜明对比——**Run 侧完全没有等价的公开脱敏器**。
- **影响/范围**：项目在 `SECURITY.md` 明确把"时间线/公开 API 泄露本机路径、任务正文"列为**最高优先级攻击面**。这些 `/api/runs/*` 端点默认受桌面 API token 约束、且远程 LAN 客户端被 `lan_api_guard` 直接拦截（仅放行 mobile 路径），因此**默认部署下暴露面限于持有桌面 token 的本机/桌面客户端**；但一旦开启 `LENGRVIS_ALLOW_LAN_DESKTOP_API`，持桌面 token 的 LAN 客户端即可读取**未脱敏**的运行正文与本机绝对路径，违反既定脱敏契约。
- **建议**：新增统一的 run-event 公开脱敏器，应用于 `get_timeline`、`get_progress` 与 `run_event_to_wire`；并在所有 run API 响应中剔除 `state._runtime` 与原样 `run.message`。
- **状态**：**已修复**（2026-06-12）。新增 `redaction.redact_run_payload`，并在 `run_service.get_timeline`/`get_progress`、`run_event_bus.run_event_to_wire`、`routes_runs._state_response`（`message`/`error`）统一接入。该脱敏器在保留标识符（不做会破坏 32 位十六进制 id 的 24+ 通用 token 折叠）与桌面所需结构化负载的前提下：剔除 `_` 前缀内部键（消除 `state._runtime.data_dir` 绝对路径泄露），并对敏感键值与内联密钥模式（api key、Bearer、`sk-`、PEM、卡号、邮箱、电话）脱敏。考虑到该面是桌面 token 约束的本机面（而非公开/移动面），刻意**未**采用任务侧那种激进的路径折叠，以保留本机桌面 UX。回归测试：`backend/tests/test_runs_api.py::test_run_timeline_progress_and_wire_redact_secrets_and_internal_paths`（覆盖 timeline / progress / WS wire / state 四个面，并锁定标识符与结构化负载保留契约）。
- **备注**：原 `AGENT_REVIEW_ISSUES.md` 的 ORCH-002 仅讨论时间线"事件回填"，未覆盖此脱敏缺口。

---

## 🟡 中严重度

### SEC-002 — 工具/感知层把 `approved`/`approval_id` 当布尔处理（纵深防御缺口）

- **位置**：`backend/app/services/browser_activity_runtime.py:780-781`、`backend/app/tools/ui_automation_tools.py:388-389`、`backend/app/tools/browser_tools.py:228-229`、`backend/app/tools/workflow_tools.py:27-28`、`backend/app/perception/ui_automation.py:750-767`
- **问题**：这些写路径只检查 `approved`/`approval_id` 的真值；`perception/ui_automation` 甚至会在带这两个标志时把任何非 `DENY` 的策略结果（含 `NEEDS_USER_APPROVAL`）升级为 `ALLOW`。它们**不做 DB/HMAC 校验**。
- **可利用性**：**正常编排/API 流程下不可直接利用**——`model_boundary` 拦截注入字段，`step_execution_handler` 与直连路由（`routes_browser.py:84-174`、`routes_ui_automation.py:240-325`）做 HMAC 绑定 + `claim_approval_for_execution`。风险在于：**若未来有新调用方直接调用 `tool.execute()`/runtime.act 并传入伪造 ID**，即可跳过审批。属经典纵深防御失效。
- **建议**：将 HMAC/DB 校验下沉为工具/runtime 层的硬前置（或在这些入口显式拒绝裸布尔审批），不要依赖上层永远先跑。

### SEC-003 — `redact_text` 不做本机路径脱敏，仅 `redact_public_text` 才做

- **位置**：`backend/app/policy/redaction.py:83-96`（`redact_text` vs `redact_public_text`）、`66-68`（`LOCAL_PATH_PATTERN`）、`backend/app/core/audit.py`（读路径走 `redact_value`/`redact_text`）
- **问题**：`LOCAL_PATH_PATTERN`/`PUBLIC_FILE_NAME_PATTERN`/`PUBLIC_PROMPT_TEXT_PATTERN` 只在 `redact_public_text` 中应用。形如路径的字符串如果落在非路径键（如 `note`、`error`、`summary`）里，经 `redact_text` 不会被替换。审计读路径与多个内部面用的是 `redact_value`/`redact_text`，因此**自由文本字段中的本机绝对路径可能未脱敏**。
- **建议**：对字符串叶子统一走 `redact_public_text`，或合并两条脱敏管线。

### SEC-004 — 配对码熵仅 32 位 + 限速为单进程内存/按 IP

- **位置**：`backend/app/services/mobile_pairing_service.py:36`（`PAIR_CODE_HEX_LENGTH = 4` → 8 位十六进制 = 32 bit）、`37-41,1051-1076`（`_PAIR_CONFIRM_FAILURES` 进程内存、8 次/60s/按 client_host）、`backend/app/api/routes_pair.py:28-36`
- **问题**：配对码 32 位随机；爆破限速按 `client_host` 计、且存于**进程内存**（重启即清零，多 worker 各自计数）。LAN 上多个伪造源 IP 可显著放大单个配对码生命周期内的总尝试数。成功一次即获得 30 天 JWT（可含 `remote:view`）。
- **缓解项**：配对码 300s 过期、单次使用（`BEGIN IMMEDIATE` 原子置 pending）、LAN 下 `/api/pair/confirm` 强制 HTTPS。单码被猜中的概率仍然很低，但不为零、且随重复配对累积。
- **建议**：提高配对码熵（≥64 bit）；限速改为持久化 + 按配对请求维度（而非仅按源 IP）；多 worker 共享计数。

### SEC-005 — 移动 JWT 30 天长有效期，无 `jti`/会话吊销表

- **位置**：`backend/app/services/mobile_pairing_service.py:34`（`TOKEN_TTL_SECONDS = 30天`）、`backend/app/security/mobile_jwt.py:22-47`
- **问题**：配对后的 bearer 30 天有效，且无 `jti`/吊销列表；仅能通过 `revoke_mobile_device` 设备级吊销。token 一旦泄露（日志/备份/恶意软件），在设备被吊销前持续可用于审批/任务/屏幕查看。
- **建议**：引入 `jti` + 吊销表或更短 TTL + 刷新令牌。

### SEC-006 — 远程输入授权 JWT 可重放

- **位置**：`backend/app/services/mobile_pairing_service.py:294-338`（`claim_remote_input_grant_token`）、`backend/app/security/mobile_jwt.py:205-233`
- **问题**：同一 grant 反复 claim 会铸造新 JWT，但**不作废此前的 token**，导致同一 grant 同时存在多个有效 `remote:input` token，直到过期/撤销。任一副本被窃取都会延长滥用窗口。
- **缓解项**：grant TTL 仅 5 分钟、每条 WS 消息实时校验 grant 状态。
- **建议**：claim 时绑定/轮换单一有效 token（如记录当前有效 token 指纹，旧的立即失效）。

### SEC-007 — 技能包 zip 导入无解压体积上限（zip 炸弹 DoS）

- **位置**：`backend/app/services/skill_service.py:268-284`
- **问题**：导入已正确阻断路径穿越/zip-slip，但 `extractall` 无解压后体积上限，恶意压缩包可撑爆磁盘造成 DoS。
- **建议**：导入前统计 `ZipInfo.file_size` 总和并设上限；逐条解压并累计校验。

### SEC-008 — SSRF 校验与 Playwright `goto` 之间存在 DNS 重绑定 TOCTOU

- **位置**：`backend/app/services/browser_activity_runtime.py:117-128,656-670`
- **问题**：SSRF 守卫在校验时解析 DNS，而 Playwright `page.goto` 会再次解析；攻击者可在两次解析之间把域名重绑定到内网/元数据地址。
- **缓解项**：httpx fallback 会对重定向后 URL 复检；默认私网阻断。
- **建议**：将解析后的 IP 钉死后再交给浏览器（与 MCP 客户端 `outbound_url.py` 的 IP 钉死策略一致）。

### SEC-009 — 敏感字段拦截基于选择器文本子串匹配

- **位置**：`backend/app/services/browser_activity_runtime.py:799-807`、`backend/app/tools/browser_tools.py:218-220`
- **问题**：对 `password`/`token` 等敏感字段的拦截是对**选择器文本**做子串匹配。使用通用选择器（如 `#f1`、`input:nth-child(3)`）即可绕过，从而对密码/支付字段进行写入。
- **建议**：在执行端按元素的实际 `type=password`/`autocomplete` 语义判定，而非选择器字符串。

### SEC-010 — 桌面端 IPC 后利用放大面

- **位置**：`desktop/src/main/desktopApiToken.ts:142-187`、`desktop/src/main/ipc.ts:382-388`（`skillsImport` 接受渲染层任意路径、无 picker 授权）、`341-379`（`cleanupExecute`/`cleanupRollback`/`commandsExecute` 无原生确认）、`534-542`（带 nonce 时策略改写无二次原生确认）、`815-833`（`showItemInFolder`/`getFileIcon` 默认根目录无 picker 授权）
- **问题**：桌面 token 在非 Windows 平台明文存储；多个敏感 IPC 动作缺少原生确认。均非远程未认证 RCE，而是**渲染层被 XSS 攻陷后的放大面**。
- **建议**：危险动作统一加原生确认；`skillsImport` 走文件选择器授权；非 Windows 平台对 token 文件加密或限制 ACL。

### SEC-011 — 移动端 Android 信任用户安装的 CA（LAN MITM）

- **位置**：`mobile/plugins/withAndroidRemoteControlHardening.js:11-15`
- **问题**：网络安全配置 `<certificates src="user" />` 信任用户安装 CA；若设备装有恶意 CA，LAN WSS 可被中间人。配对指纹 UI 仅在后端声明 `requiresTrust` 时触发，无法覆盖任意 user-CA MITM。
- **建议**：对 LAN WSS 走证书钉扎（pinning）或仅信任系统 CA + 显式指纹确认。

### SEC-012 — 部分桌面专用接口返回原始本机路径/按文件名取字节

- **位置**：`backend/app/api/routes_tasks.py:1147-1153`（rollback-preview 含原始 `rollback_info` 路径）、`1064-1081`（artifacts 返回真实本机路径）、`1092-1106` + `task_recording_service.py:59,122`（按猜测的 `file_name` 取录屏字节）
- **问题**：这些接口文档标注为桌面专用，但与脱敏后的 replay 共用同一 router；录屏可被按 `{step_id}-{phase}-{timestamp}.png` 文件名猜测取回。
- **缓解项**：默认受桌面 token 约束；公开 timeline 已隐藏文件名/URL/recording_id。
- **建议**：对这些接口加显式桌面专用门禁或签名资源（参考 `desktop_api.signed_desktop_resource_query`），录屏改为签名 URL 而非可猜文件名。

---

## 🟢 低严重度 / 代码卫生

- **SEC-013**：启用 `LENGRVIS_ALLOW_UNSAFE_LOCAL_SKILL_EXECUTION` 时本地 Python/Shell 技能**无沙盒**，以用户身份继承 `PATH/USERPROFILE` 运行（`backend/app/skills/sandbox.py:36-44,209-242`）。属显式 opt-in/有文档，但建议默认更强隔离或更醒目的风险提示。
- **SEC-014**：`use_system_browser=True` 会用用户真实浏览器 profile（cookie/SSO）打开 URL，可能"骑乘"已认证会话（`backend/app/tools/browser_tools.py:119-120`）。受桌面 API 访问约束。
- **SEC-015**：`GENERIC_TOKEN_PATTERN` 要求 24+ 字符，短 token（如 8 位配对码、点分 JWT 段）可能漏脱敏（`redaction.py:65`）；配对码主要靠键名作用域脱敏覆盖。
- **SEC-016**：PEM 私钥正则 `-----BEGIN ... PRIVATE KEY-----.*?-----END...`（`re.S`）在构造输入下存在回溯（ReDoS）风险，实际风险低（本机诊断、长度有界）（`redaction.py:58`）。
- **SEC-017**：`GET /api/system/diagnostics` 在脱敏后**有意**重新注入 `local_paths`（data_dir/database/log_dirs 绝对路径）（`routes_system.py:284-324`）。属 `diagnostic_scope=local_only`、桌面 token 约束的设计行为；导出包路径已替换为标签。建议确认产品边界并在文档中明确。
- **SEC-018**：MCP 工具参数不做本地 schema 校验，转发给远端（`backend/app/mcp/client.py:73-90`）；信任边界依赖远端 + 本地 R4 策略。
- **SEC-019**：技能 `legacy.unspecified` 权限会弱化 `_has_high_risk_permission`，R3 工具可仅凭告警安装（`backend/app/skills/loader.py:219-272`）。
- **SEC-020**：隐私擦除 `POST /api/system/privacy/erase-local-data` 需确认词（已实现，良好），但日志目录不删除（返回 `manual_cleanup`）、内存运行/编排缓存与 DB 外文件不清理（`routes_system.py:206-263`）。

### 工程化与性能（与 `docs/code-review-2026-06-11.md` 重叠，已被既有 review 跟踪）

- 同步工具执行/SQLite/ONNX/OCR 在 async 事件循环线程内阻塞（`tool_runtime.py`、`onnx_provider.py` 等）——属全局性能问题。
- SQLite 每操作新建连接、缺 WAL、`init_db()` 在多处热路径重复执行。
- Python 侧缺 ruff/mypy/pre-commit 静态检查链（`.pre-commit-config.yaml` 仅 backend ruff）。

### 既有已知项（来自仓库文档，本轮确认仍存在）

- `AGENT_REVIEW_ISSUES.md` 中 ORCH-002（时间线事件回填，已接受）、ORCH-003（同步 resume 线程无法被 `Future.cancel` 中断）、UI-001（无活动任务时轮询计时器未拆除）仍为 open follow-up。
- README 记录的已知失败：`test_browser_writes.py::test_browser_act_is_classified_by_nested_action_kind`（嵌套 `browser.act` observe 动作风险分级与 PolicyEngine 预期不一致）——与 SEC-009 的 args-only 分级（`policy_engine.py:297-298,556-557`）相关，建议一并处理。

---

## 已验证的强防御（供回归保护参考）

| 控制 | 位置 |
| --- | --- |
| 模型无法注入审批/控制字段 | `policy/model_boundary.py:8-27,113-117` |
| HMAC 审批绑定（task/step/tool/canonical args）+ 原子消费 | `policy/approval_binding.py:76-93`、`db.claim_approval_for_execution` |
| settings/permission policy 指纹绑定防 TOCTOU | `orchestration/handlers/step_execution_handler.py:454-462` |
| R4 硬拒绝、不可下调 | `policy/dynamic_risk.py:70-76`、`policy/policy_engine.py:379-390` |
| 未知/低信任工具 fail-closed | `policy/policy_engine.py:429-460` |
| 移动 scope 隔离 + 每消息 grant 实时校验 | `security/mobile_jwt.py:50-68,205-233`、`api/routes_remote.py:216-217` |
| WS 鉴权用 subprotocol、拒绝 query token、accept 前鉴权 | `security/mobile_jwt.py:101-110`、`api/routes_mobile.py:302-308` |
| MCP/浏览器 SSRF：IP 钉死 + 禁重定向 + 元数据阻断 | `core/outbound_url.py:75-167`、`mcp/client.py:96-107` |
| Electron 三件套 + 严格 CSP + IPC 白名单 + token 不下发渲染层 | `desktop/src/main/main.ts:54-90`、`desktop/index.html:5-8`、`desktop/src/main/ipc.ts:1704-1729` |
| 本机密钥 DPAPI 加密 + 原子写 + `0600` | `security/local_secret.py:35-63` |
| 配对单次使用原子化 + TTL + LAN 强制 HTTPS | `services/mobile_pairing_service.py:124-171`、`security/lan.py:66-67` |
| 任务 timeline/replay/explain 公开脱敏 | `api/routes_tasks.py` `_public_*` 系列 |
| 诊断导出分层脱敏 + fail-closed `public_safe=false` | `api/routes_system.py:398-687` |
| 审计写路径入库即脱敏 | `core/audit.py` |

---

## 优先处理建议（按 ROI 排序）

1. ~~**SEC-001**：补齐 run 引擎时间线/进度/状态的公开脱敏，剔除 `state._runtime`/原始 `run.message`（与既定脱敏契约一致）。~~ **已修复（2026-06-12）**，见上文 SEC-001 状态。
2. **SEC-002 / SEC-003**：把审批 HMAC 校验下沉为工具/runtime 硬前置；统一字符串脱敏走 `redact_public_text`。
3. **SEC-004 / SEC-005 / SEC-006**：提升配对码熵 + 持久化限速；移动 JWT 引入 `jti`/吊销或更短 TTL；远程输入 grant token 轮换作废。
4. **SEC-008 / SEC-009**：浏览器 SSRF IP 钉死；敏感字段按元素语义而非选择器文本判定（并修复 README 已知失败用例）。
5. **SEC-007 / SEC-010 / SEC-011 / SEC-012**：zip 解压上限、桌面危险 IPC 原生确认、移动端 CA pinning、桌面专用接口签名资源化。

> 说明：本报告为只读审计，不含代码改动。以上均为可操作的整改方向，建议按攻击面优先级分批落地并补充对应回归测试。
