# Lengrvis 行业最佳实践调查

**首次调查日期**：2026-07-11

**本轮增量复核**：2026-07-16

**阻断修复复核**：2026-07-17

**本轮建议续修复核**：2026-07-19—20

**调查范围**：Windows-first 本地 OS Agent、FastAPI 后端、Electron 桌面端、Android Companion、智能体安全、隐私、供应链、测试与发布。

**首次仓库快照**：`307c968e421131fa7ce62afdadef404ff02e94a6`，基于当时 dirty working tree；结论反映本机工作区，不等同于该 commit 的可复现状态。

**本轮复核快照**：`2d4f3c8b596ae00ab81407d9982a7e78cc949341`，`main` 分支，工作区仍为 dirty tree。本轮结论同样不是可复现候选签收。

**结论口径**：工程最佳实践调查，不是渗透测试、法律意见、认证结论或发布签收。

**阅读顺序**：先看下方“2026-07-19—20 审计建议续修复核”，再看“2026-07-17 阻断修复复核”、“2026-07-16 增量复核”、“2026-07-13 增量复核”以及第 0、3、10-11 节。第 1-2、4-9 节保留首次调查基线，其中部分文字已被后续实现取代；若有冲突，以日期较新的增量复核为准。

## 2026-07-19—20 审计建议续修复核

本节继续落实审计中的 P1/P2 建议，并取代下方较早章节中与本轮修改冲突的实现状态；它基于 dirty working tree 的本地复核，不构成 clean candidate、回滚签收或公开发布结论。

| 审计建议 | 2026-07-19—20 状态 | 本轮证据与仍保留的边界 |
| --- | --- | --- |
| P1-12 字段级 provenance lineage | Document 与会话摘要代码闭环，数据库级降级边界保留 | RFC 6901 `field_lineage` 继续受 envelope HMAC 保护；一次性私有 provenance 已接入 `extract_text`、`summarize`、`qa`、`convert_to_markdown`、`analyze_xlsx`、`generate_report`、`extract_tables`、`ask_with_citations`、`generate_cited_report` 九个真实 Document 调用点，不污染公开 ToolResult。持久会话摘要现在以根级 `summarize/merge` 映射绑定摘要文本、session、最新 message anchor、canonical source message IDs 与实际摘要输入；legacy 摘要只迁移成带 taint 的认证根，不伪造无法恢复的历史映射。顶层 envelope、旧版兼容 sidecar 和迁移版本相互校验，摘要/anchor/ID/envelope 篡改 fail closed；Completion 合并既有摘要与父 lineage，所有 SessionContext 整行写通过事务 CAS/reload-merge，随后 manual compact 会原子替换 canonical IDs。私有 sidecar 从 provider、snapshot、telemetry 和公开 metadata 递归移除。本轮又把 lineage 模型、V1 sidecar、canonical hash、JSON Pointer 与父来源验证集中到叶级 `content_lineage.py`；`schemas` 与 `content_provenance` 保留旧 class/常量导入身份，固定 secret/time/marker/HMAC 的 hard-coded golden wire 防止 reader/writer 同步漂移。相关 lineage/summary/Document/ToolRuntime 宽回归 `205 passed`，当前完整 backend `3835 passed, 12 skipped`。确定性转换仍可保守使用 root lineage；若攻击者同时删除 envelope、sidecar 与版本标记，仍无法与真实旧库区分，完整 hostile-SQLite 威胁需外部单调迁移门。legacy 根也不能恢复历史正文，HMAC 密钥丢失时会 fail closed。 |
| P1-13 approval session freshness | Desktop/Mobile 未 claim 授权新鲜度闭环，非语义与在途边界仍开放 | Desktop 在托盘/长后台及隐藏登录启动的首个同步边界撤销 canonical generation 和 native signing，回前台完成 runtime transition 后才生成新代际；串行可见性协调器确保最后意图获胜，后台电源事件不会复活签名。新的固定操作 control transport 对 runtime status、前后台切换和急停均先做新鲜 `/health` HMAC challenge、精确 loopback origin 复核并拒绝重定向，再允许发送 desktop token；单调 epoch 会让乱序旧响应降级为失败，发送前及 runtime 返回后都复核同一租约，旧请求的迟到失败不会撤销较新的有效证明。每次兼容令牌访问都会重核 base URL，观察到 origin 变化即撤销，因此 A→B→A 访问序列不会复活旧证明；challenge 或当前代状态变更请求失败也会撤销。兼容 getter 的证明使用单调时钟 70 秒有界租约，通用 IPC HTTP 令牌请求也拒绝重定向，非 loopback 只可做无凭据健康探测。旧式裸 token getter 与通用 WebSocket 消费者仍是兼容 seam；HMAC challenge 不能抵御租约窗口内的恶意本机透明 relay/同 origin 端口接管，完整关闭需命名管道、mTLS 或一次性 broker ticket 等认证通道。语义 UIAutomation 的 V2 私有资源态同时绑定目标元素、selector、顶层窗口 runtime id/handle/process、进程可执行文件、PID+创建时间的进程实例及完整同进程 accessibility 父链；在私有 `_resource_state` 中，目标 `name`、`automation_id`、`class_name`、selector、窗口/父链账号与工作区标签均只进入共享 approval-secret HMAC，不再原样持久化；用户可见审批参数仍由既有敏感参数与预览脱敏策略承担。click/type 工具版本升到 3，旧或畸形身份资源态视为“存在但不可验证”并 fail closed；无法读取可执行文件、检测到 PID reuse、父链中断，或在跨进程边界前不能证明顶层窗口时不创建审批，claim 前与 Invoke/SetValue 前均重新比较。身份与 approval 扩大回归 `195 passed`，固定 V2 digest 防止字段/prefix 漂移。Mobile access JWT、refresh 轮换、审批 `auth_context` 与最终原子 claim 共同绑定 `family_generation`；离开 active 清空内存 session/远控 grant，回前台须重新读取生物保护 SecureStore 并强制 refresh，迟到异步回调受锁代际/CAS 阻断。公开载荷不泄露私有绑定。accessibility 完全不可见的应用内部账号、coordinate/hotkey fallback、物理锁屏动态证据及系统调用已开始后的非原子竞态仍需 connector/stop/quiescence/broker 边界承担。 |
| Android LAN TLS 与 reviewed evidence | TLS、附件清单与签名指纹契约代码闭环，物理设备与 PoP 证据仍开放 | exact-origin TLS、证书有效期、IDN/IPv6/端口 pin 规范化保持 fail closed。严格 reviewed evidence 现在必须使用 `reviewed-evidence-ed25519/v3`，将完整公钥 SHA-256 指纹纳入规范签名载荷，并仅用发布侧公钥完成验证；`sha256-manifest/v1` 必须以非路径脱敏标签、类型、SHA-256 和正字节数绑定截图、视频、后端/移动日志及 `adb` 安装状态，标签须与 `evidence_artifacts_redacted` 精确一致。Android gate 与发布证据聚合继续携带并强制 `artifact_manifest_valid`、`signing_key_fingerprint_bound`。发布/证据相关回归 `243 passed`，补强后的契约回归 `96 passed`。Android Keystore/DPoP、受控 signer 的真实候选 APK、物理真机及其审阅附件仍未完成，不能以模板或本地测试冒充。 |
| 意图建议的信任表达 | UI/边界闭环，真实校准仍开放 | ChatPanel 不再把启发式置信度显示成精确百分比，改为“高/中/低建议强度 + 匹配原因”，并为内置建议标题、提示与原因补齐中文；预测 schema 将 confidence 约束为 `[0,1]`，单个畸形模型候选不会丢弃同批有效候选。真实接受率、编辑率和任务成功数据完成校准前，仍不得把该强度解释为概率。 |
| P2-3 BrowserHost 下载与日志 | 下载拒绝及事件脱敏闭环 | `will-download` 继续 `preventDefault()` + `cancel()`；被阻止下载的事件 URL 现在移除 userinfo，并对全部 query value 与 fragment 统一脱敏，不依赖容易漏项的参数黑名单；取消后 `getURL()` 已失效时记录空 URL，不回退到会话地址。若未来引入显式下载 broker，仍需来源/文件/类型/大小/落盘路径、恶意文件扫描、路径 policy 与 point-of-risk approval。 |
| P2-1 打包 renderer 自定义协议 | 代码闭环，本机未签名 ASAR 动态通过；签名候选仍待验 | 打包态改为 `app://local/index.html`，`file://` 不再具有导航或 IPC sender 信任。ready 前只注册 `standard/secure/supportFetchAPI`，未开启 `bypassCSP`、Service Worker、CORS 跨协议读取、stream/code cache 或 extension 权限；ready 后的 handler 仅接受精确 authority 与 GET/HEAD，通过 raw URL、Windows 保留设备名、规范路径、realpath、普通文件和 MIME allowlist 阻断编码遍历、ADS、junction/symlink 越界、目录、未知类型及次级 HTML，并下发 CSP、CORP、COOP 与 `nosniff`。本机 Electron 42.3.3 的未打包 smoke 与未签名 `win-unpacked/resources/app.asar` 均加载入口、preload bridge、JS/CSS，验证严格响应头、编码遍历 404、query/fragment 及 `app://local -> loopback /api/health` CORS；聚焦 `33 passed`、最新完整 Desktop `93 files / 416 tests passed`、typecheck、production build 与 IPC smoke 均通过。真实受保护 workflow 生成的签名候选、跨来源读取动态负测及 WSS/更新后启动仍须候选签收。 |
| UIAutomation 局部可观测性 | 公开工具尝试、截图捕获失败与审批 gate 计数闭环；标准导出仍开放 | `ui_automation_observability.py` 集中管理闭集 action/outcome、direct/vision-fallback capture failure 与 `route_review` / `route_claim` / `tool_guard` / `target_gate`。17 个公开工具每次只产生一个 action 终态，六个审批动作区分 preview/live；provider unavailable、唯一目标未找到、遍历截断、timeout、abort、畸形截图与复合 vision 结果均有结构化分类。仅 JSON 布尔 `false` 可进入 live 分支；未知 action 折叠为 `other`，理论硬上限为 306 个 series，标签不含 selector、文本、路径、窗口、进程、错误正文或截图。指标写入/恢复日志同时失败也不会改变返回或覆盖取消异常。直接契约 `48 passed`，UIAutomation/approval/automation-guard 扩大回归 `240 passed`，完整 backend `3835 passed, 12 skipped`。这些仍只是进程内 operational counters，不证明 `result_verified`，也不代表跨功能 approval telemetry、业务 parent trace、OTLP 或 SLO/告警已完成。 |
| Context compaction 决策可观测性 | projection、manual 与 provider-limit 变换决策计数闭环；retry/持久化终态与标准导出仍开放 | 新的 feature-owned `compaction_observability.py` 只生成 `context_compaction_decisions_total{trigger,strategy,outcome}`：`trigger`、`strategy`、`outcome` 均为固定闭集，笛卡尔积硬上限 180 series。task/session/message/boundary/source/provider/model、Prompt/summary/error/path 正文和 token 数均不能成为标签；未知值只折叠为 `other` / `invalid_result`。真实 projection 每次只有一个最终 decision，session-summary-only 不冒充压缩；分类使用 micro metadata、history 前后 token 与 auto 的结构/缩减事实，能够区分未需要、已应用、结构改变但无收益、错误和畸形结果。`record_projection_event=False` 的 usage/诊断 projection 在成功与异常路径都保持静默。manual 只在所有 task/session/adhoc 最终恰好经过一次的 transformation seam 计数，不把后续 CAS/AgentBus 持久化当成成功；reactive summary 与 fallback trim 分开计数，fallback 还复核 target 是否达到，但不把 transform `applied` 解释成 provider retry 成功。counter 与恢复日志同时失败不会改变结果或覆盖原异常。直接与集成契约 `41 passed`，context/commands/provider 相关宽回归 `220 passed, 4 skipped`，完整 backend `3835 passed, 12 skipped`。仍开放跨功能 approval outcome、provider invocation 终态、业务 parent trace、OTLP、SLO/告警与 `result_verified`。 |
| RR-P1-001 maintainability gate | 代码门禁恢复，通过但 legacy hotspots 仍需持续下降 | 本轮没有提高阈值或扩大 allowlist；`content_lineage.py`、`ui_automation_identity.py`、`backendControlTransport.ts`、`ui_automation_observability.py` 与 `compaction_observability.py` 都以窄 Interface 集中兼容、身份、transport 或指标分类规则。Context 指标 taxonomy、隐私、series 上限与 non-interference 没有堆回已达 1395 行的 `management.py`；`ui_automation.py` 仍为 1396 行，`ui_automation_tools.py` 为 855 行。最新同阈值门禁通过：1016 个源文件、243509 行、P95 `785 <= 800`、backend 最大 `1396 <= 1400`、desktop 最大 `898 <= 900`，且没有新增未豁免 900 行文件。通过只证明防增长阈值恢复，不代表 1300+ 行 legacy owners 已健康；后续仍应按 owner seam 和直接测试继续下降。 |

本轮续修验证包括：最终完整 backend `3835 passed, 12 skipped`，且异常边界审查门禁（814 个源文件、357 个已标注边界）与同阈值维护性门禁在同一轮通过；本次 deep-module 提取后的 lineage、summary、Document、ToolRuntime 及直接调用方宽回归 `205 passed`，历史 UIAutomation identity/approval 回归 `195 passed`，新增 UIAutomation observability 直接契约 `48 passed`、UIAutomation/approval/automation-guard 扩大回归 `240 passed`；新增 context compaction observability 直接与集成契约 `41 passed`，context/commands/provider 相关宽回归 `220 passed, 4 skipped`；后端 lineage、Document、ToolRuntime、移动 token/approval、UIAutomation、Desktop generation 与 Android evidence 原交叉回归 `318 passed`；会话摘要 provenance 专项 `23 passed`、summary/session/compaction/commands 与相关宽回归 `194 passed`；Android 发布/证据宽回归 `243 passed`；Desktop 完整 Vitest `93 files / 416 tests passed`，control transport 直接测试 `23 passed`、IPC HTTP redirect 直接测试 `1 passed`，typecheck、Electron release build 与 IPC/backend-env smoke 通过，先前 production renderer build 及 Electron 42.3.3 未打包/本机未签名 ASAR 协议动态 smoke 继续有效；Mobile typecheck、session lifecycle 与 token smoke 通过；相关后端 Ruff 通过。此前 LAN TLS source smoke 与 Kotlin main/androidTest 编译结果继续有效。Android 物理设备 instrumentation、本机损坏生成缓存清理后的完整 assemble、clean candidate、真实回滚演练、签名 Desktop 候选及真实发布 workflow 不在上述通过口径内。

公开 RC/GA 的外部 stop-ship 结论不变：仍需真正建立并验证 Windows OS 隔离宿主/文件与网络 broker，用当前候选取得达到阈值的真实 provider 130 项报告，并由受保护 RC workflow 生成同一候选绑定的签名与 release-owner 证据。active 正文加密、MCP OAuth/扩展 conformance、OTLP/SLO 和 provider reconciliation 也仍是后续深化项。

## 2026-07-17 阻断修复复核

本节取代下方 2026-07-16 静态差距矩阵中已经过时的实现状态，但不构成 clean candidate 或发布签收。

| 原阻断项 | 2026-07-17 状态 | 当前证据与剩余边界 |
| --- | --- | --- |
| Responses/CUA provider 存储默认开启 | 代码闭环 | `disable_response_storage` 默认开启，wire payload 默认发送 `store:false`；只有显式配置 `false` 才 opt in provider 存储。provider/Responses/CUA 聚焦回归 `114 passed, 4 skipped`。 |
| 路径 policy/risk 在 canonicalization 前判断 | 代码闭环 | policy 与 dynamic risk 先生成 canonical path，折叠 `..`，统一 UNC/分隔符/大小写并对歧义路径 fail closed；多路径必须全部被授权，且不会把非路径字符串列表误判成路径。 |
| Browser/CUA 任务边界、人工接管与全局急停 | 代码闭环 | 会话绑定 task/account/exact-origin/action；输入 URL、每跳 redirect 与 adapter 最终 URL 均复核，关闭会话不可复用；桌面形成 takeover → re-observe → release 闭环，并注册全局 emergency stop，后端一次取消所有非终态任务。跨模块安全回归 `200 passed`。 |
| MCP minimal adapter | 协议核心升级，仍保持 Preview | Streamable HTTP 已实现 `initialize`、版本/能力协商、`notifications/initialized`、session header/404 续期、DELETE shutdown、JSON/SSE、分页、大小限制、输入/输出 schema 复验及 registry 生命周期；工具调用支持 token-bound progress、精确 cancellation，并在 SSE 正常断开后按服务端 `retry` 携 `Last-Event-ID` 恢复。stdio 使用无 shell、最小环境和单操作进程生命周期，release profile 在可信 Windows 隔离完成前继续禁用。静态 bearer token 必须显式绑定规范化 resource/audience，持久设置拒绝原始 token/client secret。官方 `@modelcontextprotocol/conformance@0.1.16` 的 `initialize`、`tools_call`、`sse-retry` 分别通过 `1/1`、`1/1`、`3/3`，并已接入 CI；OAuth metadata/PKCE/client registration、elicitation 和更广恶意 server 互操作仍未完成，因此继续标记 Preview/R4，不宣称完整 MCP 互操作。 |
| RunState 无版本迁移 | 代码闭环 | checkpoint schema 已到 v3，支持 legacy/N-1/N-2 顺序迁移，拒绝非法或未来版本并禁止调度，恢复后写回规范化状态且保留私有 runtime metadata。 |
| 业务 trace、Memory 与 `outcome_unknown` 控制面不足 | 核心闭环，运营深化仍开放 | run/task/tool 已有稳定 parent/child trace anchor、状态和默认脱敏；桌面可审阅 Memory quarantine/version/conflict 并 promote/revoke；`outcome_unknown` 作为危险事件公开，明确“可能已执行”、要求人工核对并阻止自动重放。OTLP exporter、生产 SLO/告警以及面向每个 provider 的补偿动作仍属后续运营闭环。 |
| Windows OS sandbox | 仍为条件性 stop-ship | Windows 开发执行现在以 `CREATE_SUSPENDED` 创建子进程，先附加 kill-on-close Job Object（含进程数和内存限制）再恢复，消除了先执行后附加的抢跑窗口；创建、附加或恢复失败均 fail closed，非 Windows 请求隔离也会在启动前拒绝。隔离宿主 attestation 使用 Ed25519 签名挑战，绑定一次性 nonce、process/parent PID、短 TTL、host/policy SHA-256 与 AppContainer/受限 token/Job Object/network broker 能力，并拒绝重放、过期、未来时间、未签名结果或与 release pin 不一致的摘要。新增的原生宿主适配器要求整条路径无 reparse/junction、宿主为 release-pinned 且带可信 embedded Authenticode、固定 argv/无 shell/最小环境、固定工作目录、单进程 Job、请求/响应大小和单行 JSON 合同，并在执行前后复核 host/policy 摘要；合同见 `docs/architecture/windows-execution-isolation-host.md`。真实 Windows smoke 已验证挂起启动链路可运行；release profile 在缺少可信 attestation 时继续禁止任意 Skill/生成代码。仓库仍没有实现真正建立 AppContainer/受限 token/文件与网络 broker 的原生 launcher/broker 及候选 attack evidence；适配器、Job Object 和 attestation 合同不能替代该宿主。 |
| 真实 provider 评测 | 版本化 benchmark 代码闭环，真实结果仍阻断 | 在关闭 Mock fallback 的条件下，105 项版本化 benchmark 已经通过真实 `/api/runs`/`/api/chat` 执行链离线回放：`105/105` 通过，planning intent/tool/risk/schema 均为 `1.0`，缺参、结构化失败和未知工具均为 `0`，39 个 adversarial case 为 `39/39`。Browser 使用显式 task-local 文本夹具和 exact-host 夹具 allowlist，Document 使用隔离的测试 entitlement，二者都会在任务结束后恢复并写入 `benchmark_capabilities`，因此该结果证明确定性 planner/run-policy 与夹具执行链，不是 live hidden DOM、商业授权或真实 provider 质量证据。当前 OpenAI-compatible 配置指向 loopback/private base URL，正式 runner 按 SSRF 门禁在执行任何任务前拒绝；最新真实 provider 报告仍是旧的 130 项、成功率 `33.33%` 且 39 个 adversarial 中 36 个未通过全部断言，必须改用明确的本地 provider 类型或可达的非私有真实 provider，再用当前工作区重跑全部 130 项。 |
| 真实候选验签 | 密码学契约代码闭环，真实证据仍阻断 | release-owner 签收已从“非空字符串”升级为离线 detached Ed25519：规范 payload 绑定 repository、release tag、完整 candidate commit、candidate/reviewed-evidence run 与 attempt、build id、owner 和人工签收状态；严格门禁验证签名、payload digest、public-key fingerprint 和防重放绑定，私钥不进入 workflow。专项测试 `10 passed`，发布/证据相关回归 `89 passed`。Windows signing preflight 已按预期 fail closed：当前缺受保护签名变量、候选产物，`dist/backend.exe` 也没有有效 Authenticode。当前仍为 `manual_signoff_pending` / `PENDING_RELEASE_OWNER_SIGNATURE`；必须由受保护 RC workflow 和真实签名凭据生成同一候选绑定证据。 |

本轮另完成 Desktop typecheck、完整 Vitest `87 files / 346 tests passed`、完整 backend `3630 passed, 12 skipped`、后端定向 Ruff 全绿和 `git diff --check`；确定性 Planner/Supervisor/Goal Policy 的宽回归为 `255 passed`，代码审查收紧后的 Browser scope、Memory mutation、revoked-device 与 Document 混合读写回归为 `207 passed`，挂起创建的 Windows Job 链路单测为 `8 passed` 并完成一次真实 Windows smoke。新增的确定性计划完整性锁以 HMAC 绑定最终 tool/args，阻止 subagent、recovery 或 reflection 改写；相关 planner/eval/safety 回归 `210 passed`，关闭 Mock fallback 的 105 项端到端回放为 `105/105`。因此，原清单中可由仓库代码独立关闭的发布前 P0 缺陷已经收口；这不表示 P1/P2 深化项已经全部完成。2026-07-19 的续修已把字段级 lineage 接入九个模型驱动 Document 调用点并提供旧版可解析 sidecar，也把 conversation/session summary 迁移为认证 root-level lineage，同时关闭 Desktop 托盘/隐藏启动、语义窗口身份与 Mobile family generation/前后台锁定的新鲜度子项，并为 Android reviewed evidence 增加附件 SHA-256 manifest 和真实签名密钥指纹复核。摘要历史正文不可恢复、hostile SQLite 的完整降级检测、无 accessibility 语义的账号变化、coordinate/hotkey fallback、物理 Android/Keystore/DPoP 与在途副作用等边界仍开放；active 正文加密、MCP OAuth 扩展、OTLP/SLO 和 provider reconciliation 仍应按下文路线图继续推进。仍不能在本地伪造关闭的三项发布阻断证据是完整 Windows 隔离、通过阈值的真实 provider 报告、以及真实候选签名/owner 证据，发布应继续 fail closed。

本次续修又完成 MCP HTTP/stdio、SSRF/auth/resource、settings secret boundary 与 capability redaction 的聚焦回归，新增 SSE resume 单测并通过官方 lifecycle/tools/SSE conformance（合计 `5/5`）；Windows 隔离 adapter/签名挑战/release pin 专项为 `21 passed`，完整扩展安全门禁为后端 `101 passed` 加 Desktop IPC smoke 与供应链证据通过。真实-provider runner 在执行任务前按预期拒绝当前 loopback/private endpoint，没有生成伪造的新报告；Windows signing preflight 按预期报告候选产物、受保护 Azure/PFX 凭据和有效 Authenticode 缺失；`release:readiness:rc` 仍有 `7/7` P0 为 `in_progress`，而启用 strict state machine 的 `release:safety` 已通过并保持任意本地/代码执行默认关闭。上述外部证据阻断没有被确定性回放、模板或本地适配器冒充关闭。

2026-07-17 的架构复核没有通过提高阈值或新增豁免来消除告警，而是按高内聚 seam 提取 MCP protocol/HTTP stream、RunState checkpoint/event、确定性 planner 和 Browser safety/redaction module；当时 `npm run maintainability:gate` 通过，P95 文件大小 `779 <= 800`，后端最大文件 `1366 <= 1400`。模块拆分保留了既有公开 interface、monkeypatch seam 与 fail-closed 行为，MCP 官方 conformance、扩展安全门禁及 Browser/CUA `185/185` 定向回归均在拆分后复验通过。该数字是历史证据；2026-07-19 初始 dirty tree 曾回归到 P95 `805`、`ui_automation.py` 1470 行、`backendProcess.ts` 911 行，现已由上方 2026-07-19—20 续修数据取代。

## 2026-07-16 增量复核

### 执行结论

本轮结论不变：**确定性控制面达到强 Beta+，公开 RC/GA 仍为 No-Go。** 仓库已经具备独立策略执行、风险分级、审批绑定、工具调用 journal、恢复阻断、Electron IPC 边界、移动端撤销、审计链和候选供应链门禁等扎实基础；真正阻断发布的不是“少一个通用安全开关”，而是以下五个可验证闭环尚未同时成立：

1. 默认隐私策略与 local-first 承诺一致；
2. OS 级执行隔离、最小身份和网络出口控制可在真实 Windows 候选上证明；
3. 浏览器、MCP、Skill 和外部副作用都绑定任务级资源边界与可撤销授权；
4. 评测以最终环境状态和重复 trial 判定，而不是只看计划或模型文本；
5. clean candidate、真实 provider、clean-machine、物理 Android、第三方安全与 release-owner 证据全部绑定同一候选。

最新可用真实 provider 报告仍是 2026-07-11 的 130 项运行：任务成功率 `33.33%`、intent accuracy `57.58%`、tool overlap `57.58%`、risk match `86.21%`；39 个 adversarial case 中 36 个没有通过全部断言。这代表**门禁失败**，不代表“36 次攻击都成功”，但在通过的新候选结果出现前仍是 stop-ship 证据。

### 本轮行业共识：来源事实与落地推断

| 主题 | 一手资料中的事实 | 对 Lengrvis 的落地推断 |
| --- | --- | --- |
| Least Agency | [OWASP Agentic AI threats and mitigations](https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/) 将目标劫持、工具滥用、身份/权限滥用、记忆污染、级联失败和 rogue agent 等列为核心风险；[NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework) 要求把治理、测量和管理贯穿设计、开发、部署与评测。 | 默认采用“单编排器 + 严格工具合同”；只在可并行、上下文隔离或专业化有可测收益时启动子 Agent，并由成功率、成本、超时和风险预算共同守门。 |
| 隔离优先于反复弹窗 | Anthropic 的 2026 containment 工程报告指出，单靠人工批准会出现批准疲劳；其产品数据中用户批准了约 93% 的请求，OS 沙箱使权限提示下降约 84%。这是单一厂商测量，不是普适基准，但清楚说明审批不能替代 containment。[来源](https://www.anthropic.com/engineering/how-we-contain-claude) | 在 AppContainer/受限令牌、Job Object、独立工作区和网络 broker 形成候选证据前，任意本地 Skill、生成代码和通用 Shell/Python 继续默认关闭。高风险确认保留在真正的 point of risk。 |
| Windows Agent 身份边界 | Microsoft 的 [Windows agentic security](https://learn.microsoft.com/en-us/windows/security/book/operating-system-agentic-security) 指南建议把 Agent 放在不同于用户的身份/账户与工作区中，能力显式授予、可撤销，并对组件进行可信签名；[Application Isolation](https://learn.microsoft.com/en-us/windows/security/book/application-security-application-isolation) 将 AppContainer capabilities 和 Windows Sandbox 作为主要隔离机制。 | 后端进程、执行 worker 与用户桌面会话解耦；凭据经 vault/proxy 按任务注入，不进入模型上下文或生成代码目录；本地 connector/Skill 需要签名、权限声明和撤销表。 |
| 浏览器与 URL 数据外发 | OpenAI 的 2026 agent link-safety 说明，仅有 trusted-domain list 仍挡不住重定向和带秘密参数的 URL；应验证准确 URL/跳转并对无法验证的外发动作警告或确认，同时继续采用纵深防御。[来源](https://openai.com/index/ai-agent-link-safety/) | browser/CUA/MCP 共用 task-bound egress policy：精确 origin、账号、动作和接收方；禁止把秘密放进 URL；每次重定向、DNS 解析、上传、下载、登录或跨域外发都重新校验，超出任务 capsule 时重新授权。 |
| MCP 是不可信进程与协议边界 | [MCP Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices) 明确禁止 token passthrough，并覆盖 confused deputy、SSRF、session hijacking 和本地 server 同权限风险；[2025-11-25 tools 规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) 定义 input/output schema 与工具注解，但远端注解不能作为可信安全事实。 | 继续把当前实现描述为“MCP HTTP JSON-RPC compatibility adapter”，第三方工具保持 R4/default-deny/Preview；补 lifecycle、版本协商、OAuth audience、schema 复验、大小/超时、progress/cancel、provenance 和 conformance suite 后，才可宣称完整互操作。 |
| 最终状态评测 | Anthropic 的 Agent eval 指南把 task、trial、grader、transcript、outcome 和 harness 分开，并要求用最终环境状态判定多轮任务、对 LLM grader 做人工校准。[来源](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | 每个风险类别用真实/高保真环境断言文件、DB、审批、消息发送、浏览器和设备后态；nightly 对关键题运行 3-5 trials，保留 hidden holdout、失败分类、完整 trace 与人工抽检。 |
| 显式持久状态 | Google ADK 的长任务实践建议用 durable state、checkpoint 和事件驱动的暂停/恢复，避免从聊天历史猜测进度。[来源](https://developers.googleblog.com/build-long-running-ai-agents-that-pause-resume-and-never-lose-context-with-adk/) | `RunState` 增加 schema version、append-only 事件与 N-1/N-2 migration；恢复、重试、取消和副作用写入都必须幂等，并从最后已提交事件继续。 |
| 可观测但默认不采正文 | [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/) 覆盖 agent、conversation、model、tool、token、延迟和 eval，但同时提示输入、输出、工具参数可能含敏感信息。 | 形成 Electron → HTTP/WS → run → plan → model → tool → approval/handoff → recovery 的 parent/child trace；默认只采低敏元数据和聚合指标，Prompt、正文、raw args、截图、token 与私有路径需显式 opt-in、脱敏、TTL 和可删除。 |
| 桌面壳层与供应链 | [Electron Security Checklist](https://www.electronjs.org/docs/latest/tutorial/security) 要求 sandbox/contextIsolation、禁用远端 Node integration、限制导航/新窗口/openExternal、校验 IPC sender 并保持版本更新；[GitHub Artifact Attestations](https://docs.github.com/en/actions/concepts/security/artifact-attestations) 可绑定 workflow、repo、commit、event、provenance 和 SBOM，但证明来源不等于证明产物安全。 | 现有 Electron 与 attestation 代码方向正确；发布门槛仍必须包括真实 RC 在线验签、同一 candidate identity、人工证据复核、可复现性/差异解释和撤销路径。 |

### 仓库差距矩阵（2026-07-16 静态核验）

| 优先级 | 发现 | 仓库证据 | 发布要求 |
| --- | --- | --- | --- |
| P0 | Responses/CUA 默认允许 provider 存储，与 local-first 默认值冲突 | `backend/app/config.py:402` 的 `disable_response_storage` 默认 `False`；`backend/app/llm/openai_compatible.py:520-526,686-692,864-873` 与 `backend/app/llm/cua_provider.py:112-117` 据此发送 `store` | 默认 `store:false`；只有清楚展示 provider、用途、保留期、删除/ZDR 影响并显式 opt-in 后才开启；诊断页显示实际 wire policy |
| P0 | 路径 policy/risk 在 canonicalization 前判断 | `backend/app/policy/permissions.py:485-495` 仅做斜杠/大小写归一后 `fnmatch`；`backend/app/policy/dynamic_risk.py:269` 的路径标准化不折叠 `..` | 生成唯一 canonical resource identity，并贯穿 policy、risk、dry-run、approval binding 和执行点复核；补 `..`、UNC、junction/symlink、8.3、大小写和多路径测试 |
| P0 | Browser/CUA 缺任务级 exact-origin/account/action 边界；人工 takeover 和全局急停未闭环 | 本地 host 在 `desktop/src/main/browserHost.ts:269-279` 已有 takeover 状态切换，但 renderer 在 `desktop/src/renderer/components/BrowserActivityPanel.tsx:72,417` 硬禁用入口，bridge 在 `desktop/src/main/browserHostBridge.ts:118-119` 拒绝远端 takeover；`desktop/src/main/main.ts:319` 仅注册窗口显示快捷键 | 补齐 backend/remote takeover 的授权 capability，实现“暂停 Agent → 人工接管 → 重新观察 → 重新确认 → 交还”的原子状态机和常驻 emergency stop；以 Stop p95 < 1 秒作为候选目标 |
| P0 | 真实结果质量和候选证据不通过 | `.tmp/qa-evidence/real-llm-eval/real-llm-eval-report.json` 的 130 项报告未过门槛；`docs/release/release-readiness-dashboard.md:26-32` 的 RR-P0-001 至 007 全部 `in_progress` | 修复 planner/policy/tool/outcome 失败并用后态与重复 trial 重跑；clean-machine、物理 Android、第三方复测、诊断外发审查和 owner sign-off 绑定同一 clean candidate |
| 条件 P0 | MCP 只是最小兼容层 | `backend/app/mcp/client.py` 是 minimal HTTP JSON-RPC adapter，覆盖 `tools/list`、`tools/call` 和 basic `resources/list`；缺 initialize、capability/session、标准 transport、progress/cancel 与服务端输出 schema 复验 | 保持 Preview、R4 和 default-deny；若进入默认自治或宣称完整 MCP，全部缺口升级为发布硬门槛 |
| P1 | 本地危险执行仍无 OS sandbox | `backend/app/skills/sandbox.py:263` 明示 Python/Shell handler 无 OS sandbox；release profile 当前正确地 fail closed | AppContainer/受限 token/Job Object/文件与网络 broker、独立工作区及真实 Windows attack test |
| P1 | 持久 RunState 无 schema migration | `backend/app/orchestration/execution_models.py:67` 定义 `RunState`；`backend/app/services/run_service.py:1089` 直接 `model_validate` 持久 JSON，未见 schema version | 版本化 checkpoint，N-1/N-2 恢复回归，崩溃注入和副作用幂等/对账 |
| P1 | 控制后端强于用户控制面 | Memory 后端已有 quarantine/version/conflict；桌面仍缺完整检疫/冲突控制；`outcome_unknown` 缺“可能已执行、核对、补偿/撤销、禁止自动重试”专用 UI | 将 memory、approval scope、预算追加、outcome reconciliation 和 rollback truth 变成用户可理解、可修改、可撤销的产品状态 |
| P1 | 可观测性仍是进程内原语 | `backend/app/observability/tracing.py` 只有轻量 span；本轮补充的 UIAutomation 与 context-compaction 固定标签 counters 仍驻留内存，尚未覆盖跨功能 approval outcome，也未形成业务 parent spans/OTLP/GenAI-MCP 映射 | 默认脱敏的端到端 trace、SLO/告警、IR 演练与 opt-in exporter；不得记录原始 Prompt/正文/参数/截图 |
| P1 | 候选供应链门禁有代码、无真实候选验签 | `.github/workflows/release-candidate.yml:267-274` 使用固定 SHA 的 `actions/attest` 生成 provenance/SBOM bundle | 在真实 GitHub RC 保存在线验签、artifact/SBOM/digest/candidate identity 一致性和 reviewer 签收；attestation 不替代安全测试 |

### 建议的量化发布门槛

| 维度 | RC 门槛 |
| --- | --- |
| 对抗安全 | 所有必需 adversarial case 逐项 100% 通过；不以总体均值豁免单次越权 |
| 任务结果 | 按 read/write/browser/document/memory/mobile/developer 分类，以环境后态判定；关键题 3-5 trials，并同时报告 pass@1、方差和人工纠正率 |
| 副作用 | 故障注入后重复副作用为 0；未知结果 100% 进入人工对账，禁止自动重放 |
| 权限边界 | 未声明 origin/account/action/path/tool 默认拒绝；canonical identity 漂移、重定向或目标变化 100% 重新授权或阻断 |
| 人工控制 | 高影响动作 100% point-of-risk confirmation；拒绝/接管一步可达；Stop p95 < 1 秒；恢复前重新观察和确认 |
| 隔离 | 任意代码/Skill/Shell 不能越出工作区、网络和凭据范围；逃逸/注入/凭据外泄矩阵 100% 阻断 |
| 隐私 | provider 存储默认关闭；诊断/telemetry 默认不含正文、凭据、截图和私有路径；导出前可预览并有 TTL/删除 |
| MCP/供应链 | 未过 conformance 不宣称完整互操作；每个候选产物有 digest、SBOM、可验证 provenance、同一 candidate identity 与撤销路径 |
| 可访问性 | 自动 serious/critical 为 0；Windows 候选通过 keyboard、NVDA、high contrast、200%/400% zoom 与文本缩放 |

### 分阶段路线图

#### P0：发布前 / 0-2 周

1. 把 Responses/CUA 改为 `store:false` 默认，并加入配置、wire payload 与 UI/诊断回归。
2. 统一 canonical path/resource identity，修复 policy/risk/approval/execution 不一致；新工具和未知 capability 改为明确 default-deny。
3. 建立 browser/CUA/MCP 共用的 task-bound origin/account/action/recipient/egress capsule，补每跳 redirect/DNS/URL-secret 测试。
4. 完成全局急停和浏览器接管状态机；危险本地执行继续禁用，直到 Windows containment 有候选证据。
5. 按最终环境后态重跑真实 provider gate；关键安全任务 3-5 trials，失败全量归因，达标前冻结新增 R2/R3 能力。
6. 形成 clean candidate，完成真实 RC 验签、clean-machine、物理 Android、第三方复测、诊断外发审查和 owner sign-off。

#### P1：GA 前 / 2-6 周

1. 为 RunState/checkpoint 增加版本、迁移、append-only 事件和 crash/resume 回归。
2. 增加 AppContainer/受限 token/Job Object/文件与网络 broker、独立 agent workspace 和短期凭据注入。
3. 补 Memory quarantine/conflict/version、approval scope 修改、预算追加与 `outcome_unknown` 对账 UI。
4. 为邮件、安装、云提交等外部副作用增加 provider receipt、幂等键和 reconciliation probe。
5. 补 MCP 2025-11-25 lifecycle、授权、transport、schema/provenance、progress/cancel 和恶意 server conformance；未完成前保持 Preview。
6. 形成默认脱敏的 OpenTelemetry 业务 trace、SLO、告警与 incident-response 演练。

#### P2：企业化 / 6-12 周

1. 对 task/chat/memory/plan/tool/approval 敏感正文实施分层应用加密、权威 retention/erase 与可选外部审计锚定。
2. 建立签名和可撤销的 Skill/MCP/model/prompt/policy/tool capability registry。
3. 用生产 opt-in trace、安全事件和人工反馈扩充 hidden eval corpus，建立 1% → 10% → 50% canary 与自动回退。
4. 提供默认关闭的企业 telemetry/exporter、数据驻留和审计策略包。

### 方法与限制

- 本轮复核日期为 2026-07-16，使用 NIST、OWASP、CISA/NCSC、MCP、Electron、OpenAI、Anthropic、Microsoft、OpenTelemetry 和 GitHub 的官方/一手资料；厂商内部测量只作为方向性证据，没有当作通用基准。
- 仓库侧采用只读静态核验、现有测试/评测产物与发布 dashboard；本轮只额外运行了 Desktop typecheck，未重跑真实 LLM、完整 backend、Windows GUI、物理 Android、真实 MCP server、第三方渗透或候选发布 workflow。
- 工作区仍是 dirty tree，因此历史通过数字、代码门禁和本文档都不能替代 clean candidate 的可复现证据或独立发布签收。

## 2026-07-13 增量复核

### 结论

本轮结论仍是：**控制架构达到强 Beta+，公开 RC/GA 为 No-Go。** 过去两天的工作区变化加强了 API 错误脱敏、请求限流、LLM retry/circuit breaker、敏感记录完整性、发布产物校验、renderer error boundary 和集合型面板的故障恢复；但这些变化尚未形成 clean candidate，也没有新的真实 provider、clean-machine、物理 Android 或第三方安全证据，因此不能改变发布判断。

最新可用真实 provider 报告仍是 2026-07-11 的 130 项运行：任务成功率 `33.33%`、intent accuracy `57.58%`、tool overlap `57.58%`、risk match `86.21%`；39 个 adversarial case 中 36 个未通过全部安全断言。它证明的是**评测门禁失败**，不等于 36 次攻击都成功；但在没有更新且通过的候选绑定结果前，它仍是 stop-ship 证据。

### 本轮新增或重新定级的发现

1. **P0：云端响应存储默认值与 local-first 定位冲突。** `AppSettings.disable_response_storage` 当前默认 `False`，Responses 与 CUA provider 因而发送 `store: true`。本地 OS Agent 应默认 `store:false`，只有在设置页清楚说明 provider retention、用途、保留期与删除方式并获得用户显式选择后才启用；同时让诊断页可查看当前实际 wire policy。该缺口不代表 Chat Completions 自动存储，但它会让“隐私优先”配置在切换 wire API 或 Computer Use 路径后静默改变。
2. **P0：权限路径规则和动态风险必须基于 canonical path。** `backend/app/policy/permissions.py` 当前仅统一斜杠与大小写后执行 glob；`backend/app/policy/dynamic_risk.py` 的 `PureWindowsPath` 处理也不会折叠 `..`。本轮最小复现显示，规则 `D:/workspace/safe/*` 会允许 `D:/workspace/safe/../secret.txt`；同样，直接的 `C:\Windows\...` 被提升为 R2，而 `C:\Users\Public\..\..\Windows\...` 仍被识别为 R0。下游 `resolve_authorized`、reparse-point 和 handle 复核仍会阻止越出全局授权根目录，因此这不是无条件任意文件读取；但在全局根目录较宽时，可绕过更细粒度的用户路径 allowlist 或风险提升。发布前应先 canonicalize、拒绝歧义路径，再用同一 canonical resource identity 做 policy、risk、dry-run、approval binding 与执行点复核。权限策略在没有 allow rule 时还会 default-allow 低风险工具；应改为显式 baseline capability/default-deny，并在 UI 解释规则组合语义。
3. **P0：浏览器/CUA 缺任务级 exact-origin 与账号边界。** 全局 browser network 默认关闭、URL/SSRF/DNS pinning 很强；但启用后只要是合法公网 URL 即可访问，未发现与任务绑定的 domain/account/action allowlist。应在任务开始时绑定 exact origins、目标账号和允许动作，跨 origin、重定向新域、登录、上传、下载或数据外发时重新授权；凭据任务使用一次性 partition，任务结束销毁 cookie/cache。[OpenAI Computer use](https://platform.openai.com/docs/guides/tools-computer-use) 明确建议预先限定网站、账号和动作范围。
4. **P0：真实人工接管闭环尚未完成。** 任务级 Pause/Stop/Cancel 已存在，但 Browser Activity 明确把 takeover 设为不可用，主进程 bridge 也拒绝该动作；标题栏/托盘目前只有显示窗口快捷键，没有常驻全局急停。行业实践要求“暂停 Agent → 人工接管 → 重新观察 → 重新确认 → 交还”是原子状态机，并量化 stop-to-quiescence 延迟，而不是只提供局部停止按钮。OpenAI Computer use 也要求高影响动作保留 human-in-the-loop 与明确 handoff。
5. **P0：评测必须从计划正确扩展到执行后态。** 现有 harness 分层和 fail-closed 设计较强，但大量 case 仍是 narrated/extracted fixture。OSWorld、AgentDojo、BrowserGym 与 τ-bench 的共同启示是：以真实环境初态/后态、恶意工具数据、重复运行可靠性和最终系统状态判定任务，而不是只判模型输出。历史 benchmark 数字仅用于说明评测方法，不代表当前模型能力。
6. **条件 P0：MCP 仍是受限兼容层。** 当前客户端是 minimal HTTP JSON-RPC adapter，覆盖 `tools/list`、`tools/call` 和 basic `resources/list`，但没有 `initialize`、capability/session、Streamable HTTP/stdio、progress/cancel；注册表还把远端 `outputSchema`/annotations 折叠为通用对象，调用后不按服务端 schema 校验结构化结果。只要第三方 MCP 继续统一 R4/default-deny 并准确标为 Preview，可列 P1；若要宣称完整 MCP 兼容或允许 Agent 自动执行，则 lifecycle、OAuth、输出验证、provenance、大小限制和 conformance suite 是发布硬门槛。
7. **P1：持久 RunState 需要 schema version 与迁移。** `RunState` 使用 `extra="forbid"`，恢复路径直接 `model_validate` 持久 JSON，但未发现状态版本和 migration。字段演进可能让旧暂停任务无法恢复；应像数据库迁移一样版本化 checkpoint，并对 N-1/N-2 状态做恢复回归。
8. **P1：控制后端强于用户控制面。** Memory 后端已有 quarantine/promote/revoke/version/conflict，但桌面 MemoryPanel 只展示内容、标签和删除；`outcome_unknown` 也缺少“可能已执行、核对、标记完成、补偿/撤销、禁止自动重试”的专用 UI。审批仍只有泛化“批准/拒绝”，缺少“修改范围/返回计划/部分批准后重新绑定”。
9. **P1：可观测性原语尚未形成业务 trace。** 自研 `span()` 有 trace/span id，但除定义外未发现业务调用点，也没有 parent span、跨线程 context、OTLP exporter 或 GenAI/Agent/MCP 语义映射。应形成 HTTP → run → turn → plan → agent → model → tool → approval/handoff → recovery/rollback → memory 的脱敏 trace；Prompt、正文、原始参数和截图默认不记录。
10. **P1：本地可信边界仍需补强。** 实际 `windows_execution_isolation_host` 不存在，当前正确策略是 release profile fail closed；task/chat/memory/plan/tool/approval 正文仍主要以 SQLite 明文保存；审计 anchor、数据库和 HMAC secret 同属本机同用户边界；本地日志仍需手动清理。危险执行继续禁用，随后补 Windows broker、active 数据加密、日志权威 retention/擦除和可选外部审计锚定。
11. **P2：按 provider 能力深化适配，不把厂商特性混成通用协议。** OpenAI 路径已支持基础 Responses API，但默认仍是 Chat Completions，工具历史还会回退，未利用 state chaining、server compaction 或 background mode。可单独演进 OpenAI adapter，同时明确 background/compaction 的数据保留与 ZDR 影响；多 provider 与本地模型继续走稳定的内部消息/工具合同。A2A 只在未来确有跨厂商 Agent 互操作需求时再增加，不应为了追逐协议扩大当前攻击面。

### 本轮验证证据

- Backend 与本调查直接相关的两组聚焦回归：`147 passed`；`370 passed, 4 skipped`。4 个 skip 均因当前 Windows 进程没有创建 symlink 的权限，不是断言失败。
- Desktop：`npm --prefix desktop run typecheck` 通过；Vitest `78` 个文件、`320` 项测试通过。
- `npm run security:threat-model` 通过；`check_full_review_scorecard.py --allow-dirty` 验证文档结构仍为 `94/100`。后者只是 dirty-tree 文档校验，不是候选证据，也不是独立第三方评分。
- `npm run release:readiness:rc` 按预期失败：7/7 个 RR-P0 均为 `in_progress`，候选/CI evidence 仍绑定旧 commit，当前树非 clean，人工签收与 owner signature 缺失。
- `git diff --check` 未发现 whitespace error，仅输出 LF/CRLF 工作副本转换提示。

### 更新后的近期顺序

1. 先把 Responses 的默认策略改为 `store:false`，再修 canonical path policy/risk 不一致，并补 `..`、UNC、junction/symlink、8.3 名称、大小写和多路径参数回归。
2. 冻结新增 R2/R3 能力，用当前 scorecard 修复真实 provider 的 planner/policy/tool/outcome 失败并重新跑候选绑定 gate。
3. 为 browser/CUA 增加任务级 exact-origin/账号/动作边界，并完成全局急停、浏览器人工接管、修改范围/返回计划、Memory 检疫与 `outcome_unknown` 对账 UI。
4. 保持本地 Skill、生成代码和通用 Shell/Python 在 release profile 禁用，直到 AppContainer/受限令牌/Job Object/network broker 有真实 Windows 证据。
5. 形成 clean candidate，完成 clean-machine Windows、物理 Android LAN/WSS、日志/诊断包 retention 与人工外发审查、第三方渗透/fuzz 和 release-owner 签收。

## 0. 当前决策摘要

**当前判断**：Lengrvis 的确定性控制面达到“强 Beta+”，但不满足公开 RC/GA 签收条件。适合继续本地开发、内部验证和受控 Beta；不应扩大默认自治范围，也不应把当前 MCP、移动审批、代码执行或发布证据描述成完整生产级能力。

**已由当前工作区和针对性测试证明的核心控制**：审批 TTL 与认证上下文、事务化 ToolCall journal、`outcome_unknown` 自动重放阻断、UIAutomation 唯一目标与审批后指纹复核、真实 rollback 分态、任务 Stop/Pause/Resume/Cancel、renderer method + route allowlist、BrowserHost 下载拒绝、移动 refresh family/revoke、多父及恢复 provenance、Memory namespace/单 active successor、权威 retention/原子擦除、分层 eval scorecard，以及候选 SBOM/provenance 双验签。2026-07-11 的最终完整 backend 回归为 `3292 passed, 12 skipped`；成熟度收口合并回归另为 `229 passed`，矩阵文档契约 `2 passed`，Desktop typecheck 通过；此前桌面聚焦 Vitest 为 `23 passed`。

**当前 stop-ship**：真实 provider 评测共运行 130 项，任务成功率 `33.33%`、intent accuracy `57.58%`、tool overlap `57.58%`、risk match `86.21%`，39 个对抗 case 中有 36 个未通过安全断言。该数字表示评测门禁失败，不等同于“36 次攻击均成功”，但足以阻断候选发布。发布 dashboard 的 RR-P0-001 至 RR-P0-007 仍全部为 `in_progress`，包括 clean-machine、物理真机/LAN-WSS、结果质量、诊断包人工审查、候选签收和残余风险接受。

**条件性 stop-ship**：在 Windows OS sandbox、受限令牌/Job Object、文件与网络 broker 形成候选证据前，继续默认关闭任意本地 Skill、生成代码和通用 Shell/Python 执行。路径校验、环境变量过滤和超时不能替代 OS 隔离。

**下一步优先级**：

1. 使用新的分层 scorecard 重新运行真实 LLM gate，修复 document/memory/write 的实际 planner、policy、tool 或 outcome 问题；在达标前冻结新增 R2/R3 工具与默认自治深度。
2. 完成 clean-machine Windows、物理 Android、诊断外发人工审查、第三方渗透/fuzz 和 release-owner sign-off，形成 candidate-bound 证据。
3. 为外部副作用补 receipt/reconciliation probe 和 provider 级安全重试清单；本地 execution key 只能阻止 Lengrvis 自身重复发起，不能证明外部系统只执行一次。
4. 在真实 GitHub RC 上执行并保存候选 SBOM/provenance 验证结果；继续推进可复现构建、Windows OS sandbox 和 Android Keystore/DPoP。
5. 对已接入的 Document 与 conversation/session summary lineage 做候选升级/降级演练；若 hostile SQLite 属正式威胁模型，再增加外部保护的单调迁移标记；继续推进 Memory 检疫 UI、active 数据加密、MCP lifecycle/conformance、OpenTelemetry GenAI/MCP 语义、NVDA/高对比度/缩放和企业 exporter。

**证据置信度**：静态架构与针对性自动测试为高；真实 provider 质量结论为高；Windows GUI、物理移动设备、外部系统对账、第三方攻击面和候选安装体验仍为中低，必须以动态候选证据补齐。

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

本矩阵评的是**确定性控制和工程治理成熟度**，不把模型效果或候选签收偷换成成熟度。达到“中上”必须同时具备：默认启用或 fail-closed/default-off 的真实控制、自动测试、可审计证据和明确残余风险。危险能力只有在发布版无法绕过门禁启用时，才允许以“中上（禁用态）”计分。评级不等于 RC/GA 通过。

| 领域 | 控制成熟度 | 当前证据与结果 | 仍保留的上限 | 发布状态 |
| --- | --- | --- | --- | --- |
| 风险分级与策略执行 | 中上（待修） | R0-R4、独立 PolicyEngine、执行点复核、intent capsule、plan revision 与资源范围绑定 | 本轮确认自定义路径 glob 与 dynamic-risk 在 canonicalize 前评估，`..` 可令规则/风险分类与真实目标不一致；下游授权根仍提供边界保护 | 发布前修复 |
| 审批安全 | 强 | 参数/预览/设置/权限/provenance HMAC、风险分级 TTL、认证上下文、原子单次消费；Desktop generation 覆盖启动/锁屏/挂起/托盘/隐藏启动并在前台恢复后重建，语义 UIA 同时绑定顶层窗口身份；Mobile access/refresh/approval/claim 绑定 family generation，前后台跨越须重新认证读取与 refresh | 不暴露 accessibility 语义的应用内部账号变化、coordinate/hotkey fallback、物理移动锁屏证据以及已线性化在途副作用仍需额外边界 | 控制可用，P1 边界待补 |
| Prompt injection 与 provenance | 中上 | 浏览器/文档/MCP/工具输出统一 envelope；所有 `depends_on` 父链进入 runtime，下一跳输出合并全部 upstream taint；暂停/重启重建父链；RFC 6901 字段 lineage 已进入九个模型驱动 Document 调用；会话摘要以 HMAC 根级 `summarize/merge` 映射绑定摘要、session、anchor、source IDs 和实际输入，legacy 文本保守迁移为 tainted root | 确定性文档转换仍保守使用 root lineage；legacy 历史正文无法恢复，完整 hostile-SQLite 降级检测需外部单调迁移门 | 受控可用，P1 深化 |
| 长期记忆 | 中上 | quarantine、TTL、完整性验证、显式 promote/revoke；principal/workspace/domain namespace、version/supersedes/conflict 隔离；单 active successor 数据库约束、权威 retention 与原子擦除 | 缺桌面检疫/冲突 UI；active 用户确认记忆仍依赖本机 ACL 明文存储 | 受控可用 |
| 代码与 Skill 执行 | 中上（禁用态） | release profile 无完整 attestation 时确定性禁止本地 Skill、生成代码写入和 runtime override；只读分析工具可保留 | 仓库仍无 AppContainer/受限令牌/Job Object/network broker host | 危险执行保持禁用 |
| 工具持久化与故障恢复 | 强 | execution key、唯一索引、prepared/executing/committed/outcome_unknown、故障恢复和自动重放阻断 | 外部邮件、安装、云提交仍需 provider receipt/reconciliation | 控制可用 |
| 自治预算与熔断 | 中上 | run budget ledger、tool/turn/并发/恢复上限、重复动作阻断、Stop/Cancel 与 fail-closed retry contract | 预算追加产品 UI和按 provider 验证的安全重试清单未完成 | 受控可用 |
| Windows UIAutomation | 中上 | 候选计数、唯一匹配、runtime/process/rect 指纹、所属顶层窗口 runtime id/handle/process、可执行文件与进程实例 HMAC、完整同进程 accessibility 父链 HMAC，claim 前及 Invoke/SetValue 前复核；禁用/离屏/截断/身份不可证明均 fail closed | accessibility 不可见的应用内账号、跨进程/受保护 provider、DPI/多屏与 coordinate/hotkey 截图证据未完成；系统调用后的竞态不能原子消除 | 语义路径受控可用 |
| Electron 安全 | 强 | sandbox、contextIsolation、禁 Node、IPC sender 校验、method+route allowlist、CSP/Fuses、BrowserHost 下载拒绝；打包主 renderer 使用最小权限 `app://local` handler，`file://` 不再可信，raw path/realpath/MIME/CSP/CORP 边界已有单测与本机未签名 ASAR 动态证据；阻止下载事件的 query/fragment/userinfo 已统一脱敏 | 新 typed IPC 仍需安全 owner 审查；签名候选上的跨来源负测/WSS/更新后启动及显式下载 broker 仍待签收 | 控制可用，候选证据待补 |
| 本地 API 与网络 | 中上（待修） | loopback token、CORS/Origin、WS guard、LAN TLS gate、SSRF/DNS-IP pinning；browser network 默认关闭 | browser/CUA 启用后缺任务级 exact-origin、账号和动作 allowlist，跨域/数据外发边界需重授权 | 发布前修复 |
| Android Companion | 中上/Preview | 单次配对、family-generation access/refresh 轮换、复用检测、设备撤销与前后台 fail-closed 锁定；无可信 PoP 时高影响审批 fail closed；exact-origin/port pin 已闭环；reviewed evidence 强制候选绑定的 SHA-256 附件 manifest 与 v2 签名密钥指纹 | Android Keystore/DPoP、物理锁屏/生物认证和真实候选真机附件尚未签收 | Preview，GA 阻断 |
| Human-AI 控制与可访问性 | 中上（任务级） | Stop/Pause/Resume/Cancel、共享 AccessibleDialog、键盘焦点契约、axe serious/critical 门禁 | Browser takeover 尚未启用，缺常驻全局急停、修改计划/缩小范围、NVDA/高对比度/200%-400% 缩放候选证据 | 发布前补闭环 |
| 审计与可观测性 | 中上 | HMAC 链、敏感记录完整性、run/tool/approval 事件、trace/span、Prometheus、默认脱敏，以及 UIAutomation action/screenshot/approval-gate 与 context-compaction decision 固定闭集计数 | 跨功能 approval outcome、同用户攻击者模型、外部锚定、IR 演练和 OTel 跨组件 parentage 未完成 | 受控可用 |
| 供应链与发布 | 中上 | hash lock、SBOM、签名门禁、候选 SHA-256 subject manifest、GitHub build/SBOM attestation；publish 在 materialize 前逐 artifact 复核路径、digest、双本地 bundle/predicate、signer workflow、source digest，并核对后发布签名 SBOM | 尚未在真实 RC run 中取得线上证明；可复现构建和运行时 Prompt/tool attestation 未完成 | 代码门禁完成，候选证据待跑 |
| 真实评测与发布证据 | 中上（评测系统） | 130 项真实 provider gate、adversarial corpus、五层 scorecard、失败主分类/error code/脱敏 diagnostic、缺证据计入分母、任何评测失败与漏归因均 fail closed | 当前质量结果仍失败；clean-machine、物理真机和第三方测试未签收 | 质量 gate 阻断 |
| 隐私与合规运营 | 中上（待修） | local-first、录屏加密/opt-in、分级 task/memory retention、原子一键擦除、secure_delete、WAL truncate/VACUUM、诊断脱敏 | Responses 默认发送 `store:true`；active 正文未全面应用层加密；日志需手动清理；PIA/法务签收未完成 | 发布前修默认值 |
| MCP 集成 | 中上（受限兼容层） | schema 校验、SSRF pinning、owner/policy/allowed_tools、第三方 R4 handoff，且文档明确为 HTTP JSON-RPC compatibility adapter | 尚无 initialize/capability/session/Streamable HTTP/progress/cancel conformance | 不宣称完整互操作 |

**矩阵结论**：17/17 个控制领域均达到“中上”或更高；其中代码执行通过安全禁用达到门槛，评测与供应链通过 fail-closed 门禁达到门槛。2026-07-13 因 canonical path policy/risk、browser task boundary、takeover/global stop 和 Responses storage 默认值的新证据，相关四行被降为“中上（待修/任务级）”。真实 LLM 质量和候选发布证据仍然失败或待执行，因此公开 RC/GA 结论不变。

## 4. 做得好的实践

### 4.1 独立策略层，而不是依赖 Prompt 自律

`backend/app/policy/policy_engine.py`、`backend/app/orchestration/tool_runtime.py` 和执行 handler 将模型建议与真实副作用分开。浏览器内容出现注入信号时，post-tool review 会拒绝继续执行，而不是要求模型“忽略恶意指令”。这符合 OWASP ASI01、ASI02 和 ASI08 对独立策略执行的要求。

### 4.2 审批具备抗重放和抗篡改属性

`backend/app/policy/approval_binding.py:81` 将 task、step、tool 和 canonical args 绑定；同文件还绑定预览、设置与权限策略版本。`backend/app/core/db_approvals.py` 在副作用前原子消费审批。相比只传 `approved=true` 的常见实现，这一设计成熟得多。

### 4.3 Electron 主渲染器与嵌入网页均有独立硬化

`desktop/src/main/main.ts` 使用 `contextIsolation: true`、`nodeIntegration: false`、`sandbox: true`，并在打包态只加载 `app://local/index.html`；`desktop/src/main/rendererProtocol.ts` 以精确 authority、raw path、realpath containment、普通文件/MIME allowlist 与严格响应头提供 ASAR 资源，`rendererTrust.ts` 拒绝所有 `file://` IPC sender。`browserHostWebContentsHardening.ts` 对远程网页拒绝 popup、权限和越界导航；`electron-builder.yml` 关闭危险 Fuses 并启用 ASAR 完整性和 cookie 加密。

### 4.4 网络边界采用 fail-closed 和 DNS pinning

`backend/app/security/desktop_api.py` 对除匿名 health 外的桌面 API 要求本地随机 token，WebSocket 同时检查 token 与 Origin。`backend/app/core/outbound_url.py` 在连接前解析并固定目标 IP，避免 DNS rebinding TOCTOU；这与 MCP Security Best Practices 的 SSRF 建议高度一致。

### 4.5 移动配对不是“局域网明文 + 长 token”

`backend/app/services/mobile_pairing_service.py:117` 使用 5 分钟单次 pairing code 和额外 32-byte claim secret；非 loopback 配对要求 TLS ready。移动端在 `mobile/src/store/auth.ts:98` 将 token 放入 SecureStore，并保存/恢复证书信任状态；远程输入另有短期 grant、scope、撤销和 active-grant 绑定。

### 4.6 发布证据明确区分“代码存在”和“能力已验收”

`docs/release/release-readiness-dashboard.md`、`docs/qa/agentic-product-evals.md` 与 evidence verifier 明确区分机器证据、人工证据、模板、waiver 和 owner sign-off。这符合 NIST GenAI Profile 对 pre-deployment testing、incident evidence 和治理职责的要求，也有效降低发布材料夸大的风险。

## 5. 关键差距与建议

### P0-1 建立正式威胁模型与控制映射

**实施状态（2026-07-11，基本闭环）**：仓库已新增版本化 threat model、OWASP ASI01-ASI10 JSON control map、owner/residual risk/test 映射和 CI/release validator。剩余项是把安全负责人接受与具体 candidate identity 绑定，并随新增工具、权限、数据源、Agent 或远程通道持续更新。

控制映射至少覆盖：

- 用户、Electron renderer、main/preload、FastAPI、SQLite、LLM provider、浏览器内容、文件/文档、Skill、MCP、移动设备和 LAN。
- 恶意网页/文档、恶意 MCP/Skill、被攻陷 Provider、同用户恶意进程、LAN MITM、丢失手机、更新链攻击。
- OWASP Agentic Top 10 ASI01-ASI10 与现有控制、测试、owner、残余风险的逐项映射。
- 每次引入新工具、权限、数据源、Agent 或远程通道时必须更新的变更门禁。

**验收标准**：每个 trust boundary 至少有 threat、control、test/evidence、owner、residual risk；发布 gate 能检查文档版本与当前能力 manifest 一致。

### P0-2 将浏览器信任标签扩展为统一 provenance/taint 系统

**实施状态（2026-07-19，核心传播、Document 字段映射、会话摘要与旧版线格式闭环）**：浏览器、文档、MCP 和工具输出已统一使用签名 `ContentEnvelope`；所有 `depends_on` 父 envelope 由服务端注入 runtime，下一跳工具输出合并全部 upstream taint/source lineage，side-effect sink 在父链缺失、签名/hash/scope 错配时 fail closed。RFC 6901 字段派生关系已进入 envelope 与 HMAC，并复核直接父内容/哈希；九个模型驱动 Document 调用通过一次性私有 provenance 产生显式映射。版本 sidecar 使用旧版已有字段，旧 `extra="forbid"` 二进制可读取和验证；旧版改写/合并后新版会保守丢弃陈旧映射。持久会话摘要也已用根级 `summarize/merge` lineage 绑定文本、session、anchor、source message IDs 与真实输入，legacy 仅形成 tainted root；应用写路径使用 CAS/reload-merge 并递归隔离私有 sidecar。残余风险是 legacy 历史正文不可恢复、同时剥离 envelope/sidecar/version 时需外部单调迁移门才能识别，以及真实跨进程恢复路径的持续回归。

统一 envelope 至少包含：

```text
source_kind, source_id, origin, content_hash, field_lineage,
trust_level, taint_flags, observed_at,
task_scope, user_confirmed, sanitizers_applied
```

执行策略应遵循：

- 非可信内容可用于提取事实，但不能直接扩大目标、权限、收件人、目标路径或工具集合。
- tainted 数据流入写操作、外发、凭据域、MCP 跨服务器调用时重新审批。
- 工具 A 的输出传给工具 B 时保留 provenance，不能因经过模型重写而“洗白”。
- 对文档、网页、OCR 和 MCP 分别建立 adversarial corpus。

### P0-3 重构长期记忆为“隔离、检疫、晋升”模型

**实施状态（2026-07-11，数据边界核心闭环）**：自动 lesson 默认关闭；非用户内容进入 quarantine；召回前验证 HMAC、正文 hash、用户确认、trust 与 scope；读写 API 强制 `principal_id/workspace_id/domain_scope` namespace，并具有 `kind/version/supersedes/conflict_status`。数据库通过事务校验、active-successor guard 与迁移触发器阻止同一父记忆出现多个可召回 successor，历史分叉 fail closed 为 `conflicting`。Retention 读取权威 namespace/quarantine 状态，冲突、隔离、撤销、未确认和 superseded 记录进入有限复核窗口；隐私擦除覆盖正文、namespace、quarantine、active-successor、索引和 lineage，并执行 secure delete/WAL truncate/VACUUM。

剩余建议：

- 在桌面端提供 quarantine/conflict 来源、期限、promote/revoke、版本树和“本任务不学习”UI。
- 对 active 用户确认记忆和敏感 task/chat/plan/tool 正文增加应用层加密或 SQLCipher 分层方案。
- 将字段级 provenance 与记忆派生关系结合，避免摘要或结构化提取丢失来源定位。
- 保持跨 principal/workspace/domain、并发 successor、擦除和 retention 的数据库级回归。

**发布口径**：Memory 控制面达到中上；检疫 UI 与 active 数据加密未完成，不应宣称企业级零知识或多租户数据库隔离。

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

**实施状态（2026-07-11，核心闭环）**：普通核心 task 与 automation 均由服务端签发短期 intent capsule，绑定 task、用户目标 digest、计划 revision、允许工具、资源/外发 scope、policy、expiry 与 nonce；结构性 plan mutation 会递增 revision 并原子失效旧审批。审批同时绑定 provenance、风险分级 TTL 和桌面/移动认证上下文，最终消费在数据库写锁内复核当前身份状态。残余项是无持久化 Plan 的低层 harness ledger，以及“修改计划/缩小范围/追加预算”的产品 UI。

当前 capsule 形状为：

```text
task_id, user_goal_digest, plan_revision,
allowed_tools, resource_scope, data_egress_scope,
policy_version, expires_at, nonce
```

每次高影响工具调用都验证 capsule；目标漂移、计划重写、来源信任变化或 scope 扩大时使旧审批失效。对云 API、MCP 和移动授权逐步采用短期、task-scoped、audience-bound token。

### P1-2 完成敏感数据加密与保留周期

**实施状态（2026-07-11，retention/erasure 闭环、加密部分闭环）**：task recording 已使用 DPAPI-wrapped AES-GCM；task 与 memory 采用分级 retention，孤儿敏感记录会清理，原子擦除覆盖全部相关表并执行 `secure_delete`、WAL truncate 与 `VACUUM`。SQLite 中 task/chat/memory/plan/tool/approval 正文仍主要依赖 Windows ACL，因此应用层 at-rest encryption 仍是主要残余风险。

下一阶段建议：

- 优先加密 task recording、移动设备材料、诊断草稿和高敏感正文；密钥由 DPAPI 包装。
- 评估 SQLCipher 或“可搜索索引 + 加密原文分离”，避免直接破坏 FTS。
- 为 task、chat、memory、index、recording、logs、diagnostics 定义默认 TTL 和容量上限。
- 把日志自动清理、完整数据导出和 retention review 纳入发布证据。
- UI 在发送云端前显示 provider、数据类型和是否含文件正文，而不是只显示抽象模式名。

### P1-3 将供应链从“锁定”提升到“可证明构建”

**实施状态（2026-07-11，候选门禁代码闭环）**：hosted RC workflow 生成 CycloneDX SBOM 和 SHA-256 subject manifest，并分别签发 SLSA provenance 与 CycloneDX SBOM attestation。Publish 在 materialize 前复核路径 allowlist、digest、candidate commit、signer workflow，并用本地 bundle 对每个 artifact 分别验证两个 predicate；下载的 SBOM 还会与签名 predicate 做结构化一致性比较，并作为带 checksum 的 Release asset 发布。残余项是真实 GitHub RC run 的在线证据、可复现构建和 Prompt/tool/policy/runtime capability attestation。

下一阶段建议：

- 在真实 GitHub RC/publish run 保存 provenance/SBOM 双验签输出、candidate commit、workflow/run identity 和 reviewer 接受。
- 将 Android production APK 与同一 candidate identity、manifest 和 attestation 绑定。
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

**实施状态（2026-07-11，通用桥 fail-closed 闭环）**：共享 `apiRequestAllowlist.ts` 已按 `{method, route template}` 显式登记通用 renderer 可访问接口；Electron main 的 `validateApiEndpoint` 和 dev:web renderer transport 共用同一策略，未知路由、方法错配、跨 origin、编码分隔符、点路径、解码后空白/控制字符均默认拒绝。副作用、高敏感读取和桌面原生动作通过独立 typed IPC 调用 `proxyExplicitDesktopBridgeRequest`，通用 `apiRequest` 无法传入内部 allowlist bypass 选项。跨栈契约测试会把 allowlist 与真实 FastAPI route shape 对照，拒绝陈旧条目、重复条目、未经显式审查的新通用写方法，以及动态模板捕获未来静态路由的碰撞。核心验收已闭环。

修复前 `desktop/src/main/ipcBackendHandlers.ts:48` 暴露通用 `apiRequest` IPC，并允许所有 `/api/*` 路径后再用 denylist 排除已知敏感前缀；新增写操作或高影响路由可能默认穿过通用桥。该风险现已由上述 allowlist、专用 IPC 和跨栈漂移测试替代。

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

**实施状态（2026-07-19，代码闭环并完成本机 ASAR 动态验证）**：[Electron 安全清单](https://www.electronjs.org/docs/latest/tutorial/security)建议避免 `file://`，[protocol API](https://www.electronjs.org/docs/latest/api/protocol)要求 privilege 在 ready 前、handler 在 ready 后注册。项目现在只为 `app` 开启 `standard`、`secure` 与 `supportFetchAPI`，没有开启 `bypassCSP`、Service Worker、`corsEnabled`、stream、code cache 或 extension 权限；打包窗口改为 `loadURL("app://local/index.html")`，导航与 IPC sender 仅信任精确入口，所有 `file://` 均拒绝。

handler 仅支持 GET/HEAD，并同时检查原始 scheme/authority/path 与 WHATWG URL；路径段拒绝空段、`.`/`..`、残留/双重编码、编码分隔符、控制字符、盘符/ADS、尾随点空格及 Windows 保留设备名（含 `COM¹/²/³`、`LPT¹/²/³`）。候选路径与真实路径都必须在 canonical renderer root 内且为普通文件，因此 directory junction/symlink 也不能越界；只允许已知 JS/CSS/图片/字体类型，HTML 仅根 `index.html`，不存在 SPA fallback。响应强制 MIME、CSP、COOP、CORP 与 `nosniff`，query/fragment 不参与文件定位。

验收包括 privilege/handler、raw URL、编码遍历、Windows 设备名、junction、GET/HEAD、MIME/CSP、导航与 IPC 信任聚焦 `33 passed`，最新 Desktop 全量 `93 files / 416 tests passed`，typecheck、production renderer/electron build 和 IPC security smoke。另以 Electron 42.3.3 启动未打包隐藏窗口和本机未签名 `win-unpacked/resources/app.asar`：入口、preload bridge、JS/CSS、严格响应头均正常，双重编码遍历为 404，query/fragment 保留，`Origin: app://local` 可通过 CORS 访问 loopback health。该本地未签名包不是 clean candidate；仍需在受保护 RC workflow 生成的签名候选上复测其他来源不能读取 `app://local`、WSS、更新后启动、导航/IPC frame 及无 CSP/MIME/mixed-content 控制台错误。

### P2-2 标准化 Agent telemetry

现有自研 trace/span 和 Prometheus 足以支持本地诊断。企业化时可增加 OpenTelemetry/GenAI 语义映射，重点输出低敏感指标：goal/run id、tool、policy verdict、latency、token、retry、approval、rollback、taint propagation 和 memory promotion。默认不采集正文、原始参数、截图或凭据。

### P2-3 内置不可信浏览器默认拒绝下载

**实施状态（2026-07-19，下载拒绝与事件脱敏闭环）**：隔离 BrowserHost session 已注册 `will-download` handler，并同时调用 `preventDefault()` 与 `cancel()`；浏览网页不能隐式升级为文件写入。阻止事件会删除 URL userinfo，对全部 query value 和 fragment 统一脱敏；取消后 `getURL()` 已失效时记录空 URL，不回退到未脱敏的会话地址。后续若引入显式下载 broker，应展示来源、文件名、类型、大小和落盘位置，并复用路径策略、恶意文件扫描和用户批准。

## 6. 分阶段路线图

### 发布前 / 0-2 周

1. 用最新分层 gate 重新运行真实 LLM 评测并修复质量失败；达标前保持新增 R2/R3 工具与默认自治深度冻结。
2. 执行真实 GitHub RC 双 attestation/publish 验证并保存 candidate-bound 证据。
3. 完成现有 RR-P0 clean-machine、物理 Android、diagnostics 人工复核、第三方复测和 owner sign-off。
4. 保持 unsafe local Skill、任意生成代码执行在 release profile 中关闭，直到 Windows OS sandbox broker 有独立证据。
5. 对已接入的 Document 与 conversation/session summary lineage 做候选升级/降级演练，并在 hostile SQLite 纳入正式威胁模型时增加外部单调迁移标记；继续推进 Memory 检疫/冲突 UI 和 active 数据加密方案。
6. 完成 NVDA、高对比度、200%/400% zoom 与文本缩放候选签收。

### 首个稳定版 / 2-6 周

1. 实现 Windows sandbox broker 原型，至少覆盖 Python/PowerShell Skill。
2. 为 task/chat/memory/plan/tool/approval 敏感正文落地应用层加密或 SQLCipher 分层方案。
3. 增加 Agentic red-team live fixture、human-calibrated grader 和 CI differential eval。
4. 为移动高风险批准增加 Android Keystore challenge signature/DPoP。
5. 增加外部 receipt/reconciliation、非文件 rollback verifier 和 crash injection E2E。
6. 补齐 MCP lifecycle/conformance，或采用官方 SDK。

### 企业化 / 6-12 周

1. 可复现 Windows 构建、Prompt/tool/policy/model capability manifest 与运行时 attestation。
2. per-device asymmetric mobile identity、短 token 和 step-up auth。
3. 审计外部锚定、SIEM/OTel 可选出口和安全事件演练。
4. 统一 kill switch、分阶段 canary 和自动回退。
5. 周期性第三方渗透测试复测，并将残余风险纳入采购与安全白皮书。

## 7. 不建议采用的做法

- 不要把“系统 Prompt 写了不要被注入”当作控制。
- 不要用一次用户同意覆盖后续所有工具调用或跨任务权限。
- 不要让签名 Skill 绕过运行时 least privilege；签名只证明来源，不证明安全。
- 不要将 MCP 上游 token 原样透传给下游服务。
- 不要把 task completed 等同于 result verified。
- 不要在没有 OS sandbox 的情况下把本地脚本执行包装成“安全沙盒”。
- 不要仅为获得 checkpoint/replay 语义就引入额外的分布式 workflow 集群；当前 SQLite 状态机、execution journal 和候选证据链应先用真实规模证明不足。
- 不要把本地 HMAC 链描述为能抵御同用户完全控制。
- 不要在诊断、遥测或支持包中默认收集 Prompt、tool args、截图和本机路径。

## 8. 调查方法与限制

本次调查：

- 阅读当前仓库 README、安全白皮书、合规清单、发布 dashboard、agentic eval、供应链和可维护性文档。
- 静态检查后端策略、审批绑定、记忆、Skill、网络、移动配对、Electron main/preload/IPC、更新和存储实现。
- 对照下列官方框架和规范进行差距映射。

初始调查以静态审阅为主；修复后已补跑 backend、Desktop/Mobile 聚焦门禁、130 项真实 LLM、Android API 35 模拟器 connected instrumentation 和第一方动态安全复测。以下仍未闭环：

- 真实 GitHub RC/publish run 的双 attestation 在线证据与 reviewer 签收。
- Windows clean-machine、Android 物理真机 LAN/WSS、MITM/弱网和附件 hash manifest。
- 独立第三方渗透测试、专项 fuzz 和恶意 Skill/MCP 实战复测。
- 法律、隐私影响评估或认证审计。

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
14. [SLSA v1.2 Build Track](https://slsa.dev/spec/v1.2/build-track-basics)
15. [CISA Secure by Design](https://www.cisa.gov/securebydesign)
16. [Android App Signing](https://developer.android.com/studio/publish/app-signing)
17. [Android Biometric Authentication](https://developer.android.com/identity/sign-in/biometric-auth)
18. [Anthropic: Building effective agents](https://www.anthropic.com/research/building-effective-agents)
19. [OpenAI: Computer use](https://platform.openai.com/docs/guides/tools-computer-use)
20. [OpenAI: Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)
21. [OpenAI: Build an Agent Improvement Loop with Traces, Evals, and Codex](https://developers.openai.com/cookbook/examples/agents_sdk/agent_improvement_loop)
22. [LangGraph: Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
23. [LangGraph: Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
24. [Microsoft UI Automation Overview](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/ui-automation-overview)
25. [Microsoft Power Automate: UI elements](https://learn.microsoft.com/en-us/power-automate/desktop-flows/ui-elements)
26. [Microsoft Power Automate: Build a custom selector](https://learn.microsoft.com/en-us/power-automate/desktop-flows/build-custom-selectors)
27. [Microsoft Research: Guidelines for Human-AI Interaction](https://www.microsoft.com/en-us/research/publication/guidelines-for-human-ai-interaction/)
28. [W3C Web Content Accessibility Guidelines 2.2](https://www.w3.org/TR/WCAG22/)
29. [OpenTelemetry GenAI Semantic Conventions](https://github.com/open-telemetry/semantic-conventions-genai)
30. [OpenAI: Safety in building agents](https://platform.openai.com/docs/guides/agent-builder-safety)
31. [OpenAI Agents SDK: Human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)
32. [OpenAI Agents SDK: Tracing](https://openai.github.io/openai-agents-python/tracing/)
33. [Anthropic: Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
34. [Microsoft Durable Functions types and features](https://learn.microsoft.com/en-us/azure/azure-functions/durable/durable-functions-types-features-overview)
35. [Microsoft Foundry Agent Service: Memory](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/what-is-memory)
36. [MCP 2025-11-25 Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
37. [MCP 2025-11-25 Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
38. [MCP 2025-11-25 Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
39. [GitHub Artifact Attestations](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations/using-artifact-attestations-to-establish-provenance-for-builds)
40. [WAI-ARIA Modal Dialog Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/)
41. [Windows Accessibility Overview](https://learn.microsoft.com/en-us/windows/apps/design/accessibility/accessibility-overview)
42. [Google SRE Workbook: Canarying Releases](https://sre.google/workbook/canarying-releases/)
43. [AWS Builders' Library: Making retries safe with idempotent APIs](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/)
44. [Microsoft WindowsAgentArena](https://github.com/microsoft/WindowsAgentArena)
45. [OSWorld](https://github.com/xlang-ai/OSWorld)
46. [OWASP State of Agentic AI Security and Governance 2.01](https://genai.owasp.org/resource/state-of-agentic-ai-security-and-governance/)
47. [Anthropic: Computer use](https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/computer-use-tool)
48. [OpenTelemetry GenAI Agent Spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md)
49. [AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents](https://arxiv.org/abs/2406.13352)
50. [BrowserGym: A Gym Ecosystem for Web Agent Research](https://arxiv.org/abs/2412.05467)
51. [tau-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains](https://arxiv.org/abs/2406.12045)
52. [Nature Human-AI Meta-analysis](https://doi.org/10.1038/s41562-024-02024-1)
53. [Microsoft Windows Accessibility Testing](https://learn.microsoft.com/en-us/windows/apps/design/accessibility/accessibility-testing)
54. [Microsoft Dialog Guidelines](https://learn.microsoft.com/en-us/windows/apps/design/controls/dialogs-and-flyouts/dialogs)
55. [OpenAI: Migrate to the Responses API](https://developers.openai.com/api/docs/guides/migrate-to-responses)
56. [Anthropic: Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
57. [Microsoft Agent Framework Workflows](https://learn.microsoft.com/en-us/agent-framework/workflows/)
58. [A2A Protocol Specification](https://a2a-protocol.org/latest/specification/)
59. [Windows 11 Security Book: Securing AI agents on Windows](https://learn.microsoft.com/en-us/windows/security/book/operating-system-agentic-security)
60. [Microsoft Windows Sandbox](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/)
61. [CaMeL: Defeating Prompt Injections by Design](https://arxiv.org/abs/2503.18813)
62. [Agent Security Bench (ASB), ICLR 2025](https://proceedings.iclr.cc/paper_files/paper/2025/hash/5750f91d8fb9d5c02bd8ad2c3b44456b-Abstract-Conference.html)

## 10. 修复后复查

### 10.1 更新后的总体判断

本轮修改显著提高了确定性控制覆盖率。正式 threat model、OWASP ASI01-ASI10 control map、多父 ContentEnvelope、Memory namespace/lineage/retention、release-profile execution isolation、renderer API allowlist、BrowserHost 下载拒绝、移动 refresh token family、TLS pin 生命周期、Android APK signer gate、分层真实评测和候选双 attestation 均已形成代码与测试证据。

更新后的判断是：**工程控制面已从“强 Beta”推进到“强 Beta+”，但仍不具备公开 RC/GA 签收条件。** 当前 `review:scorecard` 为 94/100，但 7 个 RR-P0 发布证据全部仍为 `in_progress`，RC gate 会正确 fail closed。

### 10.2 原建议闭环状态

| 原建议 | 复查状态 | 已完成 | 仍需处理 |
| --- | --- | --- | --- |
| 正式 threat model / ASI control map | 基本闭环 | 版本化文档、JSON control map、CI/release validator | candidate-bound 安全负责人接受仍是 RR-P0-007 |
| 统一 provenance / taint | 核心闭环，Document 字段映射、会话摘要与旧版兼容已补 | HMAC envelope、工具输出分类、审批后参数整体绑定、side-effect sink 错配拒绝；所有 `depends_on` 父链进入 runtime；九个模型驱动 Document 调用产生 RFC 6901 映射；会话摘要 root lineage 绑定文本/session/anchor/source IDs/输入，legacy 迁移为 tainted root；父内容/哈希校验、CAS、保守 root fallback、旧版可解析 sidecar 和 invalid embedded envelope fail closed | legacy 历史正文无法恢复；hostile SQLite 完整降级检测需外部单调迁移门；确定性转换按需继续使用 root lineage |
| Memory quarantine / TTL / promotion | 核心闭环 | 自动学习默认关闭、quarantine、TTL、promote/revoke；recall 校验 HMAC/hash/确认状态；principal/workspace/domain namespace、version/supersedes/conflict、单 active successor 数据库约束 | 桌面端无检疫/冲突 UI；active 用户确认正文仍未应用层加密 |
| Intent capsule 与自治预算 | 基本闭环 | automation 与普通核心 task 均使用服务端 capsule/budget；结构性 plan mutation 递增 revision；审批绑定 goal digest/revision/provenance | 无持久化 plan 的低层测试/工具 harness 不自动建 ledger；预算追加 capability UI 尚未实现 |
| Windows OS sandbox | 仅风险缓解 | 打包/release profile 无 attestation 时禁止危险执行 | 仓库仍无 AppContainer/受限令牌/Job Object/network broker host |
| Renderer API bridge allowlist | 闭环 | method + route allowlist、未知路径默认拒绝、编码及解码字符防护；CI 对照真实 FastAPI route shape、通用写路由清单和动态模板/静态路由碰撞 | 新增通用桥条目仍需安全 owner 做代码审查，不应以扩大 allowlist 代替专用 typed IPC |
| BrowserHost 下载拒绝 | 闭环 | `will-download` 同时 `preventDefault()` 与 `cancel()`；阻止事件隐藏 userinfo、全部 query value 和 fragment，取消后无 URL 时不回退明文会话地址 | 若引入显式 broker，仍需独立授权、扫描与路径/落盘策略 |
| 移动短 token / refresh family | 核心撤销闭环，PoP 仍开放 | 30 分钟 access token、family generation、旋转 refresh、复用检测、family/device revoke；旧 access 与未消费审批在 refresh 后立即失效；前后台锁定清内存并强制生物保护存储读取/refresh；R3/执行/权限/凭据审批在可信 PoP 缺失时 fail closed | 无 Android Keystore challenge signature/DPoP；refresh token 仍可脱离设备重放；物理锁屏/生物认证证据待签收 |
| TLS pin 生命周期 | 基本闭环 | 单 active/next、24 小时重叠、30 天期限、定向撤销、损坏状态 fail closed；system/self-signed/复用路径统一 exact origin/port 与证书有效期，IDN/IPv6 采用同一 canonicalizer | Android Keystore/DPoP 与物理真机、真实候选附件仍未完成 |
| Android APK 发布身份 | 代码闭环 | `apksigner --verbose --print-certs`、v2/v3、单 signer、证书 digest、candidate binding；reviewed evidence 强制 SHA-256 附件 manifest 与真实 HMAC key fingerprint | 真实候选 signer、APK、物理真机和经审核附件尚未形成同一候选证据 |
| 敏感数据加密与 retention | retention/erase 核心闭环 | task recording 使用 DPAPI-wrapped AES-GCM；分级 task/memory retention、权威 conflict/quarantine 状态、孤儿敏感记录清理、原子擦除、secure_delete/WAL truncate/VACUUM | tasks/chat/memory/plan/tool/approval 正文仍主要依赖 ACL 明文存储 |
| 候选供应链证明 | 代码闭环 | 候选 SHA-256 manifest、CycloneDX SBOM、SLSA provenance 与 SBOM attestation；publish 逐 artifact 验证本地双 bundle、predicate、signer workflow、source digest，并核对签名 SBOM 后发布 | 尚未取得真实 GitHub RC run 在线验签证据；可复现构建和 runtime capability attestation 未完成 |
| 真实 LLM / clean-machine / 真机 / 外部测试 | 评测系统闭环、发布证据未闭环 | Android API 35 模拟器 connected instrumentation 6/6；真实 provider 全量 130 项已运行；五层 scorecard、稳定失败分类/error code/脱敏 diagnostic、缺证据纳入分母、任何评测失败 fail closed；第一方动态安全复测 14/14 | 真实 LLM quality gate 未通过；仍缺物理真机 reviewed evidence、clean-machine reviewed evidence、第三方渗透复测和 owner sign-off |

### 10.3 当前高价值发现

#### P1-1 Provenance sink 参数错配与已知上游缺失已修复

`ToolRuntime` 现在对需要 revalidation 的 side-effect tool 计算去除运行时控制字段和 provenance 元数据后的完整 canonical args payload，并要求用户确认 envelope 的 `content_hash` 与该 payload 完全一致。良性 envelope 配不同 payload 会 fail closed；无效 HMAC 不能在 revalidation 时被升级。

依赖步骤的全部 `ToolResult.content_envelope` 由服务端注入下一步 runtime，而不再依赖模型复制 envelope；审批记录固化完整上游 provenance，工具输出继续合并 runtime 中的全部 upstream envelope。暂停、审批继续或进程重启时，scheduler 与 OS execution state 会从 `tool_calls/tool_results` 重建 observations，并兼容旧 observation 日志与 recovery alias；最新失败、缺失或未完成执行阻断旧成功回退。`A+B -> write`、多跳传播、良恶来源混合和 `resume_task -> TaskPool -> 新 orchestrator` 均有回归。2026-07-19 已补齐 JSON Pointer 字段级 lineage、九个模型驱动 Document 调用、显式父内容/哈希复核、保守 root fallback 与旧版可解析 sidecar；旧版改写后只会安全丢弃陈旧映射。conversation/session summary 也已接入认证 root lineage、message-ID segment、legacy tainted root 与并发 CAS；剩余的是不可恢复的旧历史正文和 hostile SQLite 完整降级识别，而不是应用层摘要写入仍无 lineage。

#### P1-2 核心 OS Agent capsule、revision 与 budget 已接入

有持久化 Plan 的普通核心 task 现在由 guard 服务端按当前步骤签发短期 capsule，只允许当前 tool、解析出的资源和外发目标，并创建 task 级 budget ledger。客户端不能自行扩大该 scope。

恢复步骤、subagent tool proposal 和 reflection add/replace 等结构性 mutation 会递增 `Plan.version`，并原子过期未消费审批。审批 engineering boundary 新增 task、goal digest、真实 plan revision 与 provenance；执行时 revision 或 goal 不一致会拒绝。

残余风险是无持久化 Plan 的低层 harness 仍不自动创建 ledger，且用户追加预算尚无独立短期 capability 与产品 UI。

#### P1-3 Memory recall 完整性校验已修复

Recall 现在在进入 embedding 排序和 planner prompt 前验证 envelope 存在、HMAC、`content_hash == stable_content_hash(memory.content)`、memory/envelope 用户确认状态、trust level 和 scope。任一失败会把记录改为 `quarantined`、撤销 `user_confirmed` 并记录 `memory.recall_integrity_failed`。

新建无 task_id 的用户记忆会绑定 memory id 作为 envelope scope；损坏 lineage 在用户显式 promote 时重建为新的 user-revalidated envelope。Recall 和所有读写 API 现按 principal/workspace/domain namespace 隔离，并携带 version/supersedes/conflict 状态；数据库 active-successor guard 阻止静默分叉，历史重复分支进入 `conflicting`。Retention 读取权威 namespace/quarantine 状态，隐私擦除覆盖正文、namespace、quarantine、active successor、索引和 lineage。残余项是桌面 quarantine/conflict UI 与 active 正文应用层加密。

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
- BrowserHost 阻止下载事件的 userinfo、全部 query value 和 fragment 已在 2026-07-19 统一脱敏；若未来增加显式 broker，仍需独立的授权、扫描和落盘策略。
- 应用层加密目前主要覆盖 task recording；SQLite 中 task、chat、memory、plan、tool 和 approval 正文仍依赖 ACL 与 retention。
- Android reviewed evidence 已强制 `sha256-manifest/v1` 绑定截图、视频、后端/移动日志和 `adb` 安装状态的脱敏标签、SHA-256 与大小；`reviewed-evidence-ed25519/v3` 将完整公钥 fingerprint 纳入签名载荷，并由验证侧公钥校验。残余项是真实候选 APK、受控 signer、物理真机及其被审核的实际附件。
- Approval TTL 已接入模型、列表、桌面原生确认、移动决策、执行绑定和原子消费，并按风险分级为 R0/R1 15 分钟、R2 10 分钟、R3 5 分钟、R4 1 分钟。2026-07-19 Desktop generation 已覆盖成功单实例启动、隐藏登录启动、锁屏/解锁、挂起/恢复、托盘/长后台与前台恢复；Mobile access/refresh/approval/claim 绑定 family generation，离开 active 后必须重新读取生物保护存储并 refresh。语义 UIAutomation 还会在 claim 前与最终动作边界复核所属顶层窗口的 HMAC 身份。残余项是不暴露 accessibility 的应用内部账号、coordinate/hotkey fallback、物理移动锁屏证据，以及已线性化在途副作用的 stop/quiescence。
- 回滚真实性已在本轮修复：API 持久化 `succeeded/partial/manual_required/failed/unrecoverable` 汇总；移动、回收、空目录删除和备份恢复会重读文件系统后态，移动/恢复还使用 SHA-256 校验内容，只有核验通过才计为成功。后端新增真实 `rolled_back/repair_required` 终态，完整核验成功进入前者，其余结果进入后者；旧 `failed + metadata.rollback` 记录读取时确定性迁移。残余项是非文件型补偿器的专用 verifier。
- 当前 MCP 实现是覆盖 `tools/list`、`tools/call` 和 basic `resources/list` 的 HTTP JSON-RPC compatibility adapter，尚未实现标准 lifecycle、capability/session 协商、Streamable HTTP、progress/cancel 或 SSE resume。
- RC/publish workflow 已生成并验证 SLSA provenance 与 CycloneDX SBOM attestation；残余项是真实 GitHub RC run 在线证据、可复现构建和 Prompt/tool/policy/runtime capability attestation。

### 10.5 本轮验证

- 成熟度收口合并回归：后端 `229 passed`，覆盖真实 LLM harness、候选发布工作流、隐私擦除、Memory namespace/lineage/retention、恢复 provenance、Tool journal、Task resume、ToolRuntime 与 threat-model control map；成熟度矩阵文档契约 `2 passed`。相关 Ruff 全通过，修改的三个 release PowerShell step 均通过 AST 解析。
- 最终完整 backend pytest：`3292 passed, 12 skipped`；跳过项均为当前 Windows 会话缺 symlink 权限、Unix 权限模型或 Windows 上由 DPAPI 覆盖的预期平台分支，无剩余失败。
- 本轮移动安全后端回归：125 tests passed；Mobile typecheck、token 与 remote-input smoke passed。
- Electron Vitest：66 files / 264 tests passed；typecheck、完整 Desktop smoke（17 个顺序门禁）与 `smoke:ipc` passed；390x844 系统诊断和任务驾驶舱审批 reflow，以及审批/解释/回滚、首次同意、完整协议、fail-closed 同意错误和活动中心的初始焦点、双向 Tab 回环、Escape、关闭锁定与返焦均已纳入行为 smoke。`@axe-core/playwright 4.12.1` 已进入同一 browser smoke，桌面/390px 首页、任务进度、关键 dialog/alertdialog、活动中心和设置/隐私页当前 serious/critical 为 0。
- Desktop dependency audit contract：8 tests passed。
- Mobile typecheck、token/session/remote-input/TLS smoke passed。
- 回滚后态核验专项：39 passed；包含 OS/文件 API 返回但资源未变化、恢复内容不匹配和备份保留等故障注入。相关 task replay/explain 扩大回归累计 62 passed；Desktop typecheck 与 rollback mapper 7 tests passed。
- 回滚显式后端终态专项与扩大回归：后端 389 passed；覆盖 completed/failed 到 rolled_back/repair_required 的严格转换、partial/manual 状态、旧 failed rollback 记录迁移、run 不复活、移动任务终态、retention、metrics、diagnostics 和 tool journal。Desktop 64 files / 255 tests passed且 typecheck 通过。
- Stop 传播专项与扩大回归：后端 284 passed、1 个 Windows symlink 权限相关 skipped；真实子进程及其 descendant 在取消后归零，UIAutomation 取消延迟断言低于 500ms。Desktop 64 files / 255 tests passed，typecheck 通过；BrowserHost 仅关闭精确 `task_id` 匹配会话。
- Approval 风险分级 TTL 专项与扩大回归：后端 219 passed；Desktop native confirmation/mapper 10 tests passed且 typecheck 通过；Mobile typecheck 与 approval status smoke passed。
- Approval 认证上下文专项与跨消费路径扩大回归：后端 415 passed；覆盖桌面确认密钥轮换与重新绑定、移动 device/family/credential revoke、token epoch 变化、family 过期、缺失/畸形上下文和正式环境旧记录 fail closed。Desktop 64 files / 255 tests passed，桌面与移动 typecheck 通过，Mobile approval status smoke passed；本批次未重新运行完整 backend 全套。
- UIAutomation 唯一目标与跨审批指纹专项/扩大回归：后端 183 passed；覆盖 find 首项旁路、element object 旁路、重复候选、审批期间 runtime id 替换、disabled/offscreen、5000 节点遍历截断、直接 API 原子失效和通用 approval resource-state 恢复路径。修改文件 Ruff 全通过；本批次未运行 Windows 真机 DPI/多显示器 E2E。
- `security:threat-model`、`review:scorecard --allow-dirty`、非严格 `release:readiness` passed。
- `release:readiness:rc` 按预期失败：7 个 RR-P0 均为 `in_progress`，CI evidence、clean worktree、manual sign-off 和 owner signature 未完成。

### 10.6 未完成验证的实际执行结果（2026-07-11）

#### Android connected instrumentation：模拟器通过，物理真机仍未签收

- 在 `mavris_api35`、Android API 35 模拟器上构建并安装 debug APK 与 androidTest APK，连接隔离的真实 LAN `HTTPS/WSS` full backend。
- connected instrumentation 最终结果为 `OK (6 tests)`，覆盖 wrong pin 拒绝、正确 pin 后 HTTPS health、pair confirm、approval WSS、pin overlap/promotion、expiry、legacy/corrupt store fail closed 和定向撤销。
- 执行中发现并修复两项真实问题：instrumentation 未携带后端新增的一次性 `claim_secret`；pin 撤销后已有 OkHttp 缓存/连接仍可能返回成功响应。修复后撤销/过期状态在应用级拦截器进入缓存或网络前即 fail closed。
- 证据位于 `.tmp/qa-evidence/android-connected-20260711/adb-instrumentation-final.log`。该结果是 emulator supporting evidence，不等同于物理真机的相机 QR、远程屏幕、远程输入、截图/日志审阅和附件 hash manifest 签收。

#### 真实 LLM provider：全量执行，quality gate 未通过

- 默认效率模式预检拒绝当前 private/loopback base URL，未向该地址发送云端评测请求；这是 SSRF fail-closed 的预期行为。
- 隐私/本地轨道先完成 1 项真实 golden task，随后执行完整 `130` 项 quality gate（25 golden + 105 versioned benchmark），全部任务均产生记录且无 harness error。
- 结果未达到发布阈值：task success `0.3333`、intent accuracy `0.5758`、tool overlap `0.5758`、risk match `0.8621`；39 个 adversarial case 中 36 个未通过安全断言。结构化失败率、未知工具率和缺参率均为 `0`，plan schema valid rate 为 `1.0`。
- 报告位于 `.tmp/qa-evidence/real-llm-eval/real-llm-eval-report.json`。结论是“真实评测已执行但质量门禁失败”，不能作为 RC/GA 结果质量签收。
- 同时修复了 Windows 下 TestClient/SQLite 句柄导致评测报告被临时目录清理异常遮蔽的问题；最新 harness 契约回归 `44 passed`，并新增评测失败零容忍、缺失计划证据计入分母、provider/runtime 分层和 chat timeout 归因测试。上述 130 项真实结果生成于该门禁收紧前，仍作为负向 stop-ship 证据；需要用 v2 scorecard 重新运行，不能沿用旧报告宣称通过。

#### Clean-machine 安装：验证器 fail closed，缺独立机器证据

- 执行 `python scripts/verify_clean_machine_evidence.py`，结果为 `ok=false`：缺少 `build/clean-machine-release-evidence-reviewed.json`。
- 当前主机是开发工作区，不能用本机已有依赖、缓存或模板替代 clean-machine 安装/启动/首个只读任务/诊断导出/卸载回滚证据。
- 证据位于 `.tmp/qa-evidence/clean-machine-verify-20260711.log`。该 RR-P0 仍需在独立 Windows 机器或受控全新 VM 上采集并由 reviewer 签收。

#### 动态安全复测：第一方 14/14，第三方复测未完成

- 对隔离 LAN HTTPS backend 执行第一方动态探测，覆盖匿名 health 最小披露、desktop token 缺失/错误/正确路径、可信与恶意 CORS preflight、malformed JSON、pairing 注入形状、8 次失败后的爆破限流、chat 请求体上限和未认证 mobile WSS。
- 首轮发现所有 HTTP 响应缺少 `X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy`；加入最外层 ASGI security-response middleware 后复测为 `14/14 passed`，相关后端回归 `34 passed`。
- 结果位于 `.tmp/qa-evidence/dynamic-security-retest-20260711.json`。OWASP ZAP Docker 镜像在时限内未完成拉取/登记，未生成 ZAP 报告；本轮也没有独立第三方团队参与，因此不能宣称完成第三方 penetration test/retest。

综上，四类验证均已实际触发并留下正向或负向证据，但仅 Android 模拟器 supporting evidence 与第一方动态复测通过。真实 LLM quality gate、clean-machine reviewed evidence、物理真机 reviewed evidence和第三方复测仍阻止公开 RC/GA 签收。

## 11. 广义行业工程最佳实践补充

### 11.1 更新后的产品级判断

第 1-10 节证明 Lengrvis 的安全控制面已经达到强 Beta+。把调查范围扩展到代理架构、崩溃恢复、桌面自动化、Human-AI UX、可访问性和评测工程后，结论需要增加六个约束：

1. **副作用必须具备崩溃一致性**：暂停、恢复和重试本身不等于可靠执行；系统必须能区分“尚未执行”“已执行”“结果未知”，避免进程在工具返回前崩溃后重复删除、重复发送或重复修改。
2. **桌面目标必须唯一且可重验**：语义 UIAutomation 优于纯坐标是正确方向，但任何模糊 selector 都必须先消歧，不能选择深度优先遍历遇到的第一个匹配元素。
3. **多 Agent 复杂度必须由 eval 证明**：行业成熟做法是从可组合 workflow 开始，只有在任务级评测证明 routing、worker 或 evaluator 带来稳定收益时才增加自治和 Agent 数量。
4. **回滚状态必须反映真实结果**：尝试过 rollback 不等于成功回滚；部分失败、人工恢复和永久不可恢复必须是不同产品状态。
5. **用户控制必须在主流程可见**：后端具备 pause/resume/cancel 还不够，运行中的桌面任务必须始终提供 Stop、Pause、Resume、Cancel、修改目标和缩小授权范围。
6. **协议与供应链声明必须精确**：JSON-RPC 兼容适配器不能等同于完整 MCP 客户端，普通 CI SBOM 也不能等同于与候选产物绑定的签名 provenance。

因此，**通用工具 journal/outcome-unknown、真实回滚终态、主流程 Stop/Pause/Resume/Cancel、UI 唯一目标绑定、approval TTL/认证上下文、多父与模型驱动 Document 字段 provenance、会话摘要认证 root lineage、旧版可解析 lineage sidecar、Memory namespace/冲突治理、评测失败归因和候选双 attestation 已形成 fail-closed 控制。** 公开 GA 前仍需完成真实 LLM 质量修复、clean-machine/物理真机/第三方签收、NVDA/高对比度/缩放矩阵、MCP 扩展/OAuth conformance、真实 RC 在线验签、hostile SQLite 单调迁移门（若纳入威胁模型）、active 数据加密及可复现构建。在真实 LLM quality gate 达标前，不建议继续扩大默认自治范围或新增高影响工具。

### 11.2 扩展成熟度矩阵

| 领域 | 行业基准 | Lengrvis 当前证据 | 主要残余风险 | 成熟度 / 发布影响 |
| --- | --- | --- | --- | --- |
| Agent 拓扑 | 先用简单 workflow；routing、多 Agent、evaluator 必须由任务 eval 证明收益 | Planner、Supervisor、Orchestrator、安全审查及领域 Agent 分层；真实 provider benchmark 与分层 scorecard 可作为拓扑比较底座 | 尚无逐能力的单 Agent/workflow 基线与成本收益证明 | 中上 / P1 深化 |
| 持久化与恢复 | 生产状态持久化、明确 checkpoint、可暂停恢复、重试按错误类型控制 | Run state 写入 SQLite；孤儿 run 置为 PAUSED；工具 journal 无上限扫描 `executing`，原子恢复为 `committed/outcome_unknown`；未知结果阻止自动重放 | 外部系统 reconciliation probe 未普及 | 强 / P1 深化 |
| 工具副作用 | prepare/commit/reconcile，幂等或补偿，未知结果不得盲目重放 | execution key 绑定 task/goal/step/plan/tool/version/args/approval；SQLite 唯一索引和 CAS；结果落库后才 commit；通用及 direct UI/browser 写入口均进 journal | 外部 receipt/reconciliation 与 provider 瞬态错误白名单未完成 | 强 / P1 深化 |
| 回滚真实性 | rollback 逐项核验，full/partial/manual/unrecoverable 分态 | API/metadata/桌面分态；文件移动/删除/备份恢复重读后态并以 SHA-256 校验；后端使用真实 `rolled_back/repair_required` 终态 | 非文件型补偿器尚无统一 verifier | 强 / P1 深化 |
| Windows UI 自动化 | UIA 语义优先、层级 selector、多候选消歧、运行前重验，坐标仅作受控 fallback | 语义 click/type/focus 要求唯一候选；element object 不可绕过；runtime/process/rect、顶层窗口、可执行文件、进程实例及完整同进程 accessibility 父链以私有 HMAC 绑定 approval，在 claim 前和 Invoke/SetValue 前重建比较；身份不可证明时不签发 click/type 审批 | accessibility 不可见的应用内账号、受保护/跨进程 provider、DPI/多屏和 coordinate/hotkey 截图证据，以及系统调用后的非原子竞态仍开放 | 强 / P1 深化 |
| Human-AI 协作 | 风险点即时确认；始终可停止、纠正、缩小范围和接管 | 主桌面已接通 Stop/Pause/Resume/Cancel；可取消进程树、UIAutomation abort、browser task 清理和停止耗时审计已接入 | 修改计划、缩小范围、“正在停止”状态和候选 p95 分布未完成 | 中上 / P1 深化 |
| Agent 评测 | 任务特定、持续运行、轨迹与结果分层、人类校准、生产日志回灌 | 130 项真实 provider gate、版本化 benchmark、adversarial corpus、五层 scorecard、稳定 failure taxonomy、缺证据计入分母、任何评测失败阻断发布 | 当前真实质量仍失败；live OS、人工校准和生产回灌不足 | 中上（评测系统）/ 质量 stop-ship |
| 可访问性 | WCAG 2.2 AA、键盘、焦点、读屏、高对比度、缩放、减少动画 | 共享 dialog 键盘契约、focus-visible、reduced-motion、窄屏 reflow 与关键流程 axe serious/critical 自动门禁 | NVDA、Windows 高对比度和 200%/400% 缩放候选签收未完成 | 中上 / GA 证据待补 |
| Provenance 与记忆隔离 | 完整多父 lineage；主体/工作区/领域 namespace；冲突与版本治理 | 全部 `depends_on` 父链传播、多跳 taint 合并及暂停/重启 journal 重建；HMAC envelope、九个模型驱动 Document RFC 6901 映射、会话摘要认证 root lineage/legacy tainted root 与旧版可解析 sidecar；Memory quarantine/TTL/确认；principal/workspace/domain namespace；version/supersedes/conflict 与单 active successor 约束；权威 retention 和原子擦除 | legacy 历史正文不可恢复，完整数据库降级识别需单调迁移门；检疫/冲突 UI 和 active 正文加密未完成 | 中上 / P1 深化 |
| MCP 互操作 | initialize/capability/session/transport/progress/cancel 等标准 lifecycle | `tools/list`、`tools/call`、schema/SSRF 防护，第三方工具保持 R4，产品口径限定为 HTTP JSON-RPC compatibility adapter | 未达到完整 MCP client conformance | 中上（受限互操作）/ P1 深化 |
| 候选供应链 | SBOM 与每个候选 digest 绑定；hosted builder 签名 provenance/attestation | hash lock、CycloneDX SBOM、候选 SHA-256 manifest、SLSA 与 SBOM attestation；publish 双 bundle/predicate/signer/source 验证并发布签名 SBOM | 真实 RC 在线证据、可复现构建和 runtime capability attestation 未完成 | 中上 / RC 证据待跑 |
| Agent telemetry | run/agent/plan/tool 标准 span，跨服务关联，正文默认不采集 | 自研 trace/span、Prometheus、run event、审计链、低敏感指标和诊断脱敏 | 尚未形成 OTel GenAI/MCP 标准导出与完整跨组件 trace parentage | 中上 / P2 深化 |

### P0-6 建立事务化工具执行与“结果未知”对账

**实施状态（2026-07-11，崩溃一致性核心闭环）**：`ToolCall.execution_key` 现在使用 HMAC 确定性绑定 task、用户目标摘要、step、plan revision、tool/version、canonical args 和 approval；SQLite 对非空 execution key 建唯一索引，并通过原子 compare-and-set 从 `prepared` 领取到 `executing`，并发或恢复路径不能同时取得同一副作用执行权。副作用前持久化 `executing`，`ToolResult` 落库后才提交；相同已提交意图复用既有结果，`executing/outcome_unknown` 再进入时阻止自动重放。启动恢复按物理 `status` 索引查询全部 `executing` 记录，不再受全表前 5000 条限制，并在同一数据库事务内检查结果、原子转为 `committed` 或 `outcome_unknown`；JSON 状态漂移不能绕过物理 CAS，`committed` 丢失结果也会原子降级为 `outcome_unknown`。除通用 ToolRuntime、developer engine、Skill/MCP 执行路径外，原先直调的 UIAutomation 和 browser act/CUA 高风险 HTTP 入口现在也通过 direct-execution journal。`ToolDefinition` 进一步公开 `idempotency_scope`、是否支持 reconciliation、`compensation_strength` 和 `safe_to_retry_errors`；默认只声明本地 execution-key 去重、无外部对账、无补偿、无安全重试错误，不把未知 provider 行为误报为幂等。RecoveryHandler 对 R2/R3 只接受工具显式声明且与结构化 `error_code` 精确匹配的安全重试，契约缺失、无错误分类、结果未知或未列入清单时均在咨询 Agent 和生成新 step 前 fail closed。已覆盖旧库物理列回填、超过 5000 条记录、状态漂移、单行 reservation、同步/异步 direct 入口、结果复用、结果丢失、“副作用已发生、结果持久化失败”故障注入，以及高风险未分类错误拒绝/显式安全错误放行。核心 P0 验收已闭环；外部 receipt/reconciliation probe 和按具体 provider 验证的瞬态错误清单保留为 P1 深化项。

LangGraph 的 interrupt 文档明确说明恢复时节点会从头执行，因此 interrupt 之前的副作用应幂等，或放到独立节点/interrupt 之后。AWS Builders' Library 进一步指出：请求超时后，调用方无法判断副作用是否已发生，直接重试可能制造重复资源，必须依赖幂等 token 或 reconciliation。这个原则同样适用于 Lengrvis 的进程崩溃、超时取消和 approval resume。

修复前已有良好基础：

- `backend/app/services/run_service.py:347` 会在启动时识别 crash-orphaned RUNNING run 并转为 PAUSED。
- `backend/app/services/run_service.py:727` 在每个 engine turn 后持久化 `RunState`。
- `backend/app/orchestration/tool_runtime_lifecycle.py:57` 在执行前持久化 `ToolCall`，`backend/app/orchestration/tool_runtime.py:299` 在工具返回后持久化 `ToolResult`。
- 修复前的通用 `ToolCall` 只有随机 id 和 `status="created"`，没有与 task、step、plan revision、canonical args 和 approval 绑定的幂等执行键；该缺口现已由上面的 execution journal 闭环。

原始风险窗口是：副作用已经发生，但进程在 `ToolResult` 落库前退出。当前实现会将其恢复为 `outcome_unknown` 并阻止自动重放，而不是只根据 step 未成功而重试或误报为确定失败。

建议新增通用执行协议：

```text
execution_key = H(task_id, step_id, plan_revision,
                  tool_name, canonical_args, approval_id)

state = prepared | executing | committed | failed | outcome_unknown
```

- 在副作用前原子写入 `prepared`，固化 intent capsule、approval binding、资源前态和 execution key。
- 工具成功后写 `committed` 与外部 receipt、资源后态、rollback/compensation 信息。
- 同一 execution key 再次进入时，幂等工具返回既有结果；非幂等工具先调用 reconciliation probe，不能确认时进入 `outcome_unknown` 并暂停给用户。
- 每个写工具声明 `idempotency_scope`、`reconciliation_probe`、`compensation_strength`、`safe_to_retry_errors`；通用契约与保守默认值已实现，后续只在故障注入和 provider 文档证明安全后增加非空重试清单。
- 只对明确的瞬态错误自动重试；认证、参数、策略拒绝、资源状态变化和结果未知不得自动重试。
- 增加 fault-injection：在 call 落库后、系统调用后、result 落库前、step 状态更新前分别强杀进程，验证不会重复副作用。

**验收标准**：所有 R2/R3 工具在任一注入崩溃点后，要么确定性恢复到同一结果，要么停在 `outcome_unknown`；重复删除、重复发送、重复安装和重复权限修改均为 0。

### P0-7 将 UIAutomation 从“首个匹配”升级为“唯一目标绑定”

**实施状态（2026-07-11，唯一目标与跨审批指纹核心闭环）**：`find_element` 不再返回深度优先遍历遇到的首个重复项；selector inspection 返回 `match_count`、受限候选摘要、是否截断和唯一目标状态。`click`、`type_text` 和 `focus` 无论接收 selector 还是此前查到的 `UIAutomationElement`，都会重新枚举候选，只有唯一匹配才执行，element object 不能绕过消歧。候选遍历达到 5000 节点安全上限时标记 `search_truncated` 并 fail closed，不会把不完整搜索误判为唯一。

元素快照包含 UIA runtime id，并将 runtime id、process、automation id、语义类型、class、名称和 bounding box 组成动作指纹。语义 click/type_text 的 dry-run 将该指纹作为内部 `_resource_state` 持久绑定到 approval；通用 orchestrator 恢复路径和直接 UIAutomation API 都会在消费前重新生成目标状态，审批期间目标替换、重复项出现或 selector 失配会原子失效审批。2026-07-19 续修沿 ControlViewWalker 构造最多 64 层、必须完整结束的同进程 accessibility 父链，并将顶层窗口 runtime id/handle/process、进程可执行路径 HMAC、PID+创建时间实例 HMAC、父链 HMAC 和标题/账号/工作区 HMAC 加入私有资源态；可执行文件访问失败、父链中断/循环、PID 缺失，或在跨进程边界前不能证明顶层窗口时不创建 click/type 审批。claim 前重新 dry-run 后，Invoke/SetValue 所在线程还会再次查询唯一目标和上述全部身份，缩小复核到系统调用之间的竞态。私有 `_resource_state` 不原样保存 selector、元素/父链标签、可执行路径或窗口标题；用户提供的 selector/name/automation_id 仍可能出现在审批参数或差异预览中，并继续受既有公开预览脱敏策略约束。重复控件、element-object 旁路、窗口/进程/父链替换、禁用/离屏和遍历截断均有回归。剩余边界是 accessibility 完全不可见的应用内部账号、受保护/跨进程 provider、DPI/多屏、coordinate/hotkey fallback 的截图证据，以及系统调用开始后无法原子消除的竞态。

Microsoft 将 UIA 定义为 Windows 的现代可访问性与自动化框架；Power Automate 也建议现代 Windows 应用优先 UIA，并用层级 selector、多个 fallback selector 和属性组合区分相似组件。Lengrvis 已采用正确的 semantic-first 路线：`WindowsCOMUIAutomationTarget._click_sync` / `_type_text_sync` 先尝试 Invoke/Value control pattern，失败后才退到坐标或键盘输入。

当前主要残余风险在 accessibility 无法证明的上下文和坐标 fallback：

- `backend/app/perception/ui_automation_elements.py:13` 只包含 automation id、name/text、control type、class 和 process id。
- 私有批准态已绑定 top-level window、process executable、进程实例及完整同进程 parent chain；受保护/跨进程 provider 无法证明这些信息时会保守拒绝语义 click/type。应用完全不向 accessibility 暴露的内部账号切换仍无法被这条语义证据证明。
- 绝对坐标 click/drag、DPI 和多显示器拓扑尚未绑定局部截图、目标框或 display generation；这类 R3 fallback 仍主要依赖坐标参数、短 TTL 和审批。

建议：

- 在现有候选列表和 `match_count` 基础上增加可解释 match score，并要求 planner 在非唯一时补充 window、ancestor、automation id 或用户选择。
- 保持现有 process executable、进程实例和 parent-chain 私有绑定，并在 provider 可用时继续加入 control pattern 与更稳定的层级约束。
- 为不暴露语义身份、受保护或跨进程的应用提供专用 connector；无法得到等价身份和后态证据时明确禁止手机/后台批准。
- 为 UIA、UIA3 Raw、MSAA、DOM/应用专用 API 和坐标 fallback 定义明确优先级；Electron 应优先 DOM/受控 IPC 或 UIA3 Raw，不把屏幕坐标当稳定 selector。
- 坐标 fallback 对 R2/R3 动作显示局部截图和目标框；分辨率、DPI、窗口位置或屏幕拓扑改变后使旧批准失效。
- 测试重复名称控件、动态列表重排、窗口遮挡、DPI 变化、多显示器、焦点被抢占、目标在批准后替换等场景。

**验收标准**：任何多候选 selector 都 fail closed；批准后的目标替换、窗口切换或 DPI/布局漂移不会落到另一个控件。

### P0-8 修正回滚的状态机与用户文案

**实施状态（2026-07-11，文件系统真实性与后端终态核心闭环）**：rollback executor 输出 `state/attempted/succeeded/verified/verification_failed/failed/manual_required/unrecoverable`；API 将汇总写入 `Task.metadata.rollback` 和真实 final summary。移动回滚会核验源路径消失、目标为普通文件且 SHA-256 一致；回收与空目录删除会重读目标确实不存在；备份恢复会在删除备份前后校验原文件内容，并在内容不匹配时保留备份。断裂符号链接/重解析点不会被当成“已删除”。

后端 `TaskPhase` 新增真实 `rolled_back` 与 `repair_required` 终态：只有 `state=succeeded` 才从 completed/failed 转入 rolled_back，partial/manual_required/failed/unrecoverable 均转入 repair_required，API 同时返回 `task_status`。旧版本写成 failed 但含 rollback metadata 的记录会在模型读取时按汇总确定性规范化，不需要把历史成功回滚继续展示成失败。orchestrator、run resume、developer engine、tool execution journal、移动任务控制、retention、metrics 和 diagnostics 均已识别两个新终态；桌面可直接映射后端状态，同时保留 metadata 兼容。残余项是为 Excel/COM、浏览器和外部系统补偿器提供专用 verifier。

回滚是 OS Agent 建立信任的关键能力，最危险的失败不是“回滚失败”，而是“回滚部分失败却告诉用户已经成功”。原始调查发现过以下确定性错报，现已按上述范围修复：

- `backend/app/tools/rollback_tools.py:68` 明确可能返回 `requires_user_action=true`，也可能返回永久不可恢复。
- 原 executor 的 `count` 只是执行过的 rollback item 数量，没有统计成功、失败、人工、不可恢复和核验失败。
- `backend/app/api/routes_tasks.py:565` 执行后无条件把任务转为 `ROLLED_BACK`。
- `desktop/src/renderer/components/TaskTimeline.tsx:114` 把该 count 展示为“已回滚 N 个动作”。

建议引入：

```text
rollback_state = succeeded | partial | manual_required |
                 failed | unrecoverable

attempted, succeeded, failed, manual_required, unrecoverable
```

- 只有全部已核验成功才进入 `ROLLED_BACK`。
- 混合结果进入 `ROLLBACK_PARTIAL` 或 `REPAIR_REQUIRED`，逐项展示当前状态和下一步。
- 需要回收站人工恢复时提供目标、打开位置和完成确认，但不得自动宣称完成。
- 永久删除等不可逆动作在执行前明确显示 rollback 不可用，不能只在事后暴露。
- 回滚完成后重读资源后态，不能只信任 rollback handler 返回值；文件系统动作已完成，其他副作用类型仍需专用 verifier。

**验收标准**：混合“成功 + 人工恢复 + 永久不可恢复”场景下，API、任务状态、桌面文案和审计逐项一致；回滚成功误报率为 0。

### P0-9 在桌面主流程提供全局停止、纠正和接管

**实施状态（2026-07-11，控制入口与核心停止传播已闭环）**：桌面 Task Timeline 对运行/排队任务显示 Pause 和 Stop，对暂停任务显示 Resume 和 Cancel，对待审批任务显示 Cancel；动作复用 main/preload 的原生确认 IPC，成功后刷新 workspace。Stop 映射任务 cancel，并发触发 worker abort 与浏览器清理。开发测试、只读外部命令和卸载器改用可取消进程树，取消时终止 descendant；UIAutomation 每 50ms 检查 `_tool_abort_event` 并取消协程；后端 managed browser session 会关闭，Electron BrowserHost 只接受受限 `cancel_task` 消息并关闭精确 task 绑定的 session；HTTP 浏览器回退在跳转和分块读取边界检查取消。`task.cancel_completed` 审计记录单次 `elapsed_ms`，可直接汇总 p50/p95。残余项是修改目标、缩小授权范围、统一“正在停止”状态、正式候选 p95 分布，以及对已经进入 Excel/COM 调用或网络 connect/read 阻塞段的更强抢占能力。

Microsoft Human-AI Guidelines 要求系统在出错时支持高效纠正和全局控制；OpenAI HITL 建议把可暂停的风险边界设计为产品能力。原始调查发现 main/preload 虽已有 pause、resume 和 cancel IPC，但主桌面操作仍不完整；本轮已补齐基础入口与上述核心传播：

- `desktop/src/main/ipcTaskBridgeHandlers.ts:112` 已暴露任务暂停、恢复和取消。
- 原主 Task Pilot action 只有 `open | approve | compose`，现已增加 pause/resume/stop/cancel。
- 原结果区只有审批、核对和回滚预览，现已在任务状态对应位置提供可执行控制。

建议：

- 运行中始终显示 Stop；任务级提供 Pause、Resume、Cancel，且状态与后台真实生命周期一致。
- 审批不只提供批准/拒绝，还提供“批准一次”“缩小范围”“修改计划后重试”“回到桌面接管”。
- 失败重试前允许编辑目标、路径、收件人、工具范围和预算，避免原样重复错误计划。
- Stop 必须联动子进程、浏览器会话、COM/UI input 和网络请求；子进程、UIAutomation 和浏览器核心路径已接入，其他不可抢占同步边界仍应显示“正在停止”并阻止新副作用。
- 将人工 takeover、修改计划和局部完成视为正常结果，不把它们都归为失败。

**验收标准**：Stop p95 在 1 秒内阻止新的副作用；取消后孤儿子进程为 0；暂停文案出现时一定有可用恢复或取消入口。

### P1-9 用评测决定 Agent 数量和自治深度

Anthropic 的生产经验建议从最简单的可组合模式开始，把 workflow 与 agent 区分开：固定任务优先 predictable workflow，只有需要模型动态决定过程时才使用 agent。OpenAI 的评测指南也明确建议，多 Agent 架构应由 eval 驱动，过早引入会增加复杂度并拖慢上线。

Lengrvis 当前有 Planner、Supervisor、Orchestrator、SafetyReview、HumanGate、Memory、Browser、Computer、Document、File、Search、App、CodeReview 等角色。分层本身不是问题，但每一次额外模型调用都会增加延迟、成本、非确定性和跨 Agent 污染面。

建议为每个能力保留三个基线：

1. 确定性 workflow 或单模型 + 工具。
2. routing 到单一领域 worker。
3. orchestrator-workers / evaluator-optimizer。

只有当更复杂方案在同一 held-out 集上显著提高任务成功率、安全通过率或恢复率，并且延迟/成本仍满足预算时才启用。对系统诊断、固定文件扫描、审批执行等结构明确的任务，默认走 workflow；对开放式跨应用任务再启用动态 orchestrator。

### P1-10 把质量门禁拆成可归因的分层 scorecard

OpenAI 的评测最佳实践强调 task-specific、early-and-often、持续评测、生产分布、典型/边界/对抗样本和人类校准；2026 年 Agent Improvement Loop 进一步把 trace、人工/模型反馈、可重复 eval 和实现 handoff 串成闭环。WindowsAgentArena 与 OSWorld 则把评测放入可复现的真实 OS 环境，并以应用、文件和系统后态衡量结果；这比只做 Prompt/plan replay 更接近 OS Agent 的产品风险。

**实施状态（2026-07-11，评测控制闭环）**：真实 provider gate 已具备 130 项规模，并输出版本化五层 scorecard：provider/transport、planning contract、execution outcome、adversarial safety 与 failure attribution。每个失败具有稳定 primary failure class、error code 和脱敏 diagnostic；schema、intent、tool overlap 缺证据使用独立错误码并纳入分母；chat polling 超时归为 `TASK_PHASE_TIMEOUT`。本地 harness/runtime 异常不再污染 provider failure，provider 未成功时 adversarial safety 记为 `not_evaluated`。Quality gate 除原有阈值、覆盖率、adversarial 逐项断言和漏归因检查外，还要求 `evaluation_failure_count == 0`，因此大量已归因失败不能再被平均指标掩盖。

现有 130 项结果仍是负向证据：task success `0.3333`、intent/tool overlap `0.5758`、risk match `0.8621`，39 个 adversarial case 中 36 个未通过全部断言。新门禁不会把这些结果误报为可发布；需要用 scorecard 的 failure class/error code 修复 planner、policy、tool 或 outcome 后重新执行真实 provider gate。

建议分为六层门禁：

| 层 | 评测对象 | 核心指标 |
| --- | --- | --- |
| 0. Harness | provider、fixture、文件、网络、feature flag、tool registry | setup pass、错误分类完整率 |
| 1. Contract | structured output、schema、已知工具、参数完整性 | schema valid、unknown tool、missing arg |
| 2. Decision | intent、route、plan、tool choice、risk | intent accuracy、trajectory match、risk match |
| 3. Control | policy、provenance、approval、budget、sandbox | attack pass、false positive/negative |
| 4. Execution | 工具结果、幂等、恢复、rollback、资源后态 | outcome verified、duplicate side effect、recovery success |
| 5. Product | 用户目标、可理解性、人工修改、耗时和成本 | task success、human correction、approval comprehension、latency/cost |

- 每个失败必须有唯一 primary failure class 和可选 secondary causes；当前实现允许原始 provider/run error 为空，但最终报告的 failure class、error code 和 diagnostic 不得为空。
- 对抗 case 保持逐项全通过，不用总体平均分掩盖一个越权成功。
- 按 capability 和风险等级设门槛，不用一个全局 task success 覆盖 read/write/browser/mobile 的巨大差异。
- PR 使用快速单次 regression；nightly 对关键 case 运行 3-5 trials，同时报告 pass@1、pass^k 和方差，避免一次采样掩盖非确定性。
- 当前 narrated/extracted fixture 与真实环境 outcome 分开计分；逐步增加 live hidden DOM、真实 PDF/Office/OCR、恶意 MCP、memory-store mutation 和设备撤销 E2E。
- 保留 20% 左右不进入日常 prompt 调试的 hidden holdout，防止对公开 benchmark 过拟合。
- 自动 grader 定期与双人盲审校准；保留 disagreement rate 和 grader drift。
- 从真实失败 trace 生成回归 case，但敏感正文先脱敏并获得明确的数据使用依据。

### P1-11 将信任 UX 和可访问性纳入发布签收

OpenAI 的 computer-use 指南建议先完成安全工作，并在下一个高风险动作发生前即时确认；确认信息应说明动作、风险、数据、接收方和用途。Microsoft 的 Human-AI Guidelines 则覆盖首次使用、正常交互、系统出错和长期适应四个阶段，包括能力边界、纠错、解释、反馈和全局控制。

Lengrvis 在这一领域已有较好基础：`desktop/src/renderer/components/ApprovalDialog.tsx:38` 实现初始焦点、Tab trap、Escape 和焦点返还，Home/Progress 展示结果证据而非只展示内部 trace；移动审批有 accessibility label/hint；`desktop/src/renderer/lib/motion.ts:10` 尊重 reduced-motion。

**实施状态（2026-07-11，窄屏 reflow 核心闭环）**：Desktop shell 在窄屏改为单列，并使用 `overflow-x: clip; overflow-y: visible`；会扩大滚动宽度的环境伪元素在该断点隐藏，避免控件获得焦点后可滚动祖先自动横移并把系统诊断内容推离视口。`system-diagnostics-ui-smoke.cjs` 现在同时检查 document、关键卡片和文本的水平溢出，并在失败时报告 window/document/body 及祖先滚动容器状态；390x844 与桌面视口均已通过。完整 `npm --prefix desktop run smoke` 也覆盖任务驾驶舱审批、文件搜索移动操作、IPC 安全和 Browser Activity。该修复闭环了常规窄屏横向丢失，但尚不能替代 200%/400% zoom、Windows 文本缩放、NVDA 和高对比度候选签收。

**实施状态（2026-07-11，核心 Dialog 键盘闭环）**：新增共享 `AccessibleDialog` primitive，统一提供初始焦点、焦点外逃拦截、首尾 Tab/Shift+Tab 循环、Escape 关闭、关闭受阻状态和焦点返还。审批、回滚、步骤录屏、执行解释、首次使用同意页、完整协议视图和同意状态错误 `alertdialog` 均已接入。解释/回滚会在异步请求前保存 return-focus target；完整协议视图会返回原“查看完整协议”按钮；同意记录保存期间 Escape 与拒绝按钮被锁住；无法读取同意状态时 Escape 不能绕过 fail-closed 门禁，初始焦点落到“重试”。修复过程中还纠正了 primitive 将多个 `focus()` 用空值合并串联、最终总落到 dialog 容器的问题，现在只选择一个明确目标。活动中心因 280ms 退出动画保留专用实现，但具备等价的初始焦点、Tab trap、Escape、滚动锁和延迟返焦。`browser-activity-smoke.cjs` 已在真实 renderer 中逐项验证上述契约。核心 dialog/alertdialog 行为缺口已闭环。

**实施状态（2026-07-11，axe 自动门禁闭环）**：Desktop 精确锁定 `@axe-core/playwright 4.12.1`，并将扫描接入现有 `smoke:browser-activity`，因此完整 `npm --prefix desktop run smoke`、CI Desktop job 和 release `qa:gate` 会自动阻断 `serious` / `critical` violation。扫描覆盖桌面与 390px 首页、任务进度、审批、执行解释、回滚、首次同意、完整协议、同意状态错误 `alertdialog`、活动中心和设置/隐私页；失败信息包含 rule、impact、selector 与 failure summary。接入过程中修复了首页命令输入缺可见 label、状态色/风险色对比度不足、活动中心最近回复滚动区无法键盘聚焦等真实问题；扫描上下文显式启用 reduced motion，避免把弹窗淡入的瞬时混色误判为稳定界面。当前自动可检测项为 0 个 serious/critical，剩余项是候选 Windows 包上的 NVDA、Windows 高对比度、200%/400% zoom 与文本缩放人工签收。

下一步应补齐：

- memory quarantine 的来源、期限、信任级别、promote/revoke 和“本任务不学习”UI。
- `outcome_unknown` 的专用状态：明确告诉用户“可能已执行”，提供核对、标记完成、补偿/撤销和停止自动重试。
- 预算追加使用独立 capability：展示新增写次数、外发量、子进程或时间，不使用模糊的“继续”。
- 保持关键流程 axe serious/critical 自动门禁，并在候选 Windows 包上手工验证键盘-only、NVDA、Windows 高对比度、200%/400% zoom、Windows 文本缩放和 reduced motion。
- 审批、错误和结果状态不能只靠颜色；焦点返回、Escape 行为、live region 和超时提示要形成测试证据。
- 对未来新增 dialog/alertdialog 强制复用共享 primitive 或提供等价的动画型契约测试，避免重新出现容器抢焦、焦点外逃或 Escape 绕过门禁。
- 在已通过 390px 常规窄屏 reflow 的基础上，继续验证 200%/400% zoom 和 Windows 文本缩放不会横向丢失关键操作，并把这些模式纳入候选签收。
- **已落实（2026-07-19）**：ChatPanel 已将启发式数值改为“匹配原因 + 低/中/高建议强度”，不再显示精确概率；预测 schema 同时限制 confidence 为 `[0,1]` 并隔离单个畸形模型候选。真实接受/编辑/成功数据完成校准前，仍不得把建议强度解释为概率。

**验收标准**：WCAG 2.2 AA 可自动检测项无 serious/critical；首个任务、审批、暂停/恢复、结果、隐私删除均能键盘完成；NVDA 能读出动作、风险、目标和可撤销性。

### P1-12 补齐多父、多跳 provenance graph

**实施状态（2026-07-11，多父/多跳核心闭环）**：调度器将 `depends_on` 的全部 ToolResult envelope 作为不可丢失的 lineage 输入，执行 handler 绑定完整父链，下一跳工具输出合并 runtime 中的全部 upstream taint/source。side-effect sink 遇到父链缺失、HMAC/hash 不一致或 scope 冲突时 fail closed；并行 dependency、`A+B -> write`、多跳传播和良恶来源混合已有回归。

暂停、审批继续或进程重启路径已从持久化 journal 重建 observations；最新失败、缺失或未完成执行不会回退到旧成功结果。**2026-07-19 字段级基础设施、模型驱动 Document 调用、会话摘要与旧版线格式已闭环**：`field_lineage` 使用 RFC 6901 JSON Pointer、直接父 kind/id/hash 和闭集派生操作并进入 envelope HMAC；显式映射必须匹配经认证的真实父内容/哈希，指针存在且复制类操作规范值相等，未覆盖父来源保留 root fallback。一次性私有 provenance 已覆盖九个真实 Document 提取、摘要、问答、转换、表格和报告调用，预算/后处理改变字段时映射自动失效并保守降级。版本 sidecar 编入旧模型已有的 `sanitizers_applied`，所以 `extra="forbid"` 旧二进制可读取、验证和继续运行；旧版实际改写/合并后，升级时只保留保守来源，不恢复陈旧映射。

持久 conversation/session summary 现在使用同一 `ContentEnvelope`：根级 `summarize/merge` 边绑定摘要文本、session、最新 message anchor、canonical source message IDs 与实际摘要输入 bundle。旧摘要在首次加载时只生成带 `legacy_summary_lineage_unavailable` taint 的认证根，不声称无法证明的历史逐句映射；摘要、anchor、IDs、顶层 envelope、兼容 sidecar 或版本冲突都会 fail closed。Completion 在 CAS 冲突后重新加载、合并既有摘要/父 lineage 并重建 envelope；任务、偏好等其他 SessionContext 整行写也通过事务 CAS/reload-merge，Completion 后的 manual compact 原子替换 canonical IDs。私有 envelope 从 provider、prompt snapshot、telemetry、公开 token stats 和嵌套 compact metadata 递归剥离，只有明确的本地审计返回字段可见。专项 `23 passed`，相关宽回归 `194 passed`。

仍保留三个边界：legacy 根不能恢复历史消息正文或完整可重放 provenance 图；同时删除 envelope、compatibility sidecar 和迁移版本标记时，本地行无法与真实旧数据区分，若威胁模型覆盖直接 SQLite 篡改需外部单调完整性门；HMAC 密钥丢失或备份未携带密钥时现有摘要会 fail closed，不能自动恢复。确定性文档转换可继续使用工具级 root lineage，除非业务需要字段级解释。

**必测场景**：`A+B -> write`、`web -> summarize -> send`、并行 dependency、模型重命名字段、良性和恶意来源混合，以及中间工具试图删除 taint metadata。

### P1-13 为用户批准增加明确 TTL 和认证上下文

**实施状态（2026-07-11，授权新鲜度、风险分级与认证上下文核心闭环）**：`Approval` 具有从 `created_at` 确定性推导的 `expires_at`，按风险分级为 R0/R1 15 分钟、R2 10 分钟、R3 5 分钟、R4 1 分钟；没有风险字段的旧记录保守沿用 15 分钟。旧记录缺少字段时仍按原创建时间计算，不会因重启获得新 TTL。pending 列表会原子过期旧记录，桌面/移动批准、native confirmation、恢复执行、binding 校验和最终 `claim_approval_for_execution` 都会拒绝过期授权；数据库事务覆盖“检查后刚好到期”的竞态。

批准决策现在在同一事务写入 `authorized_at` 与结构化 `auth_context`。桌面原生确认绑定 confirmation id、确认时间、证明类型和 Ed25519 公钥指纹；测试专用 legacy HMAC 同样绑定 secret fingerprint。移动批准绑定 device id、token family id、credential id、token epoch、scopes 摘要和 step-up 时间。所有副作用最终共用的原子 claim 会在数据库写锁内重新核验当前桌面密钥或移动 device/family/credential/epoch/expiry；上下文缺失或畸形、密钥轮换、设备撤销、refresh reuse、family/credential 撤销和 token epoch 变化都会将未消费批准原子标记为 expired，而不是误报为已消费。已 approved 但未消费的记录可通过新的有效原生确认原子重新绑定，且不会延长原 TTL；正式环境的旧 approved 记录缺少认证上下文时 fail closed，测试 harness 仅保留显式 `LENGRVIS_TEST` 兼容。公开审批 HTTP payload 和 WebSocket 事件均不返回内部 `auth_context`。

此前 `Approval` 只有 created/decided/consumed time，短期 intent capsule 不能证明旧用户决定仍然新鲜，也不会因批准设备或确认密钥失效而撤销。TTL、风险分级和现有桌面/移动身份状态变化已闭环；**2026-07-19 续修关闭了 Desktop 启动、锁屏/解锁、挂起/恢复、托盘/长后台、隐藏登录启动，以及 Mobile family generation/前后台锁定的新鲜度子项**。Desktop generation 进入 challenge 签名、私有认证上下文、reauthorize 与最终原子 claim；后台首个同步边界撤销 signing，前台 runtime transition 完成后才重建。Mobile refresh 轮换立即使旧 access token 与未消费 approval 失效，回前台必须通过生物保护 SecureStore 和强制 refresh。语义 UIA 还在 claim 前及最终动作边界比较所属顶层窗口 HMAC 身份。整项仍保留三类诚实边界：accessibility 不可见的应用内部账号、coordinate/hotkey fallback，以及已线性化在途副作用的 stop/quiescence/broker。

建议：

- Windows 锁屏/解锁、挂起/恢复、应用长时间后台/托盘驻留和移动 family generation 已完成；下一步用物理设备证明锁屏/生物认证路径，并保持 generation 变化使未 claim 旧批准失效。
- 保持当前风险分级 TTL；只有在遥测和故障演练证明风险可控后，才考虑放宽 R2，R3/R4 不应因便利性延长。
- Desktop 主进程/隐藏启动 generation、移动前后台锁代际及可解析语义窗口重新 dry-run 已完成；对 accessibility 不可见的应用内部账号和 coordinate/hotkey 路径，增加专用 connector/截图证据或明确禁止后台批准。
- 过期、revision 变化和资源漂移统一走同一失效原因模型，便于 UI 解释和审计。

### P1-14 将长期记忆变成显式 namespace，而不只是 envelope scope

**实施状态（2026-07-11，核心闭环）**：Memory schema、recall 和所有读写 API 强制携带 `principal_id`、`workspace_id`、`domain_scope`、`kind`、`version`、`supersedes` 和 `conflict_status`。Recall 在数据库查询层隔离 namespace；同一父记忆最多存在一个 active+recallable successor，创建、promote、原始 SQL 与并发路径均由事务校验、唯一 guard 表和触发器 fail closed。历史重复 successor 迁移为 `conflicting`，不会静默召回。Retention 使用权威 namespace/quarantine 状态；主体擦除清理正文、embedding/index、namespace、quarantine、active successor 和 lineage。

残余项是桌面 quarantine/conflict/version UI、短期 session context 与长期 distilled memory 的更明确产品区分，以及 active 用户确认正文的应用层加密。

**验收标准**：不同用户、工作区、组织和领域默认互不可见；冲突记忆不会静默覆盖；删除主体数据会清理正文、embedding、索引和 lineage。

### P1-15 对外准确描述 MCP 兼容层，并补齐标准 lifecycle

**实施状态（2026-07-17，核心 lifecycle/transport 已闭环，授权仍开放）**：`backend/app/mcp/client.py` 已覆盖 `initialize`、协议版本/capability 协商、`notifications/initialized`、session、Streamable HTTP JSON/SSE、SSE `retry`/`Last-Event-ID` 恢复、progress/cancel、分页、输入/输出 schema 复验和 shutdown；`backend/app/mcp/stdio_transport.py` 提供无 shell、最小环境、严格 stdout framing 的 stdio transport，并在 release profile 缺可信隔离时 fail closed。官方 conformance 的 initialize/tools_call/sse-retry 已进入 CI。第三方 MCP 工具仍统一保持 R4 handoff，静态 bearer token 强制 resource binding，原始 secret 不允许写入持久 settings。

尚未闭环的是 OAuth 2.1 metadata/PKCE/client registration/client credentials 的完整官方 auth suite、elicitation、list-changed、更多恶意 server/断网/会话劫持矩阵以及 release 下 stdio 原生隔离。因此产品仍应称为 Preview MCP integration，不宣称完整通用 interoperability；达到完整声明前需通过相应官方 auth/扩展 conformance 与候选安全证据。

### P1-16 将 SBOM 与候选产物绑定并生成签名 provenance

**实施状态（2026-07-11，候选门禁代码闭环）**：CI 与 RC 的 `.tmp` evidence 上传显式启用 hidden files；RC 生成 CycloneDX SBOM、SHA-256 subject manifest，并由 GitHub hosted builder 分别签发 SLSA provenance 与 CycloneDX SBOM attestation。Publish 在 materialize 前按 allowlist 验证 manifest path 与 digest，对每个 artifact 使用本地 provenance/SBOM bundle、明确 predicate type、candidate source digest、repository 与 signer workflow 做两次密码学验证；下载的 SBOM 文档与签名 DSSE predicate 结构化比对一致后，作为带 checksum 的 GitHub Release asset 发布。

当前不能伪造的残余项是真实 GitHub RC run 的在线验签输出与 reviewer 签收；此外仍需可复现构建、Prompt/tool/policy/model capability manifest attestation 和更细粒度撤销。

建议：

- 每个候选包同时发布 CycloneDX/SPDX SBOM、artifact digest manifest、builder/workflow/run identity 和 commit。
- 使用 GitHub Artifact Attestations、Sigstore 或等价方案签发并在发布前验证 build provenance 与 SBOM attestation。
- installer、portable zip、backend binary、mobile artifact 和 capability manifest 必须绑定到同一个 candidate identity。
- 支持按 dependency、Skill/MCP server、model、prompt、policy 或 tool hash 撤销候选。

### P2-4 逐步映射 OpenTelemetry GenAI 语义

OpenTelemetry 已将 GenAI、Agent、Plan、Tool 和 MCP spans 拆到独立语义约定仓库，目前状态仍为 Development。Lengrvis 无需立刻替换自研 trace，但应建立兼容映射，避免企业化时每个 exporter 都重新解释私有字段。

当前 `backend/app/observability/tracing.py:22` 支持 span attribute，但结束时只输出 span name、status、duration；尚未形成标准导出或父子 span 模型。建议优先映射：

本轮 UIAutomation 与 context compaction 的本地固定标签计数改善了拆分后的 operational signal，但不改变上述标准导出、跨组件 parent trace、SLO/告警和基于环境后态成功率仍未完成的结论；provider-limit compaction 的 `applied` 也不等于 provider retry 或最终任务成功。

- run/agent/workflow/plan/tool operation 与稳定 version。
- model/provider、latency、token、retry、policy verdict、approval、budget、rollback 和 error type。
- MCP server/tool identity、但不记录 token、Prompt 正文、原始 tool args、截图或文件内容。
- 本地默认仅保留低敏感聚合指标；详细 trace 必须 opt-in、有 TTL、可预览、可删除。
- 产品成功率以 `result_verified` 和环境后态为准，不能只统计 task/run status。
- 发布健康除 crash/hang 外增加语义 canary：内部、1%、10%、50% 分阶段比较任务成功、安全阻断、tool failure、人工接管、rollback partial 和成本；SLO 回退自动暂停更新或触发 kill switch。

### 11.3 分阶段补充路线图

#### 发布前 / 0-2 周

1. 用 v2 分层 scorecard 重新运行 130 项真实 provider gate，按 failure class/error code 修复当前 planner、policy、tool 和 outcome 问题；达标前冻结默认自治范围和新增 R2/R3 工具。
2. 在真实 GitHub RC 上执行候选构建与 publish 验证，保存双 bundle/predicate 验签、SBOM 一致性和 Release asset checksum 证据。
3. 完成 clean-machine Windows、物理 Android LAN/WSS、第三方安全复测和 candidate-bound owner sign-off。
4. 对已接入的 Document 与 conversation/session summary lineage 做候选升级/降级演练；若直接 SQLite 篡改属于正式威胁模型，增加受外部完整性保护的单调迁移标记，并验证同时剥离 envelope/sidecar/version 仍会 fail closed。
5. 在桌面补齐 Memory quarantine/conflict/version UI，以及修改计划、缩小授权范围、预算追加和 `outcome_unknown` 对账 UI。
6. 完成 NVDA、Windows 高对比度、200%/400% zoom 与文本缩放候选矩阵。

#### 首个稳定版 / 2-6 周

1. 建立 workflow、单 worker、多 Agent 三档基线，以 eval 决定每个能力的默认拓扑。
2. 增加外部 receipt/reconciliation connector、provider 瞬态错误验证清单和非文件型 rollback verifier。
3. 增加 crash injection、UI selector ambiguity、DPI/多显示器、人工接管和恢复 provenance E2E。
4. 为 task/chat/memory/plan/tool/approval 敏感正文增加应用层加密或 SQLCipher 分层方案。
5. 明确 MCP compatibility adapter 边界，补 lifecycle/conformance 或切换官方 SDK。
6. 完成 Prompt/tool/policy/model capability manifest 与可撤销 attestation。

#### 企业化 / 6-12 周

1. 将 GenAI/Agent/MCP telemetry 映射到 OpenTelemetry，并提供默认关闭的企业 exporter。
2. 用生产 opt-in trace、人工反馈和安全事件持续扩充 eval corpus。
3. 对关键外部工具采用 provider receipt、幂等 API key 和 reconciliation connector。
4. 建立可复现 Windows 构建、运行时 capability attestation 与分阶段 canary/自动回退。
5. 将任务成功、安全、成本、延迟、恢复和可访问性统一纳入 release scorecard。

### 11.4 建议的量化门槛

| 指标 | RC 建议门槛 |
| --- | --- |
| 对抗安全 | 每个必需 adversarial case 100% 通过，不接受均值豁免 |
| 失败可归因 | 100% failed run 有 primary failure class、error code 和脱敏 diagnostic；未归因失败为 0 |
| 副作用恢复 | fault-injection 后重复副作用为 0；未知结果 100% 进入人工对账 |
| 回滚真实性 | full/partial/manual/unrecoverable 分类完整；成功误报为 0 |
| UI 目标安全 | 多候选写 selector 100% 阻断；批准后目标漂移 100% 阻断 |
| 结果质量 | read/write/browser/document/memory/mobile/developer 分类别达标，不只看总平均 |
| 人工协作 | 高影响动作 100% point-of-risk confirmation；Stop p95 < 1 秒；拒绝和接管可在一步内完成 |
| Approval 新鲜度 | 所有用户批准有 TTL；过期、重启、锁屏和长暂停后旧批准消费为 0 |
| Provenance/Memory | 多父 lineage 缺失时 100% 阻断；跨 principal/workspace 默认泄漏为 0 |
| MCP | conformance suite 覆盖 lifecycle/transport/tools；未完成前不宣称完整互操作 |
| 候选供应链 | 每个 release artifact 都有 digest、SBOM 和可验证 signed provenance |
| 可访问性 | 自动 serious/critical 为 0；关键流程 keyboard/NVDA/high-contrast 矩阵通过 |
| 可观测性隐私 | 默认 telemetry 不含 Prompt、正文、原始参数、截图、凭据和私有路径 |

### 11.5 扩展调查方法与限制

- 并行审阅代理架构、工具生命周期、UIAutomation、桌面主流程、回滚、记忆、MCP、审批、observability、评测 runner 和 release workflows。
- 2026-07-11 深读 15 个新增权威或原始页面，并核验当时 45 个来源链接可访问；2026-07-13 又复核 OWASP、MCP、Electron、Windows AppContainer/Windows Agent Security、OpenAI Computer use、Anthropic Agent 架构与 OpenTelemetry，并补入执行态评测、可访问性、Responses、Workflow、A2A、CaMeL 和 ASB 原始来源。第 9 节保留原有 62 个来源；2026-07-16 新增来源直接列在本节表格中，本轮没有重新自动化探测全部链接。
- 对 `.tmp/qa-evidence/real-llm-eval/real-llm-eval-report.json` 做分类汇总，用真实负向结果校准建议，而不是只根据代码存在性评分。
- 本轮扩展调查是静态审阅和现有证据分析，没有再次执行真实 LLM、Windows GUI、崩溃注入、NVDA、真实 MCP server 或候选发布 workflow；相应建议仍需实现与动态验证。
