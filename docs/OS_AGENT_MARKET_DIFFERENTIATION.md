# Lengrvis 与个人 OS Agent 市场竞品差异评价

> 版本：2026-06-08
> 用途：技术路线判断与对外叙事校准
> 口径：以个人 OS Agent 为主线，开发者 Agent 仅作为邻近参照  
> 方法：本地仓库静态审计 + 公开资料交叉验证；所有结论区分“事实 / 推断 / 建议”，避免把代码路径写成发布级体验

## 1. 执行摘要

Lengrvis 当前最可信的市场叙事不是“全面替代某个 OS Agent”，而是 **Windows-first 的本机 OS agent 技术底盘**：它围绕用户自己的文件、文档、系统、浏览器、应用、移动审批和远程桌面来执行任务，并把风险分级、dry-run、审批绑定、审计和扩展协议放在产品核心。这个定位天然比云端通用 Agent 更窄，但也更容易形成差异化：本机执行、可审计、可扩展、可自托管。

按仓库实证，Lengrvis 的实质编排组件是 Orchestrator、Planner、Supervisor、SafetyReview、OSExecutionEngine 和 Memory；File、Document、Computer、App、Browser、Search 更准确地说是 domain shell agents，提供 owner/prompt/allowed tools 边界并共享 `act()`。正常成功路径大量依赖 deterministic fast path、PolicyEngine、ToolRuntime 和 schema validation，LLM 主要介入规划、文档 AI、失败恢复和复杂改参。这种口径比“很多个完全自主 Agent”更诚实，也更适合做安全可靠的 OS 自动化。

与腾讯 Marvis、Microsoft Copilot+ PC 等发布级 OS AI 产品相比，Lengrvis 不应硬碰平台分发、端侧模型品牌和消费级图库/跨端打磨。它的可辩护差异是工程透明度和控制面：R0-R4 风险等级、路径沙盒、审批绑定、审计日志、Skill 安全审查、工具注册、上下文管理、移动审批和远程输入授权都有可读代码支撑。与 OpenAI ChatGPT Agent、Manus、Genspark 这类云端通用 Agent 相比，Lengrvis 的优势不是通用智能，而是能直接在本机 PC 上处理私域文件、系统状态、App 和审批闭环。与 Claude Code/Computer Use 相比，Lengrvis 应服务普通电脑用户，而不是主要服务代码仓库。

**判断**：Lengrvis 的路线图应从“追平别人所有表面能力”转向“把本机可控执行做深”：隐私/本地模型开箱路径、移动审批/只读查看/短授权输入、文件索引与文档引用、App Integration Protocol、Skill/MCP 生态、发布证据门禁。只在这些证据充分时，才对外写“可演示”或“可发布”。

## 2. 本地项目事实基线

### 2.1 已实证的核心能力

| 维度 | Lengrvis 当前状态 | 证据 |
|---|---|---|
| 定位 | Windows 优先的本地电脑 AI 管家，桌面端是消费级电脑助手入口 | `README.md`、`desktop/src/renderer/App.tsx` |
| Agent 编排 | Orchestrator + Planner + Supervisor + SafetyReview + OSExecutionEngine + Memory；File/Document/Computer/App/Browser/Search 是领域 shell agent，共享 `BaseAgent.act()` 和 deterministic fast path | `backend/app/agents/orchestrator_agent.py`、`backend/app/agents/base.py` |
| 工具注册 | 文件、文档、系统、远程、UI 自动化、工作流、应用、Excel、浏览器、搜索、视觉、聚类、Skill、MCP 等工具集中注册 | `backend/app/tools/registry.py` |
| 安全模型 | R0-R4 风险等级；R2/R3 操作走 dry-run、审批、绑定校验；R4 阻断 | `backend/app/policy/risk.py`、`backend/app/policy/policy_engine.py`、`backend/app/orchestration/tool_runtime.py` |
| 文件与文档 | FTS5、语义搜索、重复文件、文件操作、PDF/DOCX/XLSX/PPTX/CSV 抽取、OCR、文档摘要/问答/报告 | `backend/app/tools/file_tools.py`、`backend/app/tools/document_tools.py`、`backend/app/services/document_service.py` |
| 浏览器自动化 | 会话、观察、截图、导航、点击、填表、提交；写动作支持 dry-run 和审批 | `backend/app/tools/browser_tools.py`、`backend/app/services/browser_activity_runtime.py` |
| 本地/云模型路由 | efficiency / privacy / hybrid；隐私模式优先 ONNX，再探测 Ollama、LM Studio、llama.cpp；无本地后端时明确失败，不静默回退 Mock | `backend/app/llm/registry.py`、`backend/app/llm/local_provider.py` |
| 本地模型准备 | Settings/后端已有 Ollama 状态、硬件 readiness、setup-plan、install/start/pull/install-local-model 与 WebSocket 进度；默认发布包仍不捆绑离线模型 | `backend/app/api/routes_settings.py`、`backend/app/services/ollama_service.py`、`desktop/src/renderer/components/SettingsPanel.tsx` |
| ONNX/硬件加速 | 有 WinML / DirectML / OpenVINO / CPU provider 检测、健康检查、warmup/test_generate 框架 | `backend/app/llm/onnx_provider.py` |
| 文件环境感知 | `FileWatcher` 已用 watchdog 监听授权目录并增量更新 FTS；启动时接入 environment stream | `backend/app/indexer/file_watcher.py`、`backend/app/main.py` |
| 调度任务 | croniter 单进程调度器，可触发 Orchestrator 执行任务，并发通知 | `backend/app/services/scheduler_service.py` |
| 移动端审批 | Android companion 已有配对 payload 解析、内置 QR 扫码/粘贴/手动 fallback、JWT、审批列表、审批详情、批准/拒绝、WebSocket 通知和隐私保护通知正文 | `mobile/src/screens/PairScreen.tsx`、`mobile/scripts/mobile-token-smoke.cjs`、`mobile/App.tsx`、`backend/app/api/routes_mobile.py`、`backend/app/services/mobile_pairing_service.py` |
| 远程桌面 | 有屏幕流、输入 WebSocket、移动 token scope、输入审批链、只读/短授权输入/结束接管状态；默认由 `remote_desktop_enabled` 控制 | `backend/app/api/routes_remote.py`、`backend/app/services/remote_desktop_service.py`、`mobile/src/screens/RemoteScreen.tsx` |
| 通知 | 后端通过 AgentBus 发布通知；桌面 `NotificationBridge` 监听通知 WebSocket 并投递 Electron Notification；移动通知默认隐藏敏感正文 | `backend/app/services/notification_service.py`、`desktop/src/main/notifications.ts`、`mobile/src/notifications.ts` |
| 可扩展性 | Skill YAML + 沙盒 + 安全审查 + 动态工具注册；MCP registry 可适配工具定义 | `backend/app/skills/*`、`backend/app/mcp/*` |

### 2.2 当前成熟度判断

**事实**：Lengrvis 的核心 runtime 已经不止“demo 壳”。它有独立后端、Electron 桌面、React Native 移动端、pytest 覆盖、打包脚本、审计库、运行态状态机和真实工具注册。

**推断**：它的工程成熟度更像“可继续产品化的技术底盘”，而不是“已经能面向普通用户规模发布的消费产品”。决定体验的短板集中在模型分发、硬件适配、跨端远控稳定性、第三方 App 深度能力、权限 UX、安装包体积和用户教育。

**建议**：后续报告和路线规划应把“代码已实现”和“发布级体验已完成”拆开。否则会高估 Lengrvis 的市场成熟度，也会低估已有架构资产的价值。

## 3. 市场参照系

### 3.1 腾讯同类 OS Agent：最直接对标对象

腾讯同类 OS Agent 官方定位是操作系统层级 AI 助手，官网提供 Windows、macOS、Android 下载入口，并说明 iOS 在送审中。官网强调两种模式：效率模式走端云协同，本地模式使用端侧大模型，文件 0 上传；还强调手机连接电脑后可实时查看任务画面并接管，支持文件/图片内容搜索、AI 图库、AI 文档库、一句话电脑设置、文档/表格理解与生成等能力。

**对 Lengrvis 的意义**：腾讯同类 OS Agent 已经把“个人 OS Agent”讲成消费级产品故事：跨端、隐私、本地文件、系统设置、办公学习、生活场景。Lengrvis 在架构上对齐了很多底层概念，但在用户能直接感知的开箱能力上落后。

### 3.2 OpenAI ChatGPT Agent / Operator：云端虚拟电脑型通用 Agent

OpenAI 在 2025-07-17 发布 ChatGPT agent，称其会用自己的虚拟电脑完成复杂任务，能在可视浏览器、文本浏览器、终端、API 和 connectors 之间切换；用户可以中断、接管浏览器或停止任务，重要动作前请求权限。Operator 是此前网页操作方向的研究预览，ChatGPT agent 将 Operator 和 deep research 的能力合并到统一 agentic system。

**对 Lengrvis 的意义**：OpenAI 的优势是模型、通用任务能力、网页和文档/表格/幻灯片产出；弱点是不直接深度接管用户本机 OS。Lengrvis 应避免正面比拼通用智能，转向“本机上下文 + 本地工具 + 审批审计”的差异化。

### 3.3 Microsoft Copilot / Copilot+ PC：平台原生型 OS AI

Microsoft 在 Copilot+ PC 上提供 Recall、Click to Do、Windows Search、Copilot Vision、Settings agent 等体验。Click to Do 会在本地分析屏幕快照中的文本和图像，部分智能文本动作由本地 Phi Silica 小模型和 NPU 支撑；Windows 体验博客还宣布 Copilot Actions 将从网页扩展到本地文件，并以单独的 agent workspace 完成任务。

**对 Lengrvis 的意义**：Microsoft 代表“平台方终局”：OS 原生入口、NPU、系统设置、屏幕理解、文件入口、企业策略与安全。Lengrvis 无法复制 Windows 原生分发，但可以在非平台方角度提供更独立、可审计、可定制的 agent runtime。

### 3.4 Anthropic Computer Use / Claude Code：通用电脑控制与开发者 Agent

Anthropic 在 2024-10-22 发布 computer use public beta，让 Claude 通过屏幕、鼠标、键盘使用电脑，官方同时提醒该能力仍实验性、可能笨拙和出错。Claude Code 则是开发者产品，官方描述为能在终端、IDE、Slack、Web、桌面中处理代码任务，桌面版支持并行任务、视觉 diff、预览服务器、PR 状态，并且可从手机把任务路由到本地机器。

**对 Lengrvis 的意义**：Claude Code 是强邻近参照，但不是同一主赛道。它验证了“本地机器 + 远程发起 + 审批/差异预览 + 自动 PR”的价值。Lengrvis 可以借鉴其任务可观察性和多端任务路由，但需要围绕普通 PC 用户，而不是代码仓库。

### 3.5 Manus / Genspark：云端工作空间型通用 Agent

Manus 官网主打“Hands On AI”，入口任务包括创建 slides、建网站、开发桌面 App、设计等，并提供 Web app、browser operator、Wide Research、Slack integration、桌面和移动下载入口。Genspark 则定位 all-in-one AI workspace，列出网站、演示文稿、表格、报告、PDF 摘要、电话、市场研究等大量工具。

**对 Lengrvis 的意义**：这类产品的强项是任务模板、内容产出、云端工作流与市场传播；弱项是本地 OS 权限、用户文件系统、设备设置和离线隐私。Lengrvis 应将其视为“用户期望上限”，但技术路线不应被拉向纯云端工作空间。

## 4. 竞品矩阵

| 维度 | Lengrvis | 腾讯同类 OS Agent | ChatGPT Agent / Operator | Microsoft Copilot+ PC | Anthropic Computer Use / Claude Code | Manus / Genspark |
|---|---|---|---|---|---|---|
| 产品定位 | 本地电脑管家/OS Agent 原型 | 消费级全端私人 AI 助手 | 云端虚拟电脑通用任务 Agent | Windows 平台原生 AI 体验 | API 电脑控制 + 开发者 Agent | 云端 AI 工作空间/通用 Agent |
| 平台 | Windows 优先；Electron；Android companion；macOS 打包路径 | Windows / macOS / Android；iOS 规划 | ChatGPT Web/App | Windows 11 / Copilot+ PC | API、Claude Code 桌面/终端/IDE/Web/Slack/移动路由 | Web、桌面/移动入口、工作区工具 |
| OS 控制深度 | 文件、系统信息、Windows 设置 URI、远程桌面、UIAutomation 语义控件、屏幕截图、鼠标/键盘输入；写入动作 dry-run + 审批 | 官方宣称深度理解 PC OS，可改设置和手机接管 | 主要在自有虚拟电脑和网页环境 | OS 原生 Settings agent、Recall、Click to Do、文件/屏幕上下文 | Computer Use 可用屏幕/鼠标/键盘；Claude Code 操作开发环境 | 以云端任务和内容生产为主 |
| 本地模型 | 探测 ONNX / Ollama / LM Studio / llama.cpp；未捆绑模型 | 本地模式端侧大模型，文件 0 上传 | 云端模型为主 | Phi Silica/NPU、本地 OCR/Click to Do | Claude 云端/API；Claude Code 在本机执行但模型云端 | 云端模型为主 |
| 文件/文档 | FTS5、语义搜索、OCR、文档 AI、文件工具、Excel COM | AI 图库、AI 文档库、文档/表格理解与生成 | 可上传/生成/编辑文件，侧重云端任务产物 | Recall/Click to Do/文件 Actions，和 Windows/Office 集成 | Claude Code 读写代码文件；Computer Use 可操控应用 | 大量文档/报告/表格/PPT 工具 |
| 浏览器/网页 | Playwright/httpx；读写动作审批 | 官网场景含微博签到、票务信息、搜索监控等 | 强项：网页浏览、登录接管、采购/预订等 | Edge/Copilot/Actions 生态 | Computer Use 能网页操作，Claude for Chrome/Code 生态 | Browser operator / Web app |
| App 自动化 | 应用扫描、启动、卸载、Excel COM；深度集成少 | 官方宣称 APK/EXE 一句话调用、多端在线 | 主要网页和云工具，非本机 App | Windows/Office/Teams/Paint/Photos 等平台集成 | 开发工具链强，通用 App 依赖 computer use | 内容/办公工具强，本机 App 弱 |
| 安全审批 | R0-R4、dry-run、审批绑定、HMAC、审计、PII 脱敏、路径沙盒 | 官方强调本地模式、隐私条款；细节未公开 | 重要动作前确认、接管、Watch Mode、隐私控制 | Windows 安全、设备加密、Hello/ESS、agent workspace | 安全分类器和低风险建议；Claude Code 有开发者权限模型 | 公开细节相对少 |
| 可扩展性 | Skill YAML、MCP、OpenAI-compatible、工具注册 | 未公开 | Connectors、API、ChatGPT 工具 | Windows/Graph/Office/企业策略 | API、MCP/工具、Claude Code 集成 | 模板/工具生态 |
| 成熟度 | 技术底盘较完整，产品化中 | 发布级消费产品 | 发布级云端 Agent | 平台原生、逐步 rollout | API/开发者产品成熟，通用电脑控制仍实验性 | 发布级云端工具工作区 |

## 5. 三类差异

### 5.1 Lengrvis 更有差异化的地方

**安全与审计透明度**  
事实：Lengrvis 的 R0-R4 风险等级、dry-run、审批绑定、审批消费防重放、路径沙盒、PII 脱敏、Skill 安全审查都在代码中可查。
推断：在公开资料层面，成熟消费产品通常会描述隐私和安全原则，但不会把工具级风险模型和审批绑定细节暴露成可自查的工程资产。Lengrvis 对企业、专业用户、自托管用户的“可解释安全”更有差异化。

**本地执行面更可控**  
事实：Lengrvis 的工具面围绕用户本机：文件、系统、应用、浏览器、Excel、远程桌面、UI 自动化、调度、环境流。
推断：这比纯云端 Agent 更适合“整理下载目录、查系统配置、修改设置、扫描重复文件、审批远程输入、处理本地合同/表格”这类 PC 私域任务。

**开放架构和可替换模型**  
事实：Lengrvis 使用 OpenAI-compatible provider，支持 chat/completions 与 responses 风格，能在 efficiency/privacy/hybrid 间路由，并可探测本地后端。
推断：这适合国内外多模型供应、私有化部署和成本控制；腾讯/Microsoft/OpenAI 的消费产品通常更封闭。

**插件/Skill/MCP 方向更适合长尾 App**  
事实：Lengrvis 已有声明式 Skill、沙盒执行、动态注册和 MCP registry。
推断：如果未来定义 App Integration Protocol，它可以靠社区/企业 Skill 扩展长尾应用，而不是逐个硬编码。

### 5.2 Lengrvis 已接近市场主流的地方

**多 Agent 编排**  
Lengrvis 的 Orchestrator + Planner + Supervisor + SafetyReview + OSExecutionEngine + Memory 架构，已经具备个人 OS Agent 的编排骨架；File/Document/Browser/Computer/App/Search 等领域 Agent 主要承担工具边界、prompt 和 `act()` 路由。多数成功路径依赖 deterministic/schema validation、PolicyEngine 和 ToolRuntime，而不是每次都由独立 LLM Agent 自主推理。差异仍在模型能力、产品成熟度和领域 Agent 的真实任务成功率。

**文件/文档理解**  
Lengrvis 具备全文索引、语义搜索、OCR、文档摘要、QA、报告和多格式抽取，和腾讯同类 OS Agent 的 AI 文档库方向、Genspark 的文档/报告工具方向相邻。差距在图片语义聚类、人像/地点/节日等消费图库能力，以及可视化体验。

**浏览器与网页动作**  
Lengrvis 的浏览器工具能覆盖打开、读取、截图、导航、点击、填表、提交，并在高风险动作上审批。这在机制上接近 Operator/ChatGPT Agent 的网页操作链条；差距是反注入、登录接管、长任务稳定性、真实网站适配与可视 UX。

**移动审批与远控基础**  
Lengrvis 已有 Android companion、LAN 配对、JWT、审批 WebSocket、远程屏幕流和输入审批。它不是“完全没有跨端”，但距离腾讯同类 OS Agent 官方描述的手机实时接管电脑、随身个人电脑体验，还有发布级稳定性、锁屏/权限、网络穿透、画面延迟、输入映射和用户信任设计差距。

### 5.3 明显短板

**本地模型入口已产品化，但默认仍不是离线开箱**
这是 P0。腾讯 Marvis 和 Microsoft Copilot+ PC 都把“端侧/本地”做成用户能感知的产品能力。Lengrvis 现在已经不只是“能探测本地模型”：Settings 和后端已有 Ollama 安装、启动、拉取推荐模型、硬件 readiness、setup-plan 和失败修复入口。但默认 Windows portable/zip/SFX 仍不捆绑 Ollama 离线模型或 GPU runtime，候选版本还需要 clean-machine local model smoke 才能写成“开箱可用”。

**NPU/硬件加速还停在框架层**  
Lengrvis 有 ONNX provider、WinML/DirectML/OpenVINO 检测和 warmup/test_generate，但缺默认模型包、量化模型选择、设备兼容矩阵、fallback 策略和安装体验。Microsoft 与腾讯都把端侧模型/NPU作为核心卖点，Lengrvis 必须把“可配置框架”推进到“默认可运行”。

**App 深度集成不足**  
Lengrvis 有应用扫描、启动、卸载和 Excel COM，但缺 WPS/Office 全套、微信/浏览器收藏/邮件/日历/网盘/图片管理等消费常用 App 的稳定集成。个人 OS Agent 的粘性来自“真能替我操作常用软件”，不是只会列进程或打开设置。

**跨端体验已有闭环雏形，但还不是发布级远控产品**
Android companion 已覆盖配对、内置 QR 扫码/粘贴入口、审批、任务状态、只读屏幕流、短授权远程输入、结束接管和 LAN/TLS 风险提示。它已经超过“只有审批列表”的阶段，但仍需要真实手机/模拟器扫码配对、网络穿透、证书信任链、锁屏/权限、延迟、输入映射和移动端继续/发起任务的产品化证据；当前源码 smoke 只能证明扫码路径存在，不能替代真实设备闭环。腾讯 Marvis 已把手机操控电脑放在官网核心卖点，Claude Code 也强调从手机把任务路由到本机；Lengrvis 应先把“手机看进度、看屏幕、批准/拒绝、短授权接管、可撤销”做成可信闭环。

**消费级 UX 和可信解释不足**  
Lengrvis 桌面端已有办公室 Agent、能力卡、审批弹窗、设置页，但仍需要针对普通用户的任务模板、错误恢复、权限解释、模型安装引导、隐私状态指示、任务回放和“失败时下一步怎么办”。

## 6. 技术路线评估

### 6.1 多 Agent 架构

Lengrvis 的多 Agent 架构方向正确，但短期不建议再增加 Agent 数量或强化“自主 Agent 数量”宣传，而应提高实质编排组件和领域 shell agent 的任务成功率与可观察性：

- 强化 Planner 输出的工具选择质量，减少计划和工具 schema 的错配。
- 给 File/Document/Browser/Computer/App Agent 增加更明确的能力边界与失败恢复策略。
- 把 SafetyReview 的结论映射成用户能理解的“为什么需要审批 / 为什么不能做”。
- 将每步截图、工具事件、审批状态和最终结果合成可回放时间线。

### 6.2 工具注册与权限模型

Lengrvis 现有 R0-R4 + dry-run + approval binding 是核心资产。下一步应把它产品化：

- 所有 R2/R3 工具都必须有用户可读 preview，且 preview 与真实执行绑定。
- 远程输入、浏览器提交、文件删除、卸载 App 等场景要有更强的二次确认。
- 让用户能配置“永久允许某类低风险操作”和“永不允许某类高风险操作”。
- Skill 安装时显示权限清单，类似浏览器扩展权限，而不是只在后台审查。

### 6.3 本地/云端模型路由

Lengrvis 的 efficiency/privacy/hybrid 是正确方向，但需要从“开发者配置”转成“用户可理解”：

- 效率模式：云端模型，适合长推理、网页、综合规划。
- 隐私模式：本地模型，适合文件名、文档摘要、系统信息、离线任务。
- 混合模式：规划可云端，私密文件内容和本地 OCR/embedding 留在本地。

关键不是模式数量，而是每次任务要显示“哪些内容会上云、哪些只在本机处理”。这会成为 Lengrvis 相对 ChatGPT Agent、Manus、Genspark 的主要信任差异。

### 6.4 端侧推理与硬件加速

Lengrvis 已有 ONNX provider 框架，但 P0 应先解决“默认本地模型能跑”，再追求 NPU 性能：

1. 内置或一键下载一个小模型，例如 Qwen 3B/4B 量化版本，优先覆盖摘要、分类、规划辅助、隐私问答。
2. 为 CPU、DirectML、OpenVINO、WinML 定义兼容矩阵。
3. Settings 中提供“一键检测 / 一键下载 / 一键 warmup / 一键测试生成”。
4. 任务路由上先让本地模型承担私密低复杂任务，复杂规划仍允许混合模式走云端。

### 6.5 跨端控制

Lengrvis 已经有移动审批和远程桌面后端，下一步应分三层推进：

- P1：手机端看任务状态、接收审批通知、查看 dry-run preview、批准/拒绝、回到桌面继续。
- P1.5：手机端查看桌面截图流，只允许只读观察和任务中断。
- P2：手机端远程输入，必须默认关闭、显式配对、短期 token、可撤销、逐动作审批或会话级权限。

不要一开始追求“完全远控”。对个人 OS Agent 来说，先把“人在外面也能放心批准/拒绝电脑任务”做顺，会更快形成可信闭环。

### 6.6 文件索引与环境感知

FileWatcher 已接入启动路径，这是优势。下一步应围绕“个人知识库”打磨：

- 增量索引状态可视化：多少文件已索引、失败多少、最近更新时间。
- 图片语义升级：从 hash/k-means 走向 vision describe + embedding + 聚类标签。
- 文档引用体验：QA 结果必须显示来源文件、页码/段落、置信度。
- 环境建议：当用户打开某类 App 或文件变化时，只给高置信建议，避免打扰。

### 6.7 浏览器与 App 自动化

浏览器自动化应优先补安全和稳定，而不是功能数量：

- Prompt injection 检测、网页内容隔离、登录态接管说明。
- 表单提交、发消息、支付、账号设置等动作按风险分级，支付/金融强制手动。
- 浏览器事件和录屏回放，用于用户信任和失败排查。

App 自动化建议定义 App Integration Protocol：

- 描述 app、可执行动作、风险等级、输入 schema、dry-run preview、回滚策略。
- 先做 5 个高频集成：文件管理器、浏览器、Office/WPS、微信/企业微信、邮件/日历。
- 对无法深度 API 集成的 App，采用 UI 自动化 + 视觉校验 + 审批。

## 7. 优先级建议

### P0：把隐私模式变成开箱即用

目标：普通用户安装后，不配置 API key 也能完成本地文件搜索、摘要、系统查询和简单规划。

建议：

- 一键安装/启动 Ollama 或内置本地推理 runtime。
- 推荐并自动下载小型量化模型。
- Settings 显示本地模型状态、模型大小、预计速度、隐私边界。
- 隐私模式不可用时给明确修复按钮，而不只是错误提示。

验收：

- 新机器安装后 10 分钟内可跑本地摘要/文件搜索/简单问答。
- 断网状态下能完成不依赖网页的本地任务。
- 用户能看懂“文件是否上传”。

### P1：补齐产品化闭环

目标：把已有后端能力转成用户能感知、能信任的体验。

建议：

- 移动端从审批列表升级为任务 companion：通知、状态、preview、批准/拒绝、继续任务。
- 文件索引增加 UI 状态和失败恢复。
- 通知服务接入 Electron/系统通知，而不仅是 AgentBus。
- 定义 App Integration Protocol，并做 Office/WPS、微信/企业微信、邮件/日历、浏览器下载管理等样板 Skill。
- 浏览器自动化加入更强网页注入防护和登录接管 UX。

验收：

- 用户在手机上能完成远程审批闭环。
- 文件变更后 5 秒到 30 秒内增量索引可用。
- 至少 3 个真实第三方 App 场景可稳定跑通。

### P2：做平台级能力

目标：追近腾讯同类 OS Agent 和 Microsoft Copilot+ PC 的“OS Agent 感”。

建议：

- 远程桌面：屏幕流、输入、会话权限、撤销、审计、低延迟优化。
- NPU 加速：WinML/DirectML/OpenVINO 真实模型包、兼容矩阵、性能基线。
- 图片库：人像、地点、节日、内容主题聚类。
- Mac 发布：补 backend binary 打包和权限说明。
- PC 操作手机：谨慎评估 scrcpy/Android VM，默认高风险、强审批。

验收：

- 手机端可只读查看任务画面并安全中断。
- 支持至少一种 NPU/加速路径真实生成。
- 图片库能形成用户可理解的自动相册/主题标签。

## 8. 风险与反证

**风险 1：大厂平台能力压制**  
Microsoft 有 Windows 原生入口和 NPU，腾讯有应用生态和消费级分发，OpenAI 有最强通用模型和全球入口。Lengrvis 很难靠“也有 Agent”取胜。

**应对**：强调可私有化、本地可审计、工具权限透明、可插拔模型、长尾 App Skill。

**风险 2：本地模型体验不稳定**  
本地小模型在规划、结构化输出、复杂文档推理上可能不如云端模型。隐私模式如果体验差，反而伤害品牌。

**应对**：明确 hybrid 策略，避免让本地小模型承担超出能力的任务；用本地模型处理隐私内容摘要和检索，云端做非敏感规划。

**风险 3：OS 自动化安全事故**  
文件删除、浏览器提交、远程输入、App 卸载、支付相关任务都可能造成真实损失。

**应对**：保留并强化 R0-R4、dry-run、审批绑定、不可变 preview、回滚和审计；高风险任务默认拒绝或手动交接。

**风险 4：报告可能高估已实现能力**  
部分模块有实现但未达到消费级体验，例如远程桌面默认关闭、ONNX 依赖外部模型、本地模型默认不随包离线可用、移动远控仍缺真实手机/网络/证书信任链证据。

**应对**：所有路线评估都区分“代码路径存在”和“发布级体验完成”。

## 9. 结论

Lengrvis 与市场同类产品的核心差异可以浓缩为一句话：

**Lengrvis 是一个 Windows-first、本机执行、可审计、可扩展、可自托管的个人电脑 Agent 技术底盘；Marvis/Copilot+ PC 更像发布级 OS AI 产品；ChatGPT Agent/Manus/Genspark 更像云端通用任务 Agent；Claude Code 更像开发者工作流 Agent。**

因此，Lengrvis 的下一步不应盲目追逐“万能 Agent”，而应把已有底盘产品化为三个明确卖点：

1. **本地隐私可信**：文件、文档、系统信息优先在本机处理；隐私模式不可用时明确失败，不静默回云端。
2. **电脑任务可控执行**：真实修改都应有 preview、审批、审计、撤销或 handoff 思路。
3. **长尾应用可扩展**：Skill/MCP/App Integration Protocol 支撑个人和企业自定义工作流。

只要 P0 的本地模型开箱即用补齐，P1 的移动审批/通知/文件索引/App 集成产品化，Lengrvis 就能避开和大厂正面拼模型的陷阱，形成“本地可控 OS Agent”的清晰技术路线。

## 10. 资料来源

### 本地仓库

- `README.md`
- `docs/LENGRVIS_PARITY.md`
- `backend/app/agents/orchestrator_agent.py`
- `backend/app/tools/registry.py`
- `backend/app/llm/registry.py`
- `backend/app/llm/local_provider.py`
- `backend/app/llm/onnx_provider.py`
- `backend/app/api/routes_settings.py`
- `backend/app/services/ollama_service.py`
- `backend/app/orchestration/tool_runtime.py`
- `backend/app/policy/policy_engine.py`
- `backend/app/indexer/file_watcher.py`
- `backend/app/api/routes_mobile.py`
- `backend/app/api/routes_remote.py`
- `desktop/src/main/notifications.ts`
- `desktop/src/renderer/components/SettingsPanel.tsx`
- `desktop/src/renderer/App.tsx`
- `mobile/App.tsx`
- `mobile/src/screens/PairScreen.tsx`
- `mobile/src/screens/RemoteScreen.tsx`

### 公开资料

- 腾讯 Marvis 官网：https://marvis.qq.com/
- OpenAI，Introducing ChatGPT agent：https://openai.com/index/introducing-chatgpt-agent/
- OpenAI，Introducing Operator：https://openai.com/index/introducing-operator/
- OpenAI Help Center，ChatGPT agent：https://help.openai.com/en/articles/11752874-chatgpt-agent
- Microsoft Support，Click to Do in Recall：https://support.microsoft.com/en-us/windows/click-to-do-in-recall-do-more-with-what-s-on-your-screen-967304a8-32d1-4812-a904-fad59b5e6abf
- Windows Experience Blog，Windows 11 is the home for AI on the PC：https://blogs.windows.com/windowsexperience/2025/07/22/windows-11-is-the-home-for-ai-on-the-pc-with-even-more-experiences-available-today/
- Windows Experience Blog，Securing AI agents on Windows：https://blogs.windows.com/windowsexperience/2025/10/16/securing-ai-agents-on-windows/
- Windows Experience Blog，Making every Windows 11 PC an AI PC：https://blogs.windows.com/windowsexperience/2025/10/16/making-every-windows-11-pc-an-ai-pc/
- Anthropic，Introducing computer use：https://www.anthropic.com/news/3-5-models-and-computer-use
- Anthropic，Claude Code：https://www.anthropic.com/claude-code
- Manus 官网：https://manus.im/
- Genspark 官网：https://www.genspark.ai/
