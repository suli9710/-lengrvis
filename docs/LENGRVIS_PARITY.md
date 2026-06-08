# Lengrvis OS Agent Parity Roadmap

> Last reviewed: 2026-06-08
> Purpose: keep the roadmap credible by separating code-level capability, demo readiness, hardening work, and not-started work.
> Scope: repository evidence in `backend/`, `desktop/`, `mobile/`, `scripts/`, `docs/qa/`, and `README.md`; public competitors are used only as expectation references, not as a clone target.

## Status Key

- **已实现**: code path exists, is wired into the product surface or backend runtime, and has tests, smoke coverage, or release-gate evidence.
- **可演示**: can be shown in a controlled demo with disposable data, but still needs release-candidate evidence or manual sign-off before broad claims.
- **需要硬化**: meaningful implementation exists, but reliability, UX, security, packaging, or measurement is not yet release-grade.
- **未开始**: no meaningful product path in this repository beyond planning notes.

## Portable GUI Diagnostics Evidence Boundary

As of 2026-06-08, `npm run smoke:portable-first-screen` is automated evidence that the Windows portable launcher can open a visible window, start or connect to the packaged backend on loopback, answer token-authenticated local-only `GET /api/system/diagnostics` from isolated temporary state/data, and use packaged renderer DOM automation to click the read-only "检查电脑状态" entry. The latest evidence is `.tmp\portable-first-screen-smoke\run-20260608-154045-41396-6013e259\portable.status.log`: the read-only entry pass observed packaged renderer `/api/system/diagnostics`, read-only diagnostics copy, `tasks=0`, `runs=0`, `chat messages=0`, and `diagnostic-packages=0` after that click. The same run then filled `帮我检查这台电脑`, observed packaged renderer `POST /api/runs`, and recorded backend read-only/system diagnostics task evidence `task_99963aecac4841d2af25feb2f675c2ad` with `tasks=1`, `runs=1`, `chat messages=0`, and `diagnostic-packages=0`. Record this as packaged natural-language command-dock submission plus read-only/system diagnostics task evidence. It still does not prove clean-machine release-candidate install, real-device validation, platform distribution sign-off, completed task-result sign-off, or a full natural-language agent task completion loop; record those as separate manual release evidence until they are automated.

## Four-Column Roadmap

| 能力域 | 已实现 | 可演示 | 需要硬化 | 未开始 |
| --- | --- | --- | --- | --- |
| 新手开箱与首日任务 | README 顶部已有平台矩阵；正式启动脚本不再现场安装依赖；首屏有 5 个任务模板并显示本机处理、云端边界、审批、回滚、预计耗时。证据：`README.md`、`scripts/start_app.ps1`、`desktop/src/renderer/features/office/OfficeScene.tsx` | `docs/demo-script.md` 提供 60 秒、3 分钟、10 分钟演示路径；`docs/qa/release-gate.md` 要求 template demo path 证据；portable smoke 可验证窗口、backend health、token-authenticated local-only diagnostics、packaged renderer DOM 只读系统检查入口，以及自然语言 command dock 的 `/api/runs` submission + read-only/system diagnostics task evidence | 干净机器候选启动、自然语言 agent 任务进度/结果、completed task-result sign-off、自动更新和完整 crash/update pipeline 仍未闭环；latest portable smoke 已记录 packaged command-dock submission 和后端只读诊断 task evidence，但还不是完整任务结果、clean-machine 或 RC 签收 | 应用商店级安装、更新、崩溃上报和用户指标体系 |
| Agent 编排与工程能力 | Orchestrator、Planner、Supervisor、SafetyReview、OSExecutionEngine、Memory 形成实质编排；File/Document/Computer/App/Browser/Search 是 domain shell agents；38 个 prompt 模板外部化；step graph 支持并行。证据：`backend/app/agents/*`、`backend/app/orchestration/*`、`backend/app/llm/prompts/` | 任务时间线、Task Workspace、信任清单可以展示计划、进度、审批、结果。证据：`desktop/src/renderer/components/TaskTimeline.tsx`、`desktop/src/renderer/features/office/OfficeScene.tsx` | 领域 shell agent 的失败恢复、工具选择质量、用户可读解释还需要从“能执行”提升到“稳定完成任务” | 宣称每个领域都有独立自主推理 Agent；当前不应这样对外表达 |
| 安全、审批与审计 | R0-R4 风险等级、dry-run、审批绑定、HMAC、路径沙盒、R4 阻断、PII 脱敏、审计日志已是核心资产。证据：`backend/app/policy/*`、`backend/app/orchestration/tool_runtime.py`、`backend/app/core/audit.py`、`backend/privacy/redaction.py` | 可演示 reversible action approval、R4 credential/token block、审计记录。证据：`docs/qa/release-gate.md`、`docs/qa/e2e-acceptance-matrix.md` | R2/R3 preview 的用户解释、规则记忆、替代建议、审计导出和高风险 UX 仍需产品化 | 支付、金融、系统级不可逆动作的自动执行；应保持手动交接或拒绝 |
| 文件、文档与知识库 | 授权目录搜索、FTS5、向量 rerank、重复文件检测、PDF/DOCX/XLSX/PPTX/CSV 抽取、离线 OCR、文档摘要/QA/报告已实现。证据：`backend/app/tools/file_tools.py`、`backend/app/tools/document_tools.py`、`backend/app/services/document_service.py`、`backend/app/indexer/*` | 文档 QA 带 citation label，可作为 demo 主线。证据：`docs/demo-script.md`、`docs/qa/e2e-acceptance-matrix.md` | 文档库/图库的消费级 UI、引用页码/段落置信度、索引状态可视化、失败恢复还需打磨 | 接近消费相册的“人像、地点、节日、自动相册”体验 |
| 文件环境感知 | `FileWatcher` 已用 `watchdog` 监听授权目录，debounce 后增量更新 FTS，并在 backend lifespan 中接入 environment stream。证据：`backend/app/indexer/file_watcher.py`、`backend/app/main.py` | 可以在授权目录中演示文件创建/修改后进入索引和环境事件流 | 需要 UI 展示索引进度、最近更新时间、失败文件、重试入口；跨平台边界和大量文件性能仍需验证 | 面向全盘、无授权边界的后台爬取 |
| 本地模型与隐私模式 | 隐私/混合模式会探测 ONNX、Ollama、LM Studio、llama.cpp；未检测到本地后端会明确失败，不静默回退 Mock；后端已有 Ollama status/setup-plan/install/start/pull/install-local-model 和 WebSocket 进度。证据：`backend/app/llm/local_provider.py`、`backend/app/api/routes_settings.py`、`backend/app/services/ollama_service.py` | Settings 已能展示 quick/privacy/hybrid 边界、推荐模型、硬件状态、失败修复动作；Windows 可演示一键安装/启动/拉取流程，取决于真实机器环境。证据：`desktop/src/renderer/components/SettingsPanel.tsx` | 默认 Windows portable/zip/SFX 不捆绑 Ollama 离线模型或 GPU runtime；clean-machine local model smoke 仍需候选版本证据；Settings DOM/screenshot 不是最终布局签收，1366px 宽度下模型边界卡片挤压风险仍需复核；隐私模式可用性仍是 P0 体验短板 | 与发布级端侧模型产品相同的“安装后无需网络也能稳定运行”的默认体验 |
| 硬件加速与端侧推理 | ONNX provider 有 WinML/DirectML/OpenVINO/CPU 检测、warmup/test-generate、健康状态接口。证据：`backend/app/llm/onnx_provider.py`、`backend/app/api/routes_settings.py` | 可展示硬件检测和 unsupported diagnostics | 缺默认量化模型包、设备兼容矩阵、fallback 性能基线、真实 NPU 生成验收 | 默认 NPU 加速、本地多模态大模型包 |
| 浏览器与网页自动化 | Playwright/httpx 只读浏览器工具，navigate/click/fill/submit/wait 写动作，dry-run 和审批链已存在；桌面 browser activity smoke 已纳入 gate。证据：`backend/app/tools/browser_tools.py`、`backend/app/services/browser_activity_runtime.py`、`desktop/scripts/browser-activity-smoke.cjs` | 可演示只读页面理解、截图、低风险表单预览 | Prompt injection 防护、登录态接管说明、真实网站长任务稳定性、支付/账号设置等高风险 UX 仍需硬化 | 对支付、金融、验证码、账号安全设置的自动提交 |
| 系统与 App 自动化 | 系统信息读取、Windows 设置 URI、应用扫描/启动/卸载、Excel COM、UI 自动化工具已存在。证据：`backend/app/tools/system_tools.py`、`backend/app/tools/app_tools.py`、`backend/app/tools/app_excel.py`、`backend/app/tools/ui_automation_tools.py` | 可演示电脑状态检查、大文件查找、Excel 读写、低风险设置打开 | WPS/Office 全套、微信/企业微信、邮件/日历、浏览器下载管理等高频 App 需要稳定协议和样板 Skill | “一句话操作任意 APK/EXE”或 PC 操作手机 App |
| Skill、MCP 与可扩展性 | Skill YAML、Product Manifest、沙盒、安全审查、动态工具注册、MCP registry/OpenAI-compatible provider 已实现。证据：`backend/app/skills/*`、`backend/app/services/skill_service.py`、`desktop/src/renderer/views/SkillsView.tsx`、`backend/app/mcp/*` | 可展示非私有 Skill 样本和 Product Manifest 权限卡；QA matrix 已有 E2E-019；mocked DOM smoke 可证明声明权限与文本推断标签分离 | 真实 release-candidate import、真实生产样本数量、权限解释、预览/回滚/handoff 的一致体验仍需补齐；zip/schema 安全验证不能写成 marketplace 或真实导入通过 | 公共插件市场、签名分发、企业策略管理 |
| 移动 companion 与远程桌面 | Android companion 有配对、内置 `expo-camera` QR 扫码入口、粘贴/手动 fallback、JWT、审批列表/详情、批准/拒绝、任务状态、通知隐私；后端有远程屏幕流、输入 WebSocket、token scope、remote input approval。证据：`mobile/src/screens/*`、`mobile/scripts/mobile-token-smoke.cjs`、`backend/app/api/routes_mobile.py`、`backend/app/api/routes_remote.py`、`backend/app/services/mobile_pairing_service.py` | 手机只读看屏幕、远程输入短授权、结束接管、LAN/TLS 提示可演示；桌面 QR 生成、移动端 PairScreen 扫码源码入口、payload parser 和后端 LAN TLS ready/misconfigured metadata 口径有自动/source smoke 证据。证据：`mobile/src/screens/RemoteScreen.tsx`、`docs/qa/e2e-acceptance-matrix.md`、`backend/tests/test_lan_transport_security.py` | 真实手机/模拟器扫码配对、网络穿透、锁屏权限、延迟、实际 HTTPS/WSS 服务路径、设备侧证书信任链、移动端继续/发起任务仍需产品化；源码 smoke 不能替代真实设备证据 | iOS companion、手机完整接管 PC、PC 操作手机 App |
| 通知与任务唤醒 | 后端 `NotificationService` 会通过 AgentBus 发布通知；桌面 `NotificationBridge` 监听 `/ws/notifications` 并投递 Electron Notification；移动端审批通知默认隐藏敏感正文。证据：`backend/app/services/notification_service.py`、`desktop/src/main/notifications.ts`、`mobile/src/notifications.ts` | 可演示桌面/移动审批通知基础路径 | 通知偏好、系统权限引导、失败重连可视化、跨平台行为一致性需要硬化 | 完整 OS 通知中心和多设备通知路由策略 |
| 打包、发布与 QA gate | `qa:gate`、`release:check`、`release:quick`、runnable backend smoke、portable launcher/backend diagnostics smoke、portable renderer DOM read-only entry smoke、portable natural-language `/api/runs` submission/task-evidence smoke、dependency lock verification、release safety gate 已整理。证据：`package.json`、`docs/qa/release-gate.md`、`docs/qa/e2e-acceptance-matrix.md`、`scripts/portable_first_screen_smoke.ps1` | Windows artifact structural + backend runnable smoke + portable 窗口/health/local-only diagnostics + packaged renderer read-only click path + packaged natural-language command dock submission/task evidence 可演示；special offline Ollama release 有显式 `-RequireBundledOllama` 验证入口 | 真实机器候选启动、packaged GUI 自然语言任务进度/结果、completed task-result sign-off、macOS DMG、Android 分发证据仍需人工或平台验收；当前 packaged natural-language smoke 只记录 submission + backend read-only/system diagnostics task evidence，不证明完整任务结果或 RC 签收 | iOS 分发、自动更新、完整 crash/diagnostic pipeline |

## Differentiated Positioning

Lengrvis should not be positioned as a replacement for Marvis, Copilot, ChatGPT Agent, Claude Code, Manus, or Genspark. The credible lane is:

1. **本机 OS agent**: focus on the user's own Windows files, apps, settings, browser, approvals, and remote desktop, not a cloud-only workspace.
2. **可审计**: every meaningful local-state change should have risk level, preview, approval, audit event, and rollback/handoff thinking.
3. **可扩展**: Skill/MCP/App Integration Protocol should let users and teams add long-tail app workflows without hard-coding every app.
4. **可自托管**: OpenAI-compatible providers plus local model probes allow private, hybrid, and cost-controlled deployments.

This is narrower than the broadest consumer OS-agent promise, but it is a sharper product story: trusted local execution rather than imitation of a specific competitor's surface.

## Roadmap Priorities

| Priority | Product move | Evidence target |
| --- | --- | --- |
| P0 | Make privacy/local mode a complete first-run path on Windows without silent cloud fallback. | clean-machine local model readiness/smoke, Settings setup flow, `docs/qa/release-gate.md` handoff entry |
| P0 | Keep release claims tied to gate evidence. | `npm run qa:gate`, `npm run release:check`, explicit residual risks for any skipped P1 |
| P1 | Turn mobile from approval list into trusted task companion. | source-smoked QR scan/paste payload flow, real-device pairing evidence, mobile approval round trip, read-only screen, remote input grant revoke |
| P1 | Productize file/document knowledge base. | index status UI, document citation source details, template demo path |
| P1 | Define App Integration Protocol and migrate real samples. | 3 non-private Skill/App samples with Product Manifest cards |
| P2 | Ship platform-grade acceleration and cross-device control only after safety gates are strong. | one real NPU/accelerated generation path; audited remote screen/input session; macOS artifact evidence |

## Verification Entry Points

- Fast gate: `npm run qa:gate`
- Dependency gate when manifests or requirements change: `npm run deps:verify`
- Windows release candidate gate: `npm run release:check`
- Focused mobile/remote evidence: `python -m pytest backend/tests/test_mobile_pairing.py backend/tests/test_lan_api_guard.py backend/tests/test_remote_desktop.py -q`
- Skill/App evidence: `python -m pytest backend/tests/test_app_skill_protocol.py backend/tests/test_app_skill_packages.py backend/tests/test_skill_loader.py -q`
- Local model evidence: backend provider/settings tests plus manual Settings local model readiness or exact blocked reason

## Copy Guardrails

Do say:

- “Windows-first local OS agent with auditable execution.”
- “Local/private mode is explicit: when no local backend is available, it fails clearly instead of silently using cloud.”
- “Mobile remote input requires short-lived authorization and still produces desktop-side approval.”
- “Skill/MCP gives a path for long-tail app integrations.”

Do not say:

- “fully replaces Marvis/Copilot/ChatGPT Agent.”
- “全面领先.”
- “offline local model is bundled by default.”
- “phone can fully control the PC in production.”
- “NPU acceleration is available by default.”
