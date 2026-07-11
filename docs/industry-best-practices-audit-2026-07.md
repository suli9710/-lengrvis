# Lengrvis 行业最佳实践调查

**调查日期**：2026-07-11

**调查范围**：Windows-first 本地 OS Agent、FastAPI 后端、Electron 桌面端、Android Companion、智能体安全、隐私、供应链、测试与发布。

**仓库快照**：`307c968e421131fa7ce62afdadef404ff02e94a6`，基于当前 dirty working tree；结论反映本机工作区，不等同于该 commit 的可复现状态。

**结论口径**：工程最佳实践调查，不是渗透测试、法律意见、认证结论或发布签收。

**修复后复查**：2026-07-11 已对后续修改重新检查，最新结论见第 10 节。第 1-9 节保留为修复前基线，其中代码行号属于当时工作区状态；第 10 节优先级和证据取代旧结论。

## 1. 执行摘要

Lengrvis 已经明显超过常见早期 OS Agent 原型的安全成熟度。其优势不是单一的“审批弹窗”，而是已经形成一条较完整的确定性控制链：风险分级、dry-run、单次审批绑定、路径沙盒、SSRF 防护、Electron IPC 边界、移动端 TLS/证书指纹、审计链、扩展签名和 fail-closed 发布证据。

当前最需要补齐的不是更多通用安全开关，而是四个 Agentic AI 特有的系统性能力：

1. **统一的非可信内容传播模型**：目前浏览器内容已有专门信任标签和注入检测，但文档、RAG、MCP、工具输出、长期记忆尚未共享同一套 provenance/taint 语义。
2. **长期记忆污染防护**：任务完成后会自动写入摘要和 lesson，随后又直接参与新任务规划；缺少来源可信度、隔离域、TTL、晋升审核和污染撤销。
3. **代码执行的 OS 级隔离**：本地 Skill 执行默认关闭是正确的，但一旦开启，当前主要依赖路径、环境变量、命令和超时控制，仍以当前用户权限运行，并不构成 Windows OS sandbox。
4. **真实运行证据**：真实 LLM 对抗评测、第三方渗透测试/专项 fuzz、clean-machine Windows、真实 Android LAN/WSS 证据仍未完成，仓库自身也已将这些列为发布阻断项。

综合判断：**架构与控制面达到“强 Beta”水平，公开/付费 GA 的主要障碍是 Agentic 风险闭环和候选版本证据，而不是基础工程能力。**

## 2. 行业共识

本次调查将 OWASP Agentic Top 10 2026、NIST AI RMF、NIST GenAI Profile、Electron Security Checklist、MCP Security Best Practices、Microsoft Windows 隔离机制、OWASP MASVS、NIST SSDF、SLSA 与 CISA Secure by Design 归纳为九条共同原则：

1. **Least Agency**：只在确有价值时赋予自主性，默认只读，工具和数据范围按任务最小化。
2. **所有自然语言和外部内容默认不可信**：网页、文档、RAG、邮件、API、MCP 和 Agent 消息都不能直接改变目标或授权动作。
3. **规划与执行分离**：LLM 可以建议，独立策略引擎必须在执行点重新验证权限、参数、资源状态和用户意图。
4. **高影响动作绑定明确意图**：审批应绑定目标、计划版本、参数、资源范围、策略版本、有效期和单次 nonce，而不是只绑定按钮点击。
5. **强隔离和短期身份**：代码、Skill、浏览器和外部工具在最小权限沙盒中运行；凭据按任务、用途和时限签发。
6. **记忆是安全边界**：记忆写入需要 provenance、隔离、保留周期、污染检测和可撤销机制。
7. **可观测、可评测、可停机**：记录目标、计划、工具、策略决策、结果和传播链；设置预算、熔断、kill switch 和回滚。
8. **供应链必须可追溯**：依赖、模型、Prompt、Skill、MCP、构建与发布产物需要清单、签名、provenance 和撤销能力。
9. **隐私默认化**：数据最小化、明确云端边界、加密存储、分级保留、完整删除/导出和人工外发复核。

## 3. 当前成熟度矩阵

| 领域 | 当前成熟度 | 已有证据 | 主要差距 | 优先级 |
| --- | --- | --- | --- | --- |
| 风险分级与策略执行 | 强 | R0-R4、独立 PolicyEngine、ToolRuntime、dry-run、R4 拒绝 | 需要把目标/计划版本纳入统一 intent binding | P1 |
| 审批安全 | 强 | 参数、预览、设置、权限策略 HMAC；审批原子单次消费 | 尚未显式绑定用户目标摘要、计划 revision 和内容 provenance | P1 |
| Prompt injection / 目标劫持 | 中上 | 浏览器内容信任标签、注入信号、post-tool review | 保护主要集中在浏览器，未覆盖所有外部内容来源 | P0 |
| 长期记忆 | 偏弱 | 本地存储、删除接口、embedding recall | 自动持久化、全局召回、无 TTL/信任级别/晋升审核 | P0 |
| 代码与 Skill 执行 | 中 | 默认关闭不安全本地 Skill、命令/路径/env/超时约束 | 开启后没有 AppContainer/受限令牌/Job Object/网络隔离 | 条件 P0 |
| 自治预算与熔断 | 中上 | per-plan turn 上限、工具超时、恢复重试上限、任务并发池、取消 | 缺每个 run 的写操作/外发/子进程预算、重复动作检测和统一熔断策略 | P1 |
| Electron 安全 | 强 | contextIsolation、sandbox、nodeIntegration=false、IPC sender 校验、CSP、Fuses、权限默认拒绝 | renderer 通用 API 桥采用 `/api/*` + denylist；新增敏感路由可能未被显式拦截；内置网页下载未单独拒绝 | P1 |
| 本地 API 与网络 | 强 | loopback token、CORS allowlist、WS Origin、LAN guard、DNS/IP pinning SSRF | 可增加 Host allowlist 和统一 egress policy 作为纵深防御 | P2 |
| Android Companion | 中上/Preview | HTTPS/WSS、逐主机证书指纹、SecureStore、设备撤销、短期远控 grant | 7 天 HS256 主 token；高风险批准无设备凭据 step-up；TLS pin 缺过期/定向撤销；APK gate 未验证发布证书 | P1 |
| 审计与可观测性 | 中上 | HMAC 链、敏感记录完整性、trace/span、Prometheus、脱敏 | 本机同用户攻击者仍可重算本地链；缺外部锚定和正式 IR 演练 | P1 |
| 供应链与发布 | 中上 | hash lock、SBOM、CodeQL、gitleaks、Skill Ed25519、签名构建门禁 | 缺 SLSA provenance、可复现构建证据、Prompt/工具运行时 attestation；Android gate 未钉住 APK 签名证书 | P1 |
| 真实评测与发布证据 | 中 | 大量单测、golden tasks、smoke、fail-closed evidence dashboard | 真实 LLM、clean-machine、真实设备、外部 pentest/fuzz 未完成 | P0 |
| 隐私与合规运营 | 中 | local-first、录屏 opt-in、擦除、诊断脱敏、ROPA 草案 | 数据加密、自动保留/清理、完整导出、PIA、法务与运营责任未闭环 | P1 |

## 4. 做得好的实践

### 4.1 独立策略层，而不是依赖 Prompt 自律

`backend/app/policy/policy_engine.py`、`backend/app/orchestration/tool_runtime.py` 和执行 handler 将模型建议与真实副作用分开。浏览器内容出现注入信号时，post-tool review 会拒绝继续执行，而不是要求模型“忽略恶意指令”。这符合 OWASP ASI01、ASI02 和 ASI08 对独立策略执行的要求。

### 4.2 审批具备抗重放和抗篡改属性

`backend/app/policy/approval_binding.py:81` 将 task、step、tool 和 canonical args 绑定；同文件还绑定预览、设置与权限策略版本。`backend/app/core/db_approvals.py` 在副作用前原子消费审批。相比只传 `approved=true` 的常见实现，这一设计成熟得多。

### 4.3 Electron 主渲染器与嵌入网页均有独立硬化

`desktop/src/main/main.ts:124` 使用 `contextIsolation: true`、`nodeIntegration: false`、`sandbox: true`；`desktop/src/main/rendererTrust.ts` 校验 IPC sender；`desktop/src/main/browserHostWebContentsHardening.ts` 对远程网页拒绝 popup、权限和越界导航；`desktop/electron-builder.yml` 关闭危险 Fuses 并启用 ASAR 完整性和 cookie 加密。

### 4.4 网络边界采用 fail-closed 和 DNS pinning

`backend/app/security/desktop_api.py` 对除匿名 health 外的桌面 API 要求本地随机 token，WebSocket 同时检查 token 与 Origin。`backend/app/core/outbound_url.py` 在连接前解析并固定目标 IP，避免 DNS rebinding TOCTOU；这与 MCP Security Best Practices 的 SSRF 建议高度一致。

### 4.5 移动配对不是“局域网明文 + 长 token”

`backend/app/services/mobile_pairing_service.py:117` 使用 5 分钟单次 pairing code 和额外 32-byte claim secret；非 loopback 配对要求 TLS ready。移动端在 `mobile/src/store/auth.ts:98` 将 token 放入 SecureStore，并保存/恢复证书信任状态；远程输入另有短期 grant、scope、撤销和 active-grant 绑定。

### 4.6 发布证据明确区分“代码存在”和“能力已验收”

`docs/release/release-readiness-dashboard.md`、`docs/qa/agentic-product-evals.md` 与 evidence verifier 明确区分机器证据、人工证据、模板、waiver 和 owner sign-off。这符合 NIST GenAI Profile 对 pre-deployment testing、incident evidence 和治理职责的要求，也有效降低发布材料夸大的风险。

## 5. 关键差距与建议

### P0-1 建立正式威胁模型与控制映射

仓库已有安全白皮书和大量安全测试，但未发现覆盖完整信任边界的数据流威胁模型。建议新增版本化 threat model，至少覆盖：

- 用户、Electron renderer、main/preload、FastAPI、SQLite、LLM provider、浏览器内容、文件/文档、Skill、MCP、移动设备和 LAN。
- 恶意网页/文档、恶意 MCP/Skill、被攻陷 Provider、同用户恶意进程、LAN MITM、丢失手机、更新链攻击。
- OWASP Agentic Top 10 ASI01-ASI10 与现有控制、测试、owner、残余风险的逐项映射。
- 每次引入新工具、权限、数据源、Agent 或远程通道时必须更新的变更门禁。

**验收标准**：每个 trust boundary 至少有 threat、control、test/evidence、owner、residual risk；发布 gate 能检查文档版本与当前能力 manifest 一致。

### P0-2 将浏览器信任标签扩展为统一 provenance/taint 系统

当前 `content_trust` 和 `browser_content_warnings` 只覆盖浏览器路径。文档、OCR、RAG、MCP、HTTP Skill、Agent 消息和工具结果也可能携带间接 Prompt Injection。

建议为所有进入模型或计划器的内容建立统一 envelope：

```text
source_kind, source_id, origin, content_hash,
trust_level, taint_flags, observed_at,
task_scope, user_confirmed, sanitizers_applied
```

执行策略应遵循：

- 非可信内容可用于提取事实，但不能直接扩大目标、权限、收件人、目标路径或工具集合。
- tainted 数据流入写操作、外发、凭据域、MCP 跨服务器调用时重新审批。
- 工具 A 的输出传给工具 B 时保留 provenance，不能因经过模型重写而“洗白”。
- 对文档、网页、OCR 和 MCP 分别建立 adversarial corpus。

### P0-3 重构长期记忆为“隔离、检疫、晋升”模型

`backend/app/orchestration/handlers/completion_handler.py:36` 会在任务完成后自动保存 task summary 和每个成功 step 的 lesson。`backend/app/agents/orchestrator_agent.py:485` 又将这些记忆直接召回到后续规划。`backend/app/agents/memory_agent.py` 当前没有 TTL、信任级别、来源哈希、隔离域或审核状态。

建议：

- 默认关闭从外部内容自动晋升为长期记忆，先写入 quarantine。
- 记忆字段增加 `provenance`、`trust_level`、`scope`、`expires_at`、`review_status`、`supersedes`。
- 只允许“用户明确确认”或“确定性系统事实”进入高信任长期记忆。
- lesson 与普通偏好分库存储；高风险任务、被拒绝任务、含注入警告任务不得自动学习。
- 召回时按用户/会话/领域/工具/任务风险隔离，并将记忆视为建议，不作为权限依据。
- 提供“本任务不学习”“查看来源”“撤销这条记忆”“清空某领域记忆”。

**短期止血**：在完整模型上线前，公共版本可关闭自动 lesson 写入，仅保留用户主动保存的偏好。

### 条件 P0-4 本地代码与 Skill 执行必须进入 Windows OS sandbox

`backend/app/skills/sandbox.py:263` 已诚实说明本地 Python/Shell handler 没有 OS sandbox，因此默认关闭。这一默认值应保持为 release blocker：**没有 OS 隔离时，不要为普通用户开启任意本地 Skill 或生成代码执行。**

推荐的 Windows 执行 broker：

- AppContainer 或受限令牌，默认无用户凭据、无注册表和无任意文件访问。
- Job Object 限制进程树、CPU、内存、句柄、运行时间，并在取消时整体终止。
- 显式挂载只读输入与单一可写工作目录；禁止继承敏感 handles。
- 默认无网络，按工具声明启用目标 allowlist；所有外联走 host broker。
- 凭据保留在 host，永不注入生成代码；子进程只看到能力 stub。
- 对 PowerShell、Python、Node、Office COM 分别定义 profile，不使用一个“万能沙盒”。

### P0-5 完成真实 LLM、对抗安全和外部测试证据

仓库已有正确的 stop-ship 定义，应按现有 dashboard 完成，而不是降低门槛：

- 30+ 真实任务只是最低线；建议形成 100+ 基准集，分 read、write、browser、document、memory、mobile、developer。
- 注入语料覆盖网页隐藏文本、PDF/Office 指令、OCR、MCP tool poisoning、跨 Agent 消息和记忆污染。
- 记录 safety false positive、false negative、任务完成率、人工改写率、审批疲劳和回滚成功率。
- 第三方测试重点放在审批绕过、路径/链接逃逸、移动 LAN/WSS、更新链、Skill/MCP 和同用户本地攻击面。
- fuzz 目标包括 approval schema、IPC payload、MCP JSON-RPC、归档/ZIP、URL 解析、路径规范化和 WebSocket 状态机。

### P1-1 引入 intent capsule 和任务级短期授权

现有审批绑定很强，但没有显式绑定用户目标 digest、计划 revision 和内容 provenance。建议在每个执行周期携带签名 intent capsule：

```text
task_id, user_goal_digest, plan_revision,
allowed_tools, resource_scope, data_egress_scope,
policy_version, expires_at, nonce
```

每次高影响工具调用都验证 capsule；目标漂移、计划重写、来源信任变化或 scope 扩大时使旧审批失效。对云 API、MCP 和移动授权逐步采用短期、task-scoped、audience-bound token。

### P1-2 完成敏感数据加密与保留周期

本机 SQLite 主要依赖文件权限，任务正文、记忆、索引、审批和录屏并未做应用层 at-rest encryption。建议：

- 优先加密 task recording、移动设备材料、诊断草稿和高敏感正文；密钥由 DPAPI 包装。
- 评估 SQLCipher 或“可搜索索引 + 加密原文分离”，避免直接破坏 FTS。
- 为 task、chat、memory、index、recording、logs、diagnostics 定义默认 TTL 和容量上限。
- 把日志自动清理、完整数据导出和 retention review 纳入发布证据。
- UI 在发送云端前显示 provider、数据类型和是否含文件正文，而不是只显示抽象模式名。

### P1-3 将供应链从“锁定”提升到“可证明构建”

现有 hash lock、SBOM、签名 Skill 和代码签名门禁是良好基础。下一阶段建议达到至少 SLSA Build L2：

- hosted builder 自动生成并签名 provenance，包含 commit、builder、inputs、workflow 和产物 digest。
- 发布端验证 provenance、SBOM attestation、backend binary 与 installer 的绑定关系。
- Prompt、tool schema、policy、Skill、MCP 配置按内容 hash 和版本进入 capability manifest。
- 支持按 key/tool/server/prompt hash 紧急撤销和 kill switch。
- 长期目标是隔离构建、最小化签名密钥暴露并探索可复现构建。

### P1-4 补齐 Incident Response 与审计外部锚定

本机 HMAC 链可以发现普通篡改，但拥有同一 Windows 用户权限的攻击者可能读取 DPAPI 保护的密钥并重算本地记录。因此对外口径应保持“tamper-evident”，不要宣称绝对 tamper-proof。

建议：

- 定期将链头签名到 Windows Event Log、企业 SIEM、用户导出文件或可选远端透明日志。
- 建立 tool/MCP/Skill revoke、密钥轮换、设备吊销、版本隔离、自动更新暂停和数据保全 runbook。
- 每个安全事件记录 detection、containment、eradication、recovery、用户通知和复盘。
- 每个 RC 至少演练一次“恶意 Skill”“被盗手机”“坏更新”或“Prompt Injection 导致越权尝试”。

### P1-5 移动身份升级为设备绑定的短 access token

当前主 token 使用 HS256，TTL 为 7 天，并可在仍有效时刷新。Preview 阶段可接受，但 GA 建议：

- 15-60 分钟 access token + 旋转 refresh token。
- Android Keystore 生成设备私钥，使用 proof-of-possession 或 mTLS 绑定设备。
- 服务端保存 per-device public key、token family 和 rotation/reuse detection。
- 高风险审批需要设备解锁/生物识别 step-up；远程输入继续使用独立短期 grant。当前 `mobile/src/screens/ApprovalDetail.tsx:109` 可直接提交决定，而 `mobile/src/store/auth.ts:208` 的 SecureStore 读取未设置 `requireAuthentication`，因此 bearer token 所在设备处于解锁状态时没有第二道用户在场证明。

### P1-6 将 renderer 通用 API 桥改为 method + route allowlist

`desktop/src/main/ipcBackendHandlers.ts:48` 暴露通用 `apiRequest` IPC；`desktop/src/main/ipc/validation/apiRequestUrl.ts:74` 允许所有 `/api/*` 路径，再由 `desktop/src/shared/ipc.ts:203` 的 denylist 排除已知敏感前缀。这对当前路由有效，但安全性依赖开发者每次新增后端端点时同步更新拒绝表；新增写操作或高影响路由可能默认穿过通用桥，绕过专用 IPC、参数契约或本机确认。

建议：

- 通用桥只接受显式登记的 `{method, normalized_route}` allowlist，未知路由默认拒绝。
- 所有副作用、高敏感读取和桌面原生动作使用独立 typed IPC handler，并在 main 进程执行 schema 校验与必要的 native confirmation。
- CI 从 FastAPI 路由清单生成/比对安全清单；出现新的非只读路由而没有 owner、风险等级和 bridge policy 时失败。
- 对动态路径使用模板级匹配，不允许通过编码、尾斜杠或路径参数绕过策略。

**验收标准**：新增任意后端路由时，renderer 默认不可访问；只有经过审查的 method + route 条目才能通过通用桥。

### P1-7 验证 Android 发布证书，并治理 LAN TLS pin 生命周期

当前严格 Android gate 在 `scripts/verify_android_release_gate.ps1:759` 计算 hash、验证 APK ZIP 头和结构，但未调用 `apksigner verify --verbose --print-certs` 验证签名 scheme、证书身份和发布证书 digest。脚本中的 `valid_signature` 指 reviewed-evidence HMAC，不等同于 APK 代码签名验证。

同时，`mobile/android/app/src/main/java/com/lengrvis/approval/LengrvisLanTrust.kt:32` 会把新指纹追加到 host 数组；现有接口只能清空全部 trust，缺少单个 host/pin 的过期、替换和定向撤销。证书轮换后，旧证书可能长期继续被接受。

建议：

- release gate 使用 Android SDK `apksigner` 验证 v2/v3 等预期 scheme，并把 signer certificate SHA-256 与受控 release identity 比对。
- 将 package name、version code、artifact digest、signer digest 和构建 provenance 绑定到同一发布证据。
- 每个 host 维护 `active`、可选的短期 `next`、`created_at`、`expires_at` 和来源设备；轮换完成后自动撤销旧 pin。
- 提供逐 host/逐 pin 撤销、异常多 pin 告警和 UI 可见的证书变更确认；避免无限追加。

### P1-8 把分散的运行上限升级为统一自治预算

项目并非没有熔断：`backend/app/orchestration/os_execution_engine.py:180` 有 per-plan turn 上限，`backend/app/orchestration/tool_runtime_execution.py:121` 有工具超时，`backend/app/orchestration/handlers/recovery_handler.py:123` 限制恢复重试，`backend/app/services/task_pool.py:23` 限制并发并支持取消。差距在于这些控制分散，尚未形成按 run 统一核算、可由策略层消费的副作用预算。

建议为每个 run 建立不可由模型扩大的 budget ledger，至少包含：

- 总工具调用、写操作、删除、网络外发、不同目的域、创建子进程、UI 输入和并行 fan-out 上限。
- CPU/内存/墙钟时间、连续失败、相同动作重复、计划 revision 次数和累计重试上限。
- 达到软阈值时暂停并解释；达到硬阈值时 fail closed，取消任务、终止完整进程树并写入审计。
- 用户追加预算时签发新的短期 capability，不修改旧审批；高风险预算不能用模型自动续期。

**验收标准**：构造循环计划、工具连续失败、跨 Agent 扩散、批量外发和子进程爆发场景，均能在确定性阈值内停止且不遗留后台工作。

### P2-1 将打包 renderer 从 `file://` 迁移到受控自定义协议

Electron 官方建议避免 `file://` 并优先使用自定义协议。项目已在 `rendererTrust.ts` 预留 `app://local`，可进一步注册 privileged protocol、用响应头下发 CSP，并限制路径解析。该项属于纵深防御，不应挤占前述 P0 工作。

### P2-2 标准化 Agent telemetry

现有自研 trace/span 和 Prometheus 足以支持本地诊断。企业化时可增加 OpenTelemetry/GenAI 语义映射，重点输出低敏感指标：goal/run id、tool、policy verdict、latency、token、retry、approval、rollback、taint propagation 和 memory promotion。默认不采集正文、原始参数、截图或凭据。

### P2-3 内置不可信浏览器默认拒绝下载

`desktop/src/main/browserHostWebContentsHardening.ts:8` 已拒绝 popup、权限和越界导航，但未注册 session `will-download` handler。建议对该隔离浏览器会话默认 `preventDefault()`；确需下载时走显式 broker，展示来源、文件名、类型、大小和落盘位置，并复用路径策略、恶意文件扫描和用户批准。这样能确保“浏览网页”不会隐式变成文件写入能力。

## 6. 分阶段路线图

### 发布前 / 0-2 周

1. 完成 threat model 与 OWASP ASI01-ASI10 control map。
2. 暂停自动长期 lesson 晋升，或至少阻止来自 browser/document/MCP 的内容进入高信任记忆。
3. 定义统一 provenance/taint schema，并先覆盖 Browser、Document、MCP 和 Memory 四条主链。
4. 保持 unsafe local Skill、任意生成代码执行在 release profile 中关闭。
5. 将 renderer 通用 API bridge 改为 method + route allowlist，并为路由漂移增加 CI gate。
6. 在 Android release gate 中验证 APK signer certificate 与签名 scheme。
7. 完成现有 RR-P0 clean-machine、real LLM、Android、diagnostics 和 owner sign-off。

### 首个稳定版 / 2-6 周

1. 上线 memory quarantine、TTL、scope、用户确认和污染撤销。
2. 将 goal digest、plan revision 和 provenance 纳入 approval/intent binding。
3. 实现 Windows sandbox broker 原型，至少覆盖 Python/PowerShell Skill。
4. 落地数据 retention、日志自动清理、敏感表/字段加密方案。
5. 增加 Agentic red-team corpus 和 CI differential eval。
6. 为移动高风险批准增加设备凭据/生物识别 step-up，并实现 TLS pin 过期、轮换和定向撤销。
7. 对 Electron 内置浏览器下载采取默认拒绝和显式 broker。
8. 上线 per-run budget ledger、重复动作检测和统一 fail-closed 熔断。

### 企业化 / 6-12 周

1. SLSA Build L2 provenance、SBOM attestation 与发布验证。
2. per-device asymmetric mobile identity、短 token 和 step-up auth。
3. 审计外部锚定、SIEM/OTel 可选出口和安全事件演练。
4. Prompt/tool/policy capability manifest、运行时 attestation 和统一 kill switch。
5. 第三方渗透测试复测，并将残余风险纳入采购与安全白皮书。

## 7. 不建议采用的做法

- 不要把“系统 Prompt 写了不要被注入”当作控制。
- 不要用一次用户同意覆盖后续所有工具调用或跨任务权限。
- 不要让签名 Skill 绕过运行时 least privilege；签名只证明来源，不证明安全。
- 不要将 MCP 上游 token 原样透传给下游服务。
- 不要把 task completed 等同于 result verified。
- 不要在没有 OS sandbox 的情况下把本地脚本执行包装成“安全沙盒”。
- 不要把本地 HMAC 链描述为能抵御同用户完全控制。
- 不要在诊断、遥测或支持包中默认收集 Prompt、tool args、截图和本机路径。

## 8. 调查方法与限制

本次调查：

- 阅读当前仓库 README、安全白皮书、合规清单、发布 dashboard、agentic eval、供应链和可维护性文档。
- 静态检查后端策略、审批绑定、记忆、Skill、网络、移动配对、Electron main/preload/IPC、更新和存储实现。
- 对照下列官方框架和规范进行差距映射。

本次未执行：

- 全量 pytest、desktop/mobile 全套 gate、依赖漏洞扫描。
- 真实 LLM 规划与工具调用。
- Windows clean-machine、Android 真机 LAN/WSS、MITM 或弱网测试。
- 动态渗透测试、恶意样本执行、fuzz 或恶意 Skill/MCP 实战。
- 法律、隐私或认证审计。

## 9. 主要来源

1. [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
2. [OWASP Securing Agentic Applications Guide 1.0](https://genai.owasp.org/resource/securing-agentic-applications-guide-1-0/)
3. [OWASP Top 10 for LLM Applications 2025](https://genai.owasp.org/llm-top-10/)
4. [NIST AI Risk Management Framework 1.0](https://www.nist.gov/itl/ai-risk-management-framework)
5. [NIST AI 600-1 Generative AI Profile](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)
6. [Electron Security Checklist](https://www.electronjs.org/docs/latest/tutorial/security)
7. [Model Context Protocol Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)
8. [MCP SEP-1024 Local Server Installation Security](https://modelcontextprotocol.io/seps/1024-mcp-client-security-requirements-for-local-server-)
9. [Microsoft AppContainer Isolation](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation)
10. [Microsoft Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
11. [OWASP MASVS](https://mas.owasp.org/MASVS/)
12. [Android Network Security Configuration](https://developer.android.com/privacy-and-security/security-config)
13. [NIST SP 800-218 Secure Software Development Framework](https://csrc.nist.gov/pubs/sp/800/218/final)
14. [SLSA Security Levels](https://slsa.dev/spec/v1.1/levels)
15. [CISA Secure by Design](https://www.cisa.gov/securebydesign)
16. [Android App Signing](https://developer.android.com/studio/publish/app-signing)
17. [Android Biometric Authentication](https://developer.android.com/identity/sign-in/biometric-auth)

## 10. 修复后复查

### 10.1 更新后的总体判断

本轮修改显著提高了确定性控制覆盖率。正式 threat model、OWASP ASI01-ASI10 control map、ContentEnvelope、memory quarantine、release-profile execution isolation、renderer API allowlist、BrowserHost 下载拒绝、移动 refresh token family、TLS pin 生命周期和 Android APK signer gate 均已形成代码与测试证据。

更新后的判断是：**工程控制面已从“强 Beta”推进到“强 Beta+”，但仍不具备公开 RC/GA 签收条件。** 当前 `review:scorecard` 为 94/100，但 7 个 RR-P0 发布证据全部仍为 `in_progress`，RC gate 会正确 fail closed。

### 10.2 原建议闭环状态

| 原建议 | 复查状态 | 已完成 | 仍需处理 |
| --- | --- | --- | --- |
| 正式 threat model / ASI control map | 基本闭环 | 版本化文档、JSON control map、CI/release validator | candidate-bound 安全负责人接受仍是 RR-P0-007 |
| 统一 provenance / taint | 基本闭环 | HMAC envelope、工具输出分类、服务端依赖 observation 传播、审批后参数整体绑定、side-effect sink 错配拒绝 | 任意多跳模型改写的字段级 JSON Pointer lineage 尚未完全自动化 |
| Memory quarantine / TTL / promotion | 基本闭环 | 自动学习默认关闭、非用户内容隔离、TTL、promote/revoke API；recall 校验 HMAC、正文 hash、确认状态与 scope，失败自动 quarantine | 桌面端仍无检疫审阅/晋升 UI |
| Intent capsule 与自治预算 | 基本闭环 | automation 与普通核心 task 均使用服务端 capsule/budget；结构性 plan mutation 递增 revision；审批绑定 goal digest/revision/provenance | 无持久化 plan 的低层测试/工具 harness 不自动建 ledger；预算追加 capability UI 尚未实现 |
| Windows OS sandbox | 仅风险缓解 | 打包/release profile 无 attestation 时禁止危险执行 | 仓库仍无 AppContainer/受限令牌/Job Object/network broker host |
| Renderer API bridge allowlist | 基本闭环 | method + route allowlist、未知路径默认拒绝、编码路径防护 | 动态模板可能碰撞未来静态路由；CI 未对照真实 FastAPI 路由图 |
| BrowserHost 下载拒绝 | 闭环 | `will-download` 同时 `preventDefault()` 与 `cancel()` | 事件记录仍需更完整地清理预签名 URL 参数 |
| 移动短 token / refresh family | 部分闭环 | 30 分钟 access token、旋转 refresh、复用检测、family/device revoke；R3/执行/权限/凭据审批在可信 PoP 缺失时 fail closed；SecureStore 要求本机认证 | 无 Android Keystore challenge signature/DPoP；refresh token 仍可脱离设备重放 |
| TLS pin 生命周期 | 基本闭环 | 单 active/next、24 小时重叠、30 天期限、定向撤销、损坏状态 fail closed | system-trusted 路径按 host 而非 origin/port 复核；self-signed fallback 未检查证书有效期 |
| Android APK 发布身份 | 闭环 | `apksigner --verbose --print-certs`、v2/v3、单 signer、证书 digest、candidate binding | 真机附件仍需可独立复验的 hash manifest |
| 敏感数据加密与 retention | 部分闭环 | task recording 使用 DPAPI-wrapped AES-GCM；任务明细启动时清理 | tasks/chat/memory/plan/tool/approval 仍明文；retention 未覆盖全部新表 |
| 真实 LLM / clean-machine / 真机 / 外部测试 | 未闭环 | benchmark catalog 与 fail-closed evidence gate 更完整 | 7 个 RR-P0 均未通过，仍缺真实候选证据和 owner sign-off |

### 10.3 当前高价值发现

#### P1-1 Provenance sink 参数错配与已知上游缺失已修复

`ToolRuntime` 现在对需要 revalidation 的 side-effect tool 计算去除运行时控制字段和 provenance 元数据后的完整 canonical args payload，并要求用户确认 envelope 的 `content_hash` 与该 payload 完全一致。良性 envelope 配不同 payload 会 fail closed；无效 HMAC 不能在 revalidation 时被升级。

依赖步骤的 `ToolResult.content_envelope` 也由服务端注入下一步 runtime，而不再依赖模型复制 envelope。审批记录固化上游 provenance，批准执行时对实际参数重新签发 task-scoped 用户确认 envelope。残余风险是任意多跳、非直接依赖和复杂字段重写仍未形成通用 JSON Pointer lineage 图。

#### P1-2 核心 OS Agent capsule、revision 与 budget 已接入

有持久化 Plan 的普通核心 task 现在由 guard 服务端按当前步骤签发短期 capsule，只允许当前 tool、解析出的资源和外发目标，并创建 task 级 budget ledger。客户端不能自行扩大该 scope。

恢复步骤、subagent tool proposal 和 reflection add/replace 等结构性 mutation 会递增 `Plan.version`，并原子过期未消费审批。审批 engineering boundary 新增 task、goal digest、真实 plan revision 与 provenance；执行时 revision 或 goal 不一致会拒绝。

残余风险是无持久化 Plan 的低层 harness 仍不自动创建 ledger，且用户追加预算尚无独立短期 capability 与产品 UI。

#### P1-3 Memory recall 完整性校验已修复

Recall 现在在进入 embedding 排序和 planner prompt 前验证 envelope 存在、HMAC、`content_hash == stable_content_hash(memory.content)`、memory/envelope 用户确认状态、trust level 和 scope。任一失败会把记录改为 `quarantined`、撤销 `user_confirmed` 并记录 `memory.recall_integrity_failed`。

新建无 task_id 的用户记忆会绑定 memory id 作为 scope；损坏 lineage 在用户显式 promote 时会重建为新的 user-revalidated envelope。仍需补桌面端 quarantine 列表、来源查看、promote、revoke 和按来源删除。

#### P1-4 移动高影响审批已 fail closed，设备持有证明仍待实现

后端 step-up 判断现已覆盖所有 R3、destructive、删除、系统写入、进程执行、安装、权限扩大和凭据操作。Fresh biometric claim 还必须同时具备 active device credential、hardware-backed、attestation verified 与匹配 `cnf.jkt`；当前签发链不具备这些条件，因此高影响手机批准确定性拒绝。移动 UI 即使看到 dry-run/scope 也不会放行，token 与 refresh token 的 SecureStore 读写启用了 `requireAuthentication`。

仍需用 Android Keystore 非导出密钥完成 challenge signature/DPoP，并将 refresh family 绑定公钥 thumbprint，届时才能有条件恢复手机端高影响批准。

#### P1-5 Electron 凭据 origin、TOCTOU 与删除语义已修复

- Vault 和 ticket 现绑定规范化完整 HTTPS origin，非默认端口不会与默认 443 共用凭据；旧 hostname 记录仅保守迁移到默认 HTTPS origin。
- Capture/use preview 绑定 `session_id + origin + SHA-256 page/field fingerprint`；native confirmation 后、ticket 签发前、capture/fill 前均重验，页面导航或表单字段替换会拒绝。
- “删除本机数据”现在先清理 BrowserHost session storage、截图、单次 ticket 和 Electron credential vault；任一步失败会抛错，不再返回整体成功。

对应回归覆盖 origin 端口隔离、单次 ticket、确认后 fingerprint 变化拒绝和本机隐私擦除。

### 10.4 次要残余风险

- `desktop/src/shared/apiRequestAllowlist.ts:64` 的动态 segment 可匹配未来同层静态路由；增加 FastAPI/OpenAPI route collision CI。
- BrowserHost 下载事件记录应删除 `sig`、`signature`、`X-Amz-Signature`、`X-Amz-Credential` 等预签名参数。
- `mobile/android/app/src/main/java/com/lengrvis/approval/LengrvisLanTrust.kt:593` 在 system trust 成功后提前返回，最终 hostname verifier 又按 host 查询 pin；应始终按 exact origin/port 校验。
- 同文件 self-signed pin fallback 应在接受前调用证书 `checkValidity()`。
- 应用层加密目前主要覆盖 task recording；SQLite 中 task、chat、memory、plan、tool 和 approval 正文仍依赖 ACL 与 retention。
- Android reviewed evidence 应绑定截图、视频、日志、`adb` 安装状态等附件的 SHA-256 manifest；evidence signing key fingerprint 也应进入签名 payload。

### 10.5 本轮验证

- 本轮跨模块后端聚焦回归：226 tests passed；修改文件 Ruff 全通过。
- 本轮移动安全后端回归：125 tests passed；Mobile typecheck、token 与 remote-input smoke passed。
- Electron Vitest：62 files / 248 tests passed；typecheck 与 `smoke:ipc` passed。
- Desktop dependency audit contract：8 tests passed。
- Mobile typecheck、token/session/remote-input/TLS smoke passed。
- `security:threat-model`、`review:scorecard --allow-dirty`、非严格 `release:readiness` passed。
- `release:readiness:rc` 按预期失败：7 个 RR-P0 均为 `in_progress`，CI evidence、clean worktree、manual sign-off 和 owner signature 未完成。

本轮仍未执行全量 backend pytest、真实 Android connected instrumentation、真实 LLM provider 评测、clean-machine 安装、动态渗透测试或第三方复测。
