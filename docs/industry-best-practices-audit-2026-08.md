# Lengrvis 行业最佳实践调查与差距评估（2026-08-24）

> 调查对象：Windows-first 本地 OS Agent（FastAPI + Electron + Android Companion）  
> 仓库快照：`126a0e07`，分支 `codex/full-code-review-hardening-20260730`  
> 调查性质：外部行业基准复核 + 当前仓库静态证据映射；未执行渗透测试、真实模型评测、干净机器安装或 Android 真机验收  
> 基线：`docs/industry-best-practices-audit-2026-07.md`；本文是面向当前候选决策的增量复核，不重复基线中的全部实现历史

## 1. 执行结论

### 1.1 决策

| 目标 | 结论 | 原因 |
| --- | --- | --- |
| 继续作为 Development Preview | **可继续** | 当前默认关闭危险任意执行，安全边界和恢复骨架明显强于普通原型；README 对限制的表述准确。 |
| 进入公开 Beta / RC | **No-Go** | 当前候选绑定、真实 provider、干净机器、Android 真机、诊断包人工复核、结果质量与 release-owner 签收尚未完成；打包产物的 SQLite 修复版本也尚未被门禁证明。 |
| 对外宣称生产级 MCP 互操作 | **No-Go** | 客户端和 conformance 仍固定在 `2025-11-25`；MCP 已于 2026-07-28 发布破坏性换代。 |
| 开放任意本地代码、通用 Shell/Python 或第三方 Skill 写执行 | **No-Go** | 仓库只有 fail-closed adapter/verifier，没有真正建立 AppContainer、受限令牌、文件/网络 broker 的原生 Windows 隔离宿主。 |

### 1.2 一句话判断

Lengrvis 当前属于**工程控制成熟度中上、候选发布保证未闭环**：策略、审批、执行日志、恢复、Electron 边界、移动令牌和供应链门禁已经形成较强骨架，但真实环境证据、数据耐久性、MCP 新协议、active 数据加密、外部副作用对账和生产可观测性仍未达到公开发布标准。

### 1.3 本轮最重要的新发现

1. **SQLite 候选门禁应立即升为 P0。** SQLite 官方披露，WAL-reset 并发缺陷可能影响 `3.7.0` 至 `3.51.2`，修复于 `3.51.3`，并提供部分旧分支回移。当前本机 `.venv` 实测为 `3.49.1`，且 Lengrvis 明确使用 WAL 与线程级多连接；但正式打包产物版本未知，因此正确结论是“必须从候选二进制证明已修复”，而不是宣称候选已发生损坏。[SQLite WAL 官方说明](https://www.sqlite.org/wal.html#the_wal_reset_bug)
2. **MCP 基准已换代。** 当前代码与合规脚本固定 `2025-11-25`，而 2026-07-28 版取消 handshake/session，加入 stateless request、MRTR、header routing、cache hints 和新的授权要求。这是 7 月基线之后最明确的互操作差距。[MCP 2026-07-28 发布说明](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
3. **自动化通过不等于发布通过。** 仓库记录的 Backend `4142 passed, 16 skipped`、Desktop `98 files / 432 passed, 1 skipped` 证明回归控制很强，但发布 dashboard 的 7 个 P0 仍全部 `in_progress`。机器证据不能替代真实模型、设备、安装和人工审查。
4. **危险执行的当前姿态是正确的。** 原生隔离宿主尚不存在，但 release profile fail closed；只要继续禁用该能力，它是“安全地没有该能力”，不是一个可被测试数掩盖的已完成沙箱。
5. **下一阶段主要是系统化闭环，而非继续堆规则。** 优先方向是候选级证据、SQLite 耐久性、MCP 迁移、统一工具契约、外部副作用 reconciliation、active 数据加密、OTLP/SLO、进程监督和多人发布治理。

## 2. 调查方法与证据边界

### 2.1 调查问题

本轮将“行业最佳实践”拆成五个可验证问题：

1. Agent 的身份、授权、审批和 prompt-injection 防线是否采用确定性控制？
2. 规划、工具执行、故障恢复、幂等和评测是否能支持真实生产失败模式？
3. Electron、Windows 本地服务、SQLite、移动配对与更新链是否符合平台安全实践？
4. 隐私、审计、可观测性、供应链和人机交互是否能形成候选级证据？
5. 当前差距对 Development Preview、公开 Beta 和 GA 分别意味着什么？

### 2.2 方法

- 以 2025–2026 年官方标准、平台文档和一手工程资料为主，覆盖 OWASP、NIST、MCP、Electron、Microsoft、Android、SQLite、OpenTelemetry、SLSA、GitHub、OpenAI 和 Anthropic。
- 深读关键来源中的规范性条款或实现边界，而非只读取搜索摘要。
- 静态抽查当前代码、发布 dashboard、威胁模型、评测合同、现有代码审查记录和依赖配置。
- 将“已存在的控制”“现有文档记录的测试结果”“本轮静态推断”“仍需动态证明”分开陈述。

### 2.3 限制

- 本轮没有重新运行仓库全量测试；测试数字来自 `CODE_REVIEW_REPORT.md:3,374-392`，属于现有仓库证据。
- 没有构建并反编译候选 `backend.exe`，因此不能用本地 `.venv` 的 SQLite 版本代表发布包。
- 没有真实 provider、真实恶意 MCP server、真实隐藏 DOM、物理 Android、干净 Windows profile、签名 RC 或第三方渗透环境。
- NIST TEVV-Athlon 与部分 2026 Agent 身份资料仍处于公开草案/概念阶段；本文将其用作方向性基准，不把草案写成强制合规标准。

## 3. 2026 年行业共识

### 3.1 确定性授权优先于模型自律

NIST 的 Agent Identity and Authorization 概念文件把动态身份、最小权限、proof of authority、代表用户行动、人与 Agent 身份绑定、可验证审计和注入发生后的影响收敛列为核心问题。OWASP Agentic Top 10 同样把工具滥用、身份/权限、供应链、记忆污染、级联失败和人类监督作为独立风险面。[NIST Agent Identity 概念文件](https://www.nccoe.nist.gov/sites/default/files/2026-02/accelerating-the-adoption-of-software-and-ai-agent-identity-and-authorization-concept-paper.pdf)；[OWASP Agentic Top 10 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)

最佳实践不是让模型“记得先询问”，而是由策略层计算有效风险、签发短期且窄范围的能力、将审批绑定到精确参数和当前上下文，并在执行点再次验证。目标、工具、参数、风险或身份变化时，旧授权应失效。

### 3.2 所有外部内容都是数据，不是授权指令

Prompt injection 没有可靠的单点检测方案。浏览器页面、文档、OCR、RAG、MCP 和其他 Agent 的内容应保持 provenance/taint，不能直接改变控制面。检测器只能做告警；真正安全性来自能力约束、来源分区、外发边界、审批和副作用 sink 的失败关闭。[OWASP Securing Agentic Applications Guide](https://genai.owasp.org/resource/securing-agentic-applications-guide-1-0/)；[OpenAI URL 数据外泄防线](https://openai.com/index/ai-agent-link-safety/)

OpenAI 的 URL 安全案例还说明，域名 allowlist 和重定向检查并不足够；自动访问应尽量限制到独立确认已公开的精确 URL，未验证链接需要用户动作。这是防御纵深的一层，不能替代页面内容隔离。

### 3.3 隔离优先于审批疲劳

高影响本地执行需要 OS 级 containment：受限令牌/AppContainer、Job Object、文件 broker、网络 broker、独立工作区和短期凭据。Job Object 能约束进程树和资源，但不能单独提供文件、网络和令牌隔离。[Microsoft AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation)；[Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)

Electron 官方清单要求 sandbox、context isolation、关闭远端 Node 集成、CSP、限制导航/新窗口、校验每个 IPC sender 和优先使用自定义协议。[Electron Security Checklist](https://www.electronjs.org/docs/latest/tutorial/security)

### 3.4 可靠 Agent 需要 durable execution，而不只是重试

工具副作用应使用稳定 execution identity，并明确区分 `prepared`、`executing`、`committed` 与 `outcome_unknown`。超时或崩溃后的未知结果不能盲目重放；必须依靠 provider idempotency key、资源 CAS、receipt 或 reconciliation probe 确认外部状态。补偿动作也必须验证后态，而不是只记录“已调用回滚”。

输入输出 schema、风险、幂等范围、补偿强度和安全重试错误应成为所有工具的可执行合同；空 schema 或只在个别工具里自定义校验会留下跨工具不一致。

### 3.5 评测必须验证真实结果和真实环境

Anthropic 的 Agent eval 实践强调多轮轨迹、环境最终状态、代码/模型/人工多种 grader、多个 trial 以及人工校准；“页面显示成功”不能替代后端状态已改变。[Anthropic Agent Evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)

NIST 2026 TEVV-Athlon 草案提出四阶段、可按真实目标定制的结果评估框架；自动 benchmark 草案要求记录协议、代码/commit、统计不确定性、适当限定结论，并注意污染和可复现性。[NIST TEVV-Athlon](https://www.nist.gov/artificial-intelligence/ai-research/tevv-athlon-framework-evaluating-ai-systems)；[NIST Automated Benchmark Practices 草案](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.800-2.ipd.pdf)

### 3.6 可观测性不能以泄露内容为代价

OpenTelemetry GenAI 语义可统一 agent、model、tool、retrieval 和 token 指标，但规范明确警告 retrieval query、system instructions、tool arguments 和 results 可能含敏感信息。生产默认应只输出低基数、脱敏元数据；prompt、原始参数、截图、文件路径和凭据内容必须 opt-in 且受 retention 控制。[OpenTelemetry GenAI attributes](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)

### 3.7 发布证明必须绑定实际候选

SLSA 1.2 要求一致构建、分发 provenance；更高等级还要求托管、不可伪造 provenance 和跨构建隔离。GitHub artifact attestation 只有在消费者或发布门禁实际验证 subject、builder、workflow 和 digest 时才形成安全价值。[SLSA 1.2 Build Requirements](https://slsa.dev/spec/v1.2/build-requirements)；[GitHub Artifact Attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)

## 4. 当前成熟度矩阵

评级含义：**强**＝默认控制、自动验证和明确残余风险均已存在；**部分达标**＝架构正确但覆盖或真实证据不足；**禁用态达标**＝只有保持能力关闭才安全；**未闭环**＝公开发布前必须补证据。

| 领域 | 当前证据 | 主要差距 | 评级 / 发布影响 |
| --- | --- | --- | --- |
| 策略、审批与能力绑定 | 模型控制字段被剥离；审批绑定 task/step/tool/args/preview/settings/policy/goal；风险和 TTL 在执行点复核 | 用户 permission overlay 在无 allow 规则时仍兼容性 default allow；应迁移到显式 capability allowlist，但它目前不等于绕过整体风险/审批策略 | **强，P1 深化** |
| Prompt injection / provenance | 浏览器、文档、MCP、第三方输出签名为不可信 envelope；父链传播、摘要 lineage 和副作用重确认已有实现 | 执行 guard 使用 `if envelopes`；需用负向测试证明“曾依赖外部内容但 provenance 被移除”必然失败，而不能只证明已有 envelope 会被验证 | **中上，P1 安全不变量** |
| 任意代码 / Skill 执行 | release profile 缺完整 attestation 时禁用；host/policy digest、签名挑战和 adapter fail closed | 仓库没有真正的 AppContainer/受限令牌/文件与网络 broker | **禁用态达标；启用为条件性 P0** |
| 执行日志、恢复、回滚 | 稳定 execution key、journal、CAS、`outcome_unknown`、启动恢复、文件后态验证 | 外部系统 receipt/reconciliation 和非文件补偿 verifier 不普及；direct route 仍有人工修复边界 | **强，P1 深化** |
| 工具契约 | `ToolDefinition` 已声明 schema、risk、version、idempotency、compensation、retry；MCP 输出 schema 会复验 | 内建 runtime 未统一按 JSON Schema 强制输入/输出验证；部分 `output_schema={}` | **部分达标，P1** |
| Agent 评测 | 105 个版本化案例、39 个对抗案例；确定性回放 105/105、39/39；缺证据会 fail closed | 当前候选没有通过真实 provider 的 130 项报告；缺真实恶意 MCP/网页/文档/跨 Agent、故障注入和 30+ 人工结果质量证据 | **框架强，发布未闭环 P0** |
| Electron / IPC | `contextIsolation:true`、`sandbox:true`、`nodeIntegration:false`、自定义协议、sender 校验、导航/窗口限制、fuses | CSP 对任意 localhost 端口较宽；需持续证明所有 IPC handler 都走统一 sender/main-frame 校验 | **强，P1** |
| 本地 API / 后端进程 | loopback、随机 token、HMAC challenge、禁止重定向、启动恢复 | loopback token 不能证明同用户进程身份；退出使用强杀路径，graceful drain、退避重启/熔断不完整 | **中上，P1** |
| SQLite 耐久性 | 事务、外键、busy timeout、WAL、隐私擦除 checkpoint/VACUUM | 候选 SQLite 修复版本未门禁；未找到 Online Backup/`VACUUM INTO`、启动 `quick_check`、周期 `integrity_check` 或恢复演练 | **候选版本 P0；备份恢复 P1** |
| Active 数据隐私 | 录制和部分高敏感 payload 使用 DPAPI-wrapped AES-GCM；retention/erase 较完整 | task/chat/memory/plan/tool/approval 正文仍以 SQLite JSON/TEXT 明文保存；safeStorage/DPAPI 不能防同一登录用户空间中的恶意程序 | **部分达标，P1** |
| 移动配对与批准 | HTTPS/WSS、短时配对、独立 claim secret、refresh family、reuse 检测、TLS pin、biometric 保护 | 仍缺真机候选证据、硬件设备密钥请求级 PoP、协议/最低安全版本协商、持久且有界的失败限流 | **代码中上，发布未闭环 P0/P1** |
| MCP | lifecycle/schema/progress/cancel/SSE 恢复和第三方 R4/default-deny 已实现 | 固定 `2025-11-25`，未支持 2026-07-28 stateless core、MRTR、headers/cache/auth 变化 | **Preview 可接受；生产互操作 P1/功能 P0** |
| 可观测性与 IR | trace ID、tool span、Prometheus、HMAC audit chain、默认脱敏 | 无完整 OTLP exporter、跨组件 parentage、可靠性 SLO、告警和演练；所谓 external anchor 仍在本机 data directory | **部分达标，P1** |
| 供应链与发布 | actions 固定 SHA、依赖锁、SBOM、CodeQL、gitleaks、Authenticode/attestation 验证路径 | 真实 RC 在线验证与 owner 签收缺失；Windows signing 仍依赖长期 client secret；CODEOWNERS 只有一人；未声明可复现/L3 | **代码强，候选未闭环 P0/P1** |
| 可访问性与人类控制 | 原生确认、可取消任务、axe、焦点管理和 AccessibleDialog 已存在 | 缺真实 Electron + Narrator、键盘-only、高对比度、200–400% 缩放和审批理解率/误批率证据 | **部分达标，P2；公开发行前人工验收** |

## 5. 关键差距与验收标准

### F-01 — 候选 SQLite 版本与 WAL 数据完整性

**优先级：P0（公开 Beta/RC 候选门禁）**

事实：

- `backend/app/core/db.py:154-197` 使用线程本地多连接并明确启用 WAL。
- 本轮从 `.venv/Scripts/python.exe` 读取到 `sqlite3.sqlite_version == 3.49.1`。
- `.github/workflows/release-candidate.yml:36-40` 固定 Python `3.12`，没有对打包后 SQLite 库版本做断言。
- SQLite 官方称该竞争条件发生概率很低，不是紧急事件，但后果可能是数据库损坏，并建议应用升级到修复版本。[SQLite WAL-reset bug](https://www.sqlite.org/wal.html#the_wal_reset_bug)

推断：本地开发运行时处于受影响范围，但**不能据此断言候选 `backend.exe` 受影响**。发布流程缺的是二进制级证明。

验收标准：

1. 从最终 `backend.exe`/安装包内运行时输出并签入 evidence：Python 版本、`sqlite3.sqlite_version`、compile options 和 artifact digest。
2. 候选只接受官方已修复的 SQLite lineage；最简单的政策是要求 `>=3.51.3`，不依赖旧分支例外。
3. 增加多连接 writer/checkpoint 并发压力与崩溃恢复测试，结束后执行 `PRAGMA quick_check`。
4. 迁移前使用 Online Backup API 或 `VACUUM INTO` 创建受保护快照；定期做 `integrity_check` 和真实恢复演练。[SQLite Backup API](https://www.sqlite.org/backup.html)；[SQLite integrity_check](https://www.sqlite.org/pragma.html#pragma_integrity_check)

### F-02 — 当前候选发布证据未闭环

**优先级：P0**

`docs/release/release-readiness-dashboard.md:13-32` 仍指向旧候选 `307c968e...`，最后审阅于 2026-07-09，且 RR-P0-001 至 RR-P0-007 全部 `in_progress`：

1. 干净 Windows 安装、启动、首个只读任务、诊断导出、卸载/回滚；
2. 本地模型 clean-machine 路径；
3. Android 真机/模拟器 LAN-WSS、证书、远控 grant/revoke；
4. 30+ 真实自然语言任务的人工结果质量；
5. 真实诊断包外部分享前人工复核；
6. public Beta/RC handoff 与 release-owner 签收；
7. 与候选绑定的 Agent threat model / OWASP control map 复核。

此外，`docs/qa/agentic-product-evals.md:40-45` 明确说明 105/105 确定性回放不能替代真实 provider；旧失败报告继续是 stop-ship。`docs/compliance/agentic-threat-model.md` 仍为 `TM-2026-07-11-v1`，而代码在 7 月 31 日和 8 月 24 日有大量安全变更，因此至少需要重新审阅并绑定新候选。

验收标准：只有 7 个 dashboard row 都为 `passed`，并包含同一 commit/build/artifact digest、证据路径、reviewer 与时间，才能改变 No-Go。

### F-03 — Windows 原生执行隔离尚未实现

**优先级：条件性 P0**

`docs/architecture/windows-execution-isolation-host.md:3-53` 准确声明：仓库有 fail-closed adapter/verifier，但没有真正的 native broker。当前策略应保持不变：公开 Beta/RC/GA 禁用任意本地 Skill、生成代码写入和通用 Shell/Python，直到候选证据能证明 AppContainer/受限令牌、非 breakaway Job、文件 broker、默认拒绝网络、凭据/句柄隔离和逃逸测试。

如果产品路线不开放这些能力，该项不是 Development Preview 的立即阻断；如果准备开放，它直接成为功能发布 P0。

### F-04 — MCP 2026-07-28 迁移

**优先级：P1；若对外宣称生产 MCP，则为该功能 P0**

当前证据：

- `backend/app/mcp/client.py:53`：`MCP_PROTOCOL_VERSION = "2025-11-25"`。
- 根 `package.json:16-19`：conformance 固定 `@modelcontextprotocol/conformance@0.1.16` 与 `--spec-version 2025-11-25`。
- README 已谨慎标注 MCP Preview/R4/default-deny/未声明完整 OAuth，这一产品表述是准确的。

新规范变化包括：取消 `initialize/initialized` 和 `Mcp-Session-Id`；每个请求自描述；可选 `server/discover`；使用 `Mcp-Method`/`Mcp-Name`；MRTR 替代旧 server-initiated streams；list cache metadata；验证 OAuth `iss`、issuer-bound credentials，并从 DCR 转向 CIMD；Tasks 移入 extension。[MCP 发布说明](https://blog.modelcontextprotocol.io/posts/2026-07-28/)；[MCP SDK 迁移指南](https://ts.sdk.modelcontextprotocol.io/v2/migration/support-2026-07-28)

验收标准：

1. 设计 dual-era client，明确 server capability/version negotiation 和 2025 fallback 截止日期。
2. 新路径无 session 假设，支持 MRTR、headers、cache hints 和 Tasks extension；保留 cancel/timeout/size/schema guards。
3. OAuth 增加 `iss` 验证、凭据 issuer 绑定、desktop/CLI application type，并制定 CIMD 迁移。
4. 官方 conformance 与 hostile server 测试覆盖两个 era；文档只声明实际通过的范围。

### F-05 — Agent 可靠性：强骨架，缺候选级真实结果

**优先级：真实评测 P0；工具合同与重放 P1**

当前强项：planning/execution 分离、HMAC 确定性合同、稳定 execution identity、journal/CAS、`outcome_unknown`、启动恢复、审批绑定、摘要 lineage 和安全重试白名单。它们符合“把模型决策限制到必要位置、每步使用真实反馈”的工程方向。[Anthropic Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents)

主要差距：

- 当前候选缺真实 provider 130 项、多个 trial、真实环境状态 grader 和人工校准。
- 内建工具未统一按 Draft 2020-12 强制 input/output schema；部分输出 schema 为空。
- 外部副作用没有普遍声明 provider idempotency key、receipt/reconciliation probe 或资源 CAS。
- checkpoint 没有完整复现信封：candidate commit、workflow/schema version、实际响应模型修订、provider request id、tool schema hash 等仍不完整。
- 长上下文摘要忠实度、权限信息保持、R3 审批理解率和误批准率没有专项证据。

建议把这些变成发布可计算指标，而不是文档承诺：tool contract coverage、R2/R3 reconciliation coverage、outcome_unknown 率、恢复成功率、审批延迟/放弃率、真实结果成功率和对抗逐案通过率。

### F-06 — 数据机密性、完整性和恢复

**优先级：P1**

`backend/app/security/sensitive_data_crypto.py` 已为录制/截图等高敏感 payload 提供 DPAPI-wrapped AES-GCM，这是正确基础。但 `backend/app/core/db_upserts.py:13-153,215-245` 和 `backend/app/core/db_memory.py:52-142` 仍将 task/chat/plan/run/tool/approval/memory 正文以 JSON/TEXT 保存。HMAC 提供篡改检测，不提供机密性。

Electron 官方也说明 Windows `safeStorage` 使用 DPAPI，主要防止其他用户读取，不保证阻止同一用户空间中的其他应用。[Electron safeStorage](https://www.electronjs.org/docs/latest/api/safe-storage)

建议：

1. 先做数据分类：secret/high-sensitive、用户正文、检索索引、审计元数据，按列/记录采用 envelope encryption。
2. 搜索需要的内容保存最小派生索引，权威原文加密；密钥版本、轮换、备份恢复和删除纳入同一合同。
3. 为活跃数据库增加 Online Backup、quick/integrity check、恢复演练和损坏隔离流程。
4. 审计 chain head 若要提供企业不可抵赖性，应发送到独立 append-only/WORM 或远程签名时间戳边界；本机同目录 anchor 只能称 tamper-evident。

### F-07 — Electron、本地服务与 Windows 运维

**优先级：P1；可访问性部分 P2**

强项证据位于 `desktop/src/main/main.ts:111-203`、`desktop/src/main/rendererTrust.ts:5-30`、`desktop/src/main/rendererProtocol.ts:78-151` 和 preload allowlist：sandbox、context isolation、Node 隔离、默认拒绝权限、导航/窗口限制、自定义协议和 IPC sender 校验基本符合 Electron 官方清单。

需要改进：

- `desktop/src/main/rendererProtocol.ts:10-22` 的 `connect-src`/`img-src` 允许任意 localhost 端口；应收窄到已认证 backend origin，特权请求尽量全部经 main IPC。
- `desktop/src/main/backendProcess.ts:543-580` 的 Windows 退出最终走 `taskkill /T /F`，异常退出只记 error；增加 authenticated graceful shutdown、超时强杀、Job Object、指数退避、熔断和安全模式。
- `backend/app/security/lan_tls.py:239-284` 的私钥以 PEM 写盘后只 `chmod(0600)`；Windows 必须创建并验证受保护 DACL，过宽时拒绝启用移动配对。[Windows File ACL](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights)
- 应用内写入/回滚已有共享路径锁，但外部进程仍可能在状态比较与路径操作之间抢写；高价值写路径应逐步改为 native handle-bound 操作，避免名称重解析与跨进程 TOCTOU（现有残余风险记录：`CODE_REVIEW_REPORT.md:384-387`）。
- Windows signing 脚本依赖长期 `AZURE_CLIENT_SECRET`；应迁移到 GitHub OIDC/workload identity federation，并验证 Production Environment reviewers 与最小 signer role。
- 主进程同步 PowerShell/文件 I/O 应移到异步或 utility process，并建立 cold start、backend-ready、IPC P95、renderer long task、RSS 和 WAL 峰值预算。[Electron Performance](https://www.electronjs.org/docs/latest/tutorial/performance)

### F-08 — 移动伴侣

**优先级：真机候选 P0；协议与设备身份 P1**

当前设计已包含短期配对码、独立 claim secret、HTTPS/WSS、JWT issuer/audience/scope/jti、refresh rotation/reuse detection、active/next TLS pin 和生物保护存储，作为 Preview 是扎实的。

仍需：

1. 与候选 APK 绑定的真机 LAN/WSS、证书、锁屏/恢复、grant/revoke、远控和日志脱敏证据。
2. backend/mobile protocol version、capability negotiation 和 minimum-supported-build；服务端能拒绝存在已知安全缺陷的旧远控客户端。
3. 高影响批准使用 Android Keystore 硬件密钥对 challenge + action digest + nonce 做请求级 PoP，并绑定用户存在/生物验证。[Android Keystore](https://developer.android.com/privacy-and-security/keystore)；[Android Biometric](https://developer.android.com/identity/sign-in/biometric-auth)
4. 将进程内、重启即清空的配对失败限流改为有界 TTL 状态、全局预算和可审计策略。
5. 对移动审批 WebSocket 事件做有限字段的运行时 schema 解析，不以 TypeScript 类型断言代替不可信网络输入验证；保留 polling fallback（现有残余风险记录：`CODE_REVIEW_REPORT.md:384-392`）。
6. Expo SDK 56 / RN 0.85.3 的已知 Hermes 内存回归按独立升级项目迁移至 SDK 57 / RN 0.86.2+，完成真机内存与后台恢复回归（现有证据：`CODE_REVIEW_REPORT.md:389`）。

### F-09 — 可观测性、SLO 与 Incident Response

**优先级：P1**

当前 trace、metrics 和 audit 适合开发诊断，但尚不是跨 Electron/FastAPI/provider/tool/mobile 的生产追踪体系。建议：

- 建立 `plan → model → tool → approval → checkpoint → recovery` parentage；区分逻辑调用与 provider retry。
- 默认只导出 model/provider/operation、token、latency、risk、result class、error code、outcome_unknown 和 recovery result；禁止默认发送 prompt、原始 tool args/results、文件路径、截图和凭据。
- 定义 SLO：任务最终成功率、R2/R3 未知结果率、恢复成功率、审批等待 P95、取消完成 P95、provider/tool 错误率、每任务成本和 WAL/内存水位。
- 对 audit chain failure、approval anomaly、token reuse、MCP capability drift、update/signing failure、DB integrity failure 做演练和告警。

### F-10 — 供应链、治理和可访问性

**优先级：候选签收 P0；治理 P1；无障碍 P2/发行前人工验收**

- `.github/CODEOWNERS` 目前所有路径只由 `@suli9710` 所有；无法形成真正的两人发布复核。至少对 release workflow、signing、policy、tool runtime、mobile auth 和 update chain 配置独立 reviewer。
- 现有 SBOM、SHA-256、SLSA/GitHub attestation 路径较强，但不要在真实在线 RC 验证之前声明等级完成；后续可推进可复现构建和 SLSA L3 隔离。
- 自动 axe 不能替代真实 Windows 辅助技术。公开发行前应覆盖 Narrator、键盘-only、高对比度、forced colors、200–400% 缩放、焦点恢复和风险确认的可理解性。[Microsoft Accessibility Testing](https://learn.microsoft.com/en-us/windows/apps/design/accessibility/accessibility-testing)

## 6. 优先实施路线图

### 0–14 天：公开 Beta 的 P0 闭环

1. **新增打包后二进制 SQLite 版本门禁**，升级到明确修复版本，并运行 WAL 并发/恢复/quick_check evidence。
2. 从当前目标 commit 切出干净、不可变候选；刷新 dashboard、threat model 和 control map，所有证据绑定同一 commit/build/digest。
3. 使用非 mock provider 执行完整 25 golden + 105 benchmark；报告绑定模型、prompt、policy、tool catalog 和 candidate。对非确定性关键 case 运行多个 trial。
4. 完成 30+ 真实自然语言任务人工结果质量、诊断包实际内容复核和 release-owner 签收。
5. 在干净 Windows profile 完成安装、启动、首个任务、更新/回滚、卸载；验证 Authenticode、SBOM 和 provenance 在线消费路径。
6. 用候选 APK 完成 Android 真机 LAN/WSS、证书、锁屏/恢复、审批、grant/revoke、远控和脱敏附件复核。
7. 继续禁用任意代码、通用 Shell/Python 和第三方 Skill 写执行；不要用 adapter attestation 代替 native broker。

### 15–45 天：P1 生产硬化

1. 支持 MCP 2026-07-28 dual era、MRTR、headers/cache/auth，并更新 conformance 与 hostile server tests。
2. 为所有工具统一执行 input/output JSON Schema；副作用工具禁止空输出合同；生成 contract coverage gate。
3. 为 R2/R3 外部效果补 provider idempotency/CAS/receipt/reconciliation；否则强制 manual repair。
4. 为 active task/chat/memory/plan/tool/approval 正文设计 envelope encryption 和密钥轮换；增加 SQLite backup/integrity/restore。
5. 收窄 renderer CSP；增加 graceful backend shutdown、Job Object、restart backoff/circuit breaker；LAN TLS key 使用受保护 DACL。
6. 建立 OTLP opt-in exporter、可靠性/安全 SLO 和 incident drills；内容字段默认不外发。
7. 移动端增加协议版本、最低安全 build、硬件密钥 PoP 和持久有界限流。
8. 签名身份迁移到 OIDC；关键路径配置第二 reviewer。

### 46–90 天：能力扩展与 GA 准备

1. 若产品需要任意执行，建设真正的 Windows native isolation broker，并完成 symlink/junction/UNC/device path/8.3、DNS rebinding、child egress、credential/handle leak、crash/cancel/restart 逃逸测试。
2. 将 audit chain head 外置到独立 append-only/WORM 或签名时间戳边界。
3. 增加 checkpoint N/N-1 重放、完整复现信封、真实生产分布 shadow eval 和模型路由质量门。
4. 完成 Expo/RN 跨代升级与真机内存/后台回归。
5. 建立 Narrator、高对比度、缩放、键盘和审批理解度人工验收；补第三方渗透与隐私评估。
6. 评估可复现构建与 SLSA L3，明确 builder/signing trust assumptions。

## 7. 发布 Gate 建议

| Gate | 最低通过条件 | 失败动作 |
| --- | --- | --- |
| SQLite candidate | 最终 backend 运行时为官方修复版本；WAL stress + quick_check 通过 | 阻断候选 |
| Real Agent quality | 130 项候选绑定真实 provider 报告达到门槛；所有关键对抗 case 单独通过 | 阻断候选 |
| Human outcome | 30+ 真实任务人工复核，记录 outcome、artifact、rewrite 与 safety FP/FN | 阻断候选 |
| Windows install | 干净机器 install/start/task/update-or-rollback/uninstall 证据完整 | 阻断候选 |
| Android | 候选 APK 真机 LAN/WSS/证书/审批/远控/revoke 通过 | 阻断移动功能或候选 |
| Threat model | 当前 commit 的边界、工具、MCP、mobile、update 和 open risks 有 owner 接受 | 阻断候选 |
| Supply chain | Authenticode、artifact digest、SBOM 与 provenance 在线验证且 reviewer 签收 | 阻断发布 |
| Arbitrary execution | Native broker 与候选 attack evidence 全部通过 | 未通过则保持能力禁用 |
| MCP production | 2026-07-28 conformance/auth/hostile-server 证据通过 | 保持 Preview 标签 |
| Privacy/diagnostics | 实际诊断包人工检查；active 数据加密方案和 retention/erase 有 evidence | 阻断外部共享；GA 前闭环 |

## 8. 关键来源

以下均为本轮使用的官方标准、平台文档或一手工程资料；访问日期为 2026-08-24。

### Agent 安全与治理

1. [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
2. [OWASP Securing Agentic Applications Guide 1.0](https://genai.owasp.org/resource/securing-agentic-applications-guide-1-0/)
3. [NIST AI RMF Generative AI Profile, AI 600-1](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)
4. [NIST/NCCoE Software and AI Agent Identity and Authorization](https://www.nccoe.nist.gov/sites/default/files/2026-02/accelerating-the-adoption-of-software-and-ai-agent-identity-and-authorization-concept-paper.pdf)
5. [OpenAI: Keeping your data safe when an AI agent clicks a link](https://openai.com/index/ai-agent-link-safety/)

### 评测与可靠性

6. [NIST TEVV-Athlon Framework](https://www.nist.gov/artificial-intelligence/ai-research/tevv-athlon-framework-evaluating-ai-systems)
7. [NIST Automated Benchmark Practices, AI 800-2 IPD](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.800-2.ipd.pdf)
8. [Anthropic: Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
9. [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
10. [Anthropic: Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)

### MCP

11. [MCP 2026-07-28 release](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
12. [MCP TypeScript SDK migration: 2026-07-28](https://ts.sdk.modelcontextprotocol.io/v2/migration/support-2026-07-28)
13. [MCP current draft: Tools](https://modelcontextprotocol.io/specification/draft/server/tools)

### Desktop、Windows 与移动

14. [Electron Security Checklist](https://www.electronjs.org/docs/latest/tutorial/security)
15. [Electron safeStorage](https://www.electronjs.org/docs/latest/api/safe-storage)
16. [Electron Performance](https://www.electronjs.org/docs/latest/tutorial/performance)
17. [Electron Updates](https://www.electronjs.org/docs/latest/tutorial/updates)
18. [Microsoft AppContainer Isolation](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation)
19. [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
20. [Microsoft File Security and Access Rights](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights)
21. [Microsoft Accessibility Testing for Windows apps](https://learn.microsoft.com/en-us/windows/apps/design/accessibility/accessibility-testing)
22. [Android Keystore](https://developer.android.com/privacy-and-security/keystore)
23. [Android Network Security Configuration](https://developer.android.com/privacy-and-security/security-config)
24. [Android Biometric Authentication](https://developer.android.com/identity/sign-in/biometric-auth)

### 数据、可观测性与供应链

25. [SQLite WAL and WAL-reset bug](https://www.sqlite.org/wal.html)
26. [SQLite Online Backup API](https://www.sqlite.org/backup.html)
27. [SQLite PRAGMA integrity_check](https://www.sqlite.org/pragma.html#pragma_integrity_check)
28. [OpenTelemetry GenAI Semantic Attributes](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)
29. [SLSA 1.2 Build Requirements](https://slsa.dev/spec/v1.2/build-requirements)
30. [GitHub Artifact Attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)

## 9. 最终建议

不要把下一阶段定义为“再做一轮全面加固”。更有效的目标是：

> **先形成一个由真实候选、真实模型、真实机器、真实设备和真实 reviewer 共同证明的公开 Beta；同时关闭 SQLite 候选版本风险，并保持危险任意执行默认禁用。随后再以 MCP 2026-07-28、active 数据加密、统一工具合同、外部对账和 OTLP/SLO 为 P1 主线。**

这一路线最能降低“代码看起来很安全，但发布证据和真实结果仍不可证明”的剩余风险。
