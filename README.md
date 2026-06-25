# Lengrvis

Lengrvis 是一款面向 Windows 的本地 AI 电脑助手。它把自然语言任务拆解为可审计的执行步骤，调用本机文件、文档、浏览器和系统工具，并在写入文件、删除内容或修改系统状态前展示预览、请求确认、保留回滚信息。

产品设计以“本地优先、可控执行、可追溯”为核心：适合整理文件、总结文档、检索本机资料、检查电脑状态、辅助浏览器操作，以及在需要时把审批和任务监督延伸到 Android companion。

**仓库：** [github.com/suli9710/-lengrvis](https://github.com/suli9710/-lengrvis)

**当前版本：** [v0.1.0](https://github.com/suli9710/-lengrvis/releases/tag/v0.1.0)（Windows portable zip / 自解压包 / 独立后端可执行文件）

| 组件 | 技术栈 |
| --- | --- |
| 桌面端 | Electron · React · TypeScript · Vite · Zustand |
| 后端 | Python 3.12 · FastAPI · SQLite · Playwright |
| 移动伴侣 | Expo · React Native（Android Preview） |
| 自动化验证 | GitHub Actions（依赖与 SBOM、pytest、golden tasks、typecheck、smoke、IPC/Skill/MCP/settings security） |

## 平台支持矩阵

| 平台 | 状态 | 当前交付 | 已知限制 |
| --- | --- | --- | --- |
| Windows 桌面 | Supported | Electron 桌面、FastAPI 后端、Windows portable zip、自解压包、任务工作台、审批、文件/文档/系统工具 | v0.1.0 产物未签名；clean-machine、升级/回滚、真实设备联动仍需独立验收。 |
| Android Companion | Preview | 配对、移动审批、任务监督、暂停/继续/取消、只读屏幕流、受控远程输入授权 | 适合内部预览和联调；真机 LAN/WSS、证书信任路径和应用商店分发尚未完成。 |
| macOS 桌面 | Preview | macOS 后端构建脚本与 DMG 脚本 | 需要在 macOS 主机完成打包、签名和 notarization 验证。 |
| iOS Companion | Planned | 暂不交付 | Android companion 稳定后再排期。 |

## 安装与快速开始

1. 打开 [Releases](https://github.com/suli9710/-lengrvis/releases)，下载 `Lengrvis-0.1.0-win-portable.zip` 或 `Lengrvis-0.1.0-x64-self-extracting.exe`。
2. 使用 portable zip 时，解压后运行 `Lengrvis.exe`；使用自解压包时，双击后会释放到本机用户目录并启动应用。
3. 启动成功后，桌面窗口会显示任务入口。你可以直接从整理下载目录、总结本地文档、查找大文件、检查电脑状态、文档问答等场景开始。
4. 每个会改变本机状态的操作都会先进入预览和审批流程；文件写入和编辑会记录回滚信息，便于撤销。
5. 如果你下载的是源码或 Git 仓库，请跳到“源码开发 setup”。源码模式需要安装 Python、Node 和前端依赖，不属于普通用户启动路径。
6. 完整上手步骤、FAQ 与故障排查见 `docs/user-guide.md`。

## 配置、隐私与诊断

- AI Provider、隐私模式、本地模型、硬件加速和手机配对优先在桌面“设置”中完成。普通用户不需要手动编辑 `.env` 或 `config.yaml`。
- “设置 → 套餐与授权”展示当前 Free / Pro / Team 能力、云端额度、许可证主体与到期时间；离线许可证可在本机验签。在线购买、订阅、退款自动降级和吊销同步仍在建设中。
- “设置 → 本机数据与隐私”提供本机数据删除入口。删除前需要输入确认短语并再次通过系统确认；随后清除任务、对话、录屏、配对、索引与已导出诊断包，保留防篡改审计链并记录删除事件。日志目录仍需手动清理。
- “系统信息”用于查看桌面版本、后端版本、服务状态、日志目录、只读系统诊断和本地发布说明。
- “导出诊断包”用于支持排查。诊断包会写入本机数据目录下的 `diagnostic-packages`，包含版本、服务状态、本机范围摘要、网络接口、进程、启动项和最近失败统计；导出过程会尽量脱敏路径、用户名、密钥、任务正文、设备名、配对码、grant id 和模型路径。
- 诊断包默认不是可公开材料。对外分享前，仍应检查是否包含不该外发的路径、日志片段或组织信息。
- 应用打不开时，可运行 `Start-Lengrvis-Debug.cmd` 查看已脱敏的最近启动日志摘要；完整日志通常位于仓库 `logs` 目录或应用数据目录的 `logs` 目录。

## 任务证据与录屏隐私

- 任务步骤录屏/截图默认不采集。只有你明确开启任务录屏（开发/测试环境可用 `LENGRVIS_TASK_RECORDING_ENABLED=true`，测试可用 `LENGRVIS_TASK_RECORDING_FORCE=1`）时，才会把截图作为本机 task recording 写入数据目录；不要在含私人资料的 profile 上随手开启。
- 任务 timeline、replay、任务列表、agent messages、safety reviews、progress 和 explain 接口只返回 redacted summary、状态、计数和边界标签。它们不会返回截图 URL、截图文件名、recording id、raw tool args/result、隐藏 prompt、任务 metadata、review reasons 或文件正文。
- 诊断包导出只保留 task recording 的状态边界，例如是否开启和默认 opt-in 策略；不会把录屏图片、截图文件名或 task recording 路径放进支持包。原始截图只能通过显式本机文件名路线读取，不能从公开 timeline/replay 自动发现。

## 产品定位

Lengrvis 的目标不是替代用户直接控制电脑，而是在用户授权范围内把重复、跨工具、需要上下文的本机任务变成可预览、可批准、可回滚的执行流程。

桌面端围绕消费级电脑助手场景设计，提供单句任务入口、隐私 / 混合 / 效率三种模式、文件/文档/图片/系统/应用/网页能力、移动端审批与屏幕查看入口，以及 Agent 进度和安全审批视图。当前版本已经具备 Windows 本地运行、打包分发和核心安全边界；本地模型开箱即用、跨端分发、深度应用集成、签名安装包和真实设备验收仍在持续完善。

## 架构

```text
lengrvis/
├── backend/app/         FastAPI 后端、Agent、策略、工具、索引、服务
├── backend/tests/       pytest 契约测试（~135 文件）与 golden tasks
├── desktop/src/         Electron main/preload + React 渲染层
├── desktop/scripts/     Playwright UI smoke（14 项，本地 qa:gate 覆盖）
├── mobile/              Expo Android 伴侣（配对、审批、远程监督）
├── scripts/             PowerShell 启动、开发 setup、测试和打包
├── test_data/           授权目录、策略和隐私测试数据
└── docs/                用户手册、QA 门禁、发布与合规文档
```

运行时流程：

```text
用户 -> OrchestratorAgent -> PlannerAgent -> SafetyReviewAgent
    -> domain shell agent act() / PolicyEngine / ToolRuntime
    -> SafetyReviewAgent -> 下一步 / 审批 / 完成
```

## 已实现能力

### 核心架构
- 自然语言任务提交：桌面端或 `POST /api/chat`。
- **Agent 分层口径**：实质编排/推理组件是 Orchestrator、Planner、Supervisor、SafetyReview、OSExecutionEngine 和 Memory；PolicyEngine、ToolRuntime、审批绑定、路径沙盒和 schema validation 负责确定性安全与执行约束。
- **Domain shell agents**：File、Document、Computer、App、Browser、Search 等领域 Agent 主要提供 owner/prompt/allowed tools 边界，并共享 `BaseAgent.act()`；多数正常成功路径会先走 deterministic fast path（tool owner、required args、schema、dry-run/approval 约束），不是每一步都调用 LLM 做自主推理。
- **LLM 介入边界**：Planner/Supervisor、文档摘要/问答/报告、失败恢复、复杂改参或 fast path 无法确定时会调用 structured LLM；安全审查中低风险消息优先走确定性快速通道，高风险或模糊场景再进入完整审核。
- **Step 级并行执行**：Plan 中无依赖的步骤通过 `asyncio.gather` 并发执行，有依赖的步骤按拓扑排序串行。
- **38 个外部化 Prompt 模板**：Agent system prompt 和 LLM 任务模板以 `.md` 文件存放在 `backend/app/llm/prompts/` 目录，可独立调整。

### LLM 与推理
- OpenAI-compatible 真实 AI 接入：`base_url`、`api_key`、`model`、`wire_api` 可配置；支持 `chat/completions` 与 `responses` 两种 OpenAI 格式。
- OpenAI-compatible `base_url` 可以填写裸域名或完整 `/v1` API base，例如 `https://api.example.com` 会自动归一化为 `https://api.example.com/v1`；已有 `/v1` 或自定义代理 path 不会重复改写。
- 三模式 Provider 路由：默认效率（云端）/ 隐私（本地）/ 混合（按任务类型分流）。
- 只有隐私模式或混合模式的本地任务会探测 Ollama、LM Studio、llama.cpp-compatible server；未检测到本地 LLM 时明确报错，不再静默回退 `MockProvider`。
- `MockProvider` 仅用于开发、测试和非隐私路径的演示兜底。
- ONNX Runtime Provider 框架（WinML / DirectML / OpenVINO / CPU）。
- 上下文管理运行时：所有 `get_provider()` 返回的 LLM provider 都会先经过统一 ContextManager，按 `tool result budget -> history snip -> micro-compact -> session memory -> auto-compact -> LLM call -> prompt-too-long reactive retry` 控制模型可见上下文；原始 AgentBus/DB 历史不删除。
- Token 预算配置：`LENGRVIS_MODEL_CONTEXT_WINDOW`、`LENGRVIS_MODEL_AUTO_COMPACT_TOKEN_LIMIT`、`LENGRVIS_CONTEXT_*`。默认保留输出预算，接近阈值时自动摘要旧消息并保留最近消息尾部；中文/ CJK 文本使用更密的 token 估算，避免过早触顶或迟迟不触发 auto-compact。

### 安全
- 风险等级：`R0_READ_ONLY`、`R1_OPEN_ONLY`、`R2_REVERSIBLE_MODIFY`、`R3_DESTRUCTIVE_OR_SYSTEM`、`R4_FORBIDDEN_OR_HANDOFF`。
- R2/R3 操作会生成 dry-run 预览和审批记录。
- R4 请求会直接拒绝，例如读取浏览器 cookie、token、密码。
- 路径沙盒：拦截符号链接逃逸、`..` 穿越、系统敏感路径。
- **SafetyReview 批量审查**：低风险消息走确定性快速通道，高风险消息批量送 LLM 审核。
- 全链路审计日志 + 自动 PII 脱敏。

### 文件与文档
- 授权目录文件搜索、FTS5 全文索引（含 CJK trigram 迁移）、重复文件检测。
- **向量语义搜索**：FTS5 候选召回 → Embedding rerank（BLOB 存储）→ cosine similarity → 按文件折叠。
- **文档 AI**：LLM 驱动的摘要（map-reduce 分块）、问答（chunk 检索 + 引用）、报告生成，含 extractive fallback。
- 文档文本提取：PDF / DOCX / XLSX / PPTX / CSV。
- **离线 OCR**：本地 Tesseract → 元数据 OCR → 云 vision fallback；PDF 图片自动 OCR。
- 文件/应用/图片聚类（k-means + hashing trick）。

### 工具与集成
- 浏览器自动化：只读（Playwright + httpx fallback）+ 写操作（navigate / click / fill / submit / wait + dry_run）。
- 系统信息读取：psutil / winreg / 磁盘 / 电池 / 启动项。
- 应用扫描 + MSI 卸载。
- Excel COM 自动化（status / read / write_cell）。
- MCP 客户端 + Registry（JSON-RPC 2.0 over HTTP）。
- 视觉工具（describe / OCR / compare）。

### 扩展性
- **Skill 包系统**：声明式 `skill.yaml` 格式 + 安全审查（R4 阻断 / 路径逃逸 / 敏感 header 检测）+ Python / Shell 沙盒执行 + 动态工具注册。
- **Skill 签名边界**：`skill.yaml` 可声明 Ed25519 签名；当 `LENGRVIS_SKILL_TRUSTED_PUBLIC_KEYS` 配置了对应 `key_id` 的公钥时，manifest digest 或签名不匹配会 fail-closed，未签名或未信任 key 只作为审计/发布证据状态记录。
- **定时调度器**：croniter + async tick + 真实任务执行。
- **长期记忆**：MemoryAgent（embed + cosine + DB + TTL + tags）。
- **WebSocket 实时推送**：`/ws/tasks/{task_id}` 实时 Agent 消息流。
- 回滚工具（逆序重放 rollback_info）。
- 状态机审计/严格模式（默认审计同步，strict 模式非法转移抛错）。

## 源码开发 setup

正式发布包不需要执行本节；普通用户解压完整发布包后直接双击 `启动 Lengrvis.cmd`。只有从源码或 Git 仓库运行时，才需要先安装开发依赖：

```powershell
.\scripts\setup_dev.ps1
```

`setup_dev.ps1` 会创建 `.venv`、安装 Python 开发依赖，并按 `desktop/package-lock.json` 安装桌面/前端依赖。

可选：安装 pre-commit 钩子（backend 使用 ruff 格式化与 lint）：

```powershell
python -m pip install pre-commit
pre-commit install
```

如需手动排查，等价命令是：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -r requirements-dev.txt
npm --prefix desktop ci
```

开发者可选真实 AI 配置（普通用户请在桌面“设置”里完成配置，不要手动编辑 `.env` 或 `config.yaml`）：

```powershell
Copy-Item .env.example .env
notepad .env
```

`.env.example` 使用发布安全默认值，不会自动启用 MockProvider。仅做本地开发演示且没有真实 LLM 时，可把 `.env.development.example` 中的覆盖项追加到本机未提交的 `.env`；不要将这些开发覆盖用于打包或发布。

设置：

```text
LENGRVIS_PROVIDER_NAME=openai_compatible
LENGRVIS_BASE_URL=https://api.openai.com/v1
LENGRVIS_API_KEY=your-key
LENGRVIS_MODEL=gpt-4o-mini
LENGRVIS_WIRE_API=chat_completions
```

OpenAI-compatible 网关也可以写裸域名：

```text
LENGRVIS_BASE_URL=https://api.example.com
```

运行时会自动请求 `https://api.example.com/v1/chat/completions`。如果网关支持 OpenAI Responses API，可改为：

```text
LENGRVIS_WIRE_API=responses
```

`LENGRVIS_API_KEY`、`LENGRVIS_JWT_SECRET` 等敏感值应通过 `.env`、环境变量或外部配置提供，不要提交到仓库，也不要通过 Settings API 持久化。

不配置 `LENGRVIS_API_KEY` 时，效率/混合模式只有在开发者显式设置 `LENGRVIS_ALLOW_MOCK_FALLBACK=true` 后才会使用 `MockProvider` 做演示。隐私模式始终需要真实本地 LLM 后端。

## 运行

已完成源码 setup 后，可以启动完整应用：

```powershell
.\Start-Lengrvis.cmd
```

也可以分开启动开发服务。

启动完整后端：

```powershell
python -m uvicorn backend.main:full_app --reload --host 127.0.0.1 --port 8000
```

`backend.main:app` 是 Guardian 瘦身入口；桌面端自启动后端时会注入 `LENGRVIS_FULL_BACKEND=1`。手动开发完整功能时请使用 `backend.main:full_app`。

启动桌面端：

```powershell
npm --prefix desktop run dev
```

桌面端默认连接 `http://127.0.0.1:8000`。

## 测试

```powershell
.\scripts\run_tests.ps1          # backend pytest（xdist）+ desktop/mobile typecheck + mobile smokes
npm run qa:gate                  # 上述 + desktop 全量 smoke（14 项）
npm run golden:gate              # golden tasks 报告（≥95% 通过率）
```

主测试入口会运行 backend pytest、desktop TypeScript typecheck、mobile TypeScript typecheck，以及 mobile token WebSocket、task companion、remote-input grant、wakeup contract 和 Android back navigation smokes。
这些 mobile smoke 都是本地行为桩/客户端契约证据，避免发布门禁漏掉移动任务监督、远程输入授权、唤醒合同和返回导航边界；它们不等同于真机 LAN/WSS 或证书信任路径验收。

**CI 与本地差异：** `.github/workflows/ci.yml` 在 push/PR 上跑 hygiene、deps:verify、SBOM 生成、backend pytest、golden gate、desktop/mobile typecheck、desktop behavior smokes、mobile smokes 和 `security:extensions` IPC/Skill/MCP/settings 门禁；末尾的 `release-evidence` job 会自动生成并上传唯一当前证据文件 `docs/release/current-release-evidence.md`，汇总 commit、日期、命令、机器环境、测试结果、失败项、豁免项、手工验收项、负责人签名和 artifact 链接。CI 还会上传 `current-sbom` 和 `extension-security-gate` artifacts。CI **不包含** `release:check`、portable GUI smoke、clean-machine/真实设备人工验收。完整发布前请本地跑 `npm run release:check`，需要 GUI/portable 证据时另跑 `npm run smoke:portable-first-screen`。每周 SCA 见 `.github/workflows/security-audit.yml`。

### 测试结果来源

README 不再维护手写的“最近一次测试结果”、pass 数、失败用例名或本轮目标结果。当前自动化结果的唯一来源是 CI 末尾 `release-evidence` job 生成并上传的 `current-release-evidence` artifact；仓库内同结构文件是 `docs/release/current-release-evidence.md`。

本地或候选版本需要刷新同结构摘要时，运行下面的命令；它只更新 evidence 摘要，不是 release sign-off，也 not a pass：

```powershell
npm run evidence:current-release
```

定向验证、历史对照和人工验收材料必须附 exact command/log，并记录到 release evidence artifact、`docs/qa/release-gate.md` 的 handoff、或对应 `.tmp` evidence 输出中；不要把测试结果复制回 README。

端到端 QA 和发布门禁见：

- `docs/qa/e2e-acceptance-matrix.md`：backend / desktop / mobile 的 P0-P2 验收矩阵。
- `docs/qa/release-gate.md`：发布前 `qa:gate`、产物 `release:check`、人工 P1 验收和 stop-ship 条件。

快速发布前置门禁：

```powershell
npm run qa:gate
```

黄金任务回归（≥30 条真实任务的 E2E 回归，断言计划/风险/审批/文件副作用/工具产物，已包含在 backend pytest 与 `qa:gate` 中；单命令报告与 95% 通过率守门）：

```powershell
npm run golden:gate
```

数据集与证据边界见 `docs/qa/golden-tasks.md`：机器通过率是版本回归自证，不等同真人结果质量评分（成功率/可读性/返工率）签收；真人评审打包入口仍是 `npm run evidence:result-quality-review`。

依赖漏洞扫描（desktop/mobile `npm audit` + backend `pip-audit`，高危即失败；漏洞披露流程见根目录 `SECURITY.md`）：

```powershell
npm run audit:deps
```

市场化发布状态（非严格模式只校验结构并报告阻塞；严格模式在主体、支付、法务、授权运营或售后未闭环时失败）：

```powershell
npm run market:readiness
npm run market:readiness:strict
```

离线许可证管理入口：

```powershell
npm run license:admin -- --help
```

商业套餐能力的唯一矩阵见 `docs/pricing.md`，付费上线阻塞项见 `docs/business/market-readiness.md`。工程 RC 通过不等于可以收款或公开承诺付费服务。

依赖锁与 SBOM（Python transitive lock + desktop/mobile npm lock + CycloneDX JSON）：

```powershell
npm run deps:verify
npm run sbom:generate
```

已有 Windows 发布产物时再跑产物门禁：

```powershell
npm run release:check
```

证据 helper 新手入口（只整理材料，不产生签收）：

```powershell
npm run evidence:current-release # CI/local current summary; not a pass
npm run security:extensions # IPC + Skill/MCP + settings gate; not release signoff
npm run evidence:release # template only; not a pass
npm run evidence:rc-handoff -- -CandidateCommit "<commit SHA>" -BuildId "<build id>" -Platform "<platform>" -ArtifactLabel "<redacted artifact label>" -GateCommand "<exact command>" -GateExit "<exit code/status>" -StrictStateSource "<strict state source>" -ManualP1Check "<check/status/artifact label>" -Waiver "<none or owner/reason/expiry/follow-up>" -ResidualRisk "<risk/owner/follow-up>" # template only; not a pass
npm run evidence:result-quality-review -- -TaskArtifactLabel "<task/run/status-log label>" -ResultArtifactLabel "<user-visible result/artifact label>" -UserVisibleResultReview "<review notes>" -SourceArtifactCheck "<source/artifact check>" -NextStepActionabilityCheck "<next-step/actionability check>" -Reviewer "<reviewer label>" -ReviewedAtUtc "<UTC timestamp>" -BlockedReason "none" # template only; not a pass
npm run evidence:mobile-lan-wss # prerequisite template only; not real-device pass
npm run android:release-gate -- -PreflightOnly # source/config check only; not APK or real-device pass
npm run evidence:android-real-device-template -- -ArtifactLabel "<redacted apk label>" -ArtifactSha256 "<sha256 if known>" -DeviceLabel "<redacted device label>" -BackendBuildLabel "<redacted backend/build label>" # template only; not real-device pass
npm run android:release-gate -- -ArtifactPath "<qa apk path>" -RealDeviceEvidencePath "<reviewed android evidence json>" # strict gate; requires APK + real-device evidence
npm run evidence:local-model-template -- -EvidenceMode clean-machine -Runtime "<runtime>" -RuntimeVersion "<version>" -Model "<model>" -ModelVersion "<version>" -BlockedReason "<redacted blocked reason>" # template only; not a pass
npm run evidence:diagnostics-review # template only; not public-safe/signoff
npm run evidence:distribution-template -- -InstallerArtifactLabel "<redacted installer label>" -InstallerSha256 "<sha256>" -SigningSubject "<cert subject>" -SigningThumbprint "<thumbprint>" -CleanWindowsMachineLabel "<clean Windows label>" -UpgradeFromVersion "<version>" -UpgradeToVersion "<version>" -UpgradeOutcome "<outcome>" -RollbackOutcome "<outcome>" -RealDeviceEvidenceLabel "<evidence label>" -Reviewer "<reviewer>" # template only; not a pass
```

这组顶层 npm 命令只是包装现有 helper：`evidence:current-release` 写入唯一当前证据文件 `docs/release/current-release-evidence.md`，CI 会把它作为 `current-release-evidence` artifact 上传；`sbom:generate` 生成 CycloneDX SBOM，CI 会把它作为 `current-sbom` artifact 上传；`security:extensions` 跑 IPC policy/openExternal smoke、Skill Ed25519 签名验证/权限/升级 diff 测试、MCP schema/SSRF 测试和敏感设置 server-side enforcement 测试，CI 会把输出作为 `extension-security-gate` artifact 上传；`evidence:release` 生成 release evidence packet 索引，`evidence:rc-handoff` 只整理候选 commit/build、platform、artifact label、gate command/exit、strict state source、manual P1、waiver 和 residual risk 的 handoff 模板字段，`evidence:result-quality-review` 只整理自然语言结果质量 review checklist，`evidence:mobile-lan-wss` 是无手机/无真 WSS 的 prerequisite preflight，`android:release-gate -PreflightOnly` 只检查 Android source/build 配置，`evidence:android-real-device-template` 只生成 fail-closed 真机证据模板，严格 `android:release-gate` 需要真实 APK 和已复核的手机/模拟器远控证据，`evidence:local-model-template` 只填 clean-machine handoff 模板字段，`evidence:diagnostics-review` 只整理诊断包外发复核模板/状态，`evidence:distribution-template` 只整理 clean Windows、签名安装包、升级、回滚、真实设备和 reviewer 字段。输出只能作为 evidence/template/preflight/inventory 记录，不是 clean-machine pass、real-device pass、signed-installer pass、upgrade/rollback pass、`public_safe=true`、public-safe/signoff、result-quality signoff、RC signoff、发布签收或 completed task-result signoff；即使模板字段都填完，也必须附上完整 gate 日志、人工 P1 证据、waiver/risk 处理记录，并由 release owner 明确人工批准后，才可以进入 RC 或发布签收。

新手只看这张缺口表即可，不需要额外流程：

| 看到的 helper/preflight 输出 | 不能称为 | 下一步真实证据 |
| --- | --- | --- |
| `npm run evidence:current-release` 或 CI artifact `current-release-evidence` | release signoff、RC signoff、人工验收完成 | 用它核对本次 CI 的 commit、命令、机器环境、测试结果和失败项；再补齐手工验收、waiver/residual risk 和 release owner 签名。 |
| `npm run sbom:generate` 或 CI artifact `current-sbom` | vulnerability pass、license approval、package provenance/signature proof | 用它核对候选 commit 的 Python/npm component inventory；漏洞、license、provenance 和 release owner review 仍需单独证据。 |
| `npm run security:extensions` 或 CI artifact `extension-security-gate` | release signoff、真实第三方 Skill/MCP 审批完成、签名包发布通过 | 用它核对 IPC policy/openExternal、Skill Ed25519 签名验证/权限/升级 diff/audit、MCP schema/SSRF 和敏感设置 server-side enforcement 的机器门禁；真实 release signing key、第三方 MCP owner policy 和候选 profile 审计链仍需单独证据。 |
| `npm run android:release-gate -- -PreflightOnly` | Android release pass、APK install pass、real-device pass | 生成 QA APK，安装到目标 Android/模拟器，附上已复核的 camera QR、HTTPS/WSS、证书信任、远程屏幕/输入、revoke/expiry 和 artifact redaction evidence，再跑严格 `android:release-gate`。 |
| `npm run evidence:android-real-device-template` | Android real-device pass、remote-control pass | 把它当成 `android-real-device-evidence.redacted.json` 的 fail-closed 起点；template only, not real-device pass；只有真实 APK 安装、真机/模拟器 WSS、证书信任、输入审批和脱敏复核都完成后，才能把对应字段改成 passed/true。 |
| `npm run evidence:mobile-lan-wss` | real-device LAN/WSS pass | 按 `real-device-evidence-checklist.redacted.md` 在真实手机/模拟器上补 camera QR、approval WSS、remote screen WSS、remote input WSS、设备证书信任和截图/日志复核。 |
| `npm run evidence:local-model-template` | clean-machine local model pass | 在干净机器或干净 profile 上记录 artifact/build/profile、runtime/model/version、install/start/pull/task-smoke outcome，或记录明确 blocked reason。 |
| `npm run evidence:diagnostics-review` | `public_safe=true`、可外发诊断包、发布签收 | 对实际导出的诊断包做人工内容复核，记录包路径 label、日志/路径/task/model/device 检查、reviewer、timestamp、decision 和 blocked reason。 |
| `npm run evidence:distribution-template` | clean Windows pass、signed installer pass、upgrade pass、rollback pass、real-device pass、release signoff | 在干净 Windows、签名安装包、升级/回滚和真实设备实际跑完后，把 artifact label、hash、证书、版本、outcome、reviewer 和日志链接填进模板；模板本身保持 fail-closed。 |
| `npm run evidence:rc-handoff` 或 `npm run evidence:release` | RC signoff、release signoff | 命令输出 not a pass/signoff；补齐 candidate commit/build/platform、完整 gate 日志、manual P1、waiver/residual risk 处理，并由 release owner 单独批准。 |

生成 release packet 后，先打开 `.tmp\release-evidence-packet\...\release-evidence-packet.redacted.md` 给新人看缺口，再逐项处理 `release_readiness_blockers`：clean-machine local model、真实设备 LAN/WSS、自然语言结果质量、诊断包实际内容复核和 RC handoff。所有 blocker 都有对应证据并完成 release owner 人审签收前，不要打 tag、发布、公告或对外共享诊断包。

发布候选若需要收集打包 GUI 首屏和只读任务入口证据，再跑 `npm run smoke:portable-first-screen`，并把该次命令、退出码和输出目录记录到 current release evidence、RC handoff 或对应 QA packet；README 不记录某次本地运行目录或通过结果。该证据只覆盖 packaged command-dock 提交和只读任务证据，不能替代 clean-machine、真实设备、人工 RC sign-off 或 completed task-result sign-off。诊断包外发前的人工内容复核也只是实际包内容检查，不是 `public_safe` 批准、clean-machine/RC sign-off 或发布签收；相关 helper/自动测试只能作为模板或契约证据。移动/LAN 演示的 TLS 仅按显式设备信任路径记录，不代表系统级证书链已完成。

## 打包

后端 binary：

```powershell
.\scripts\build_backend.ps1
```

产物：`dist\backend.exe`，Electron Builder 会打进 `resources\backend\backend.exe`。

macOS 后端 binary 需要在 macOS 主机上构建，PyInstaller 不支持从 Windows 交叉产出 macOS 可执行文件：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -U pip
python3 -m pip install -r requirements-dev.txt
bash scripts/build_backend_mac.sh arm64
```

可选架构参数为 `arm64`、`x86_64`、`universal2`；也可以用 `LENGRVIS_BACKEND_TARGET_ARCH=arm64 bash scripts/build_backend_mac.sh`。产物：`dist/backend`，Electron Builder 会打进 `Lengrvis.app/Contents/Resources/backend/backend`。

桌面端 installer（Electron Builder）：

```powershell
.\scripts\build_desktop.ps1
```

Windows portable 目录、portable zip 和自解压包由完整构建入口生成：

```powershell
.\scripts\build_all.ps1
```

默认产物为 `dist\Lengrvis-win-portable`、`dist\Lengrvis-win-portable.zip` 和 `dist\Lengrvis-<version>-x64-self-extracting.exe`（版本号唯一来源是 `desktop\package.json`）。发布到自定义目录时，这些参数会贯穿 backend、portable、zip、SFX 和最终验证：

```powershell
.\scripts\build_all.ps1 -DistDir release\win -PortableDir release\win\Lengrvis-win-portable -PortableZip release\win\Lengrvis-win-portable.zip -SelfExtractingExe release\win\Lengrvis-0.1.0-x64-self-extracting.exe
```

代码签名与自动更新（公开发布通道）：

- **签名**：本地 `npm --prefix desktop run dist` 不签名（仅内部分发）。持 OV/EV PFX 证书时设置 `WIN_CSC_LINK` / `WIN_CSC_KEY_PASSWORD` 环境变量后照常构建即可；走 Azure Trusted Signing 时用 `npm --prefix desktop run dist:signed`（配置见 `desktop/electron-builder.signed.js`，需 `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET`、`AZURE_TRUSTED_SIGNING_ENDPOINT`、`AZURE_TRUSTED_SIGNING_ACCOUNT_NAME`、`AZURE_TRUSTED_SIGNING_CERTIFICATE_PROFILE_NAME`、`AZURE_TRUSTED_SIGNING_PUBLISHER_NAME`）。`dist:signed` / `dist:publish` 会先运行 `verify:signed-build-config` 拒绝空值或 `REPLACE_*` 占位，再校验随包的 `backend.exe` 已在打包前单独用 signtool 或 Azure Trusted Signing 签名。macOS 公开候选使用 `npm --prefix desktop run dist:mac:signed`，要求 `dist/backend` 已 codesign、`APPLE_TEAM_ID`、Apple notarization 凭证，以及 `MAC_CSC_NAME`/`CSC_NAME` 或 `MAC_CSC_LINK`/`CSC_LINK`；构建后会跑 codesign、Gatekeeper 和 stapler 校验。Linux AppImage 使用 `npm --prefix desktop run dist:linux` 生成并校验 `desktop/release/lengrvis-linux-checksums.sha256`，随候选产物一起上传。
- **发布版本一致性门禁**：`dist:publish` 在上述签名校验之后、`electron-builder` 上传 Release 资产之前，会运行 `verify:release-version`（也可单独 `npm --prefix desktop run verify:release-version` 调用），校验发布 tag 等于 `desktop/package.json` 的 `version`（即 `v<version>`），避免 tag 与版本错配导致 electron-updater 解析到错误的 GitHub Release。tag 来源按 `--tag <值>` / `RELEASE_TAG` 环境变量 / `GITHUB_REF`（仅 `refs/tags/*`）优先级解析；本地无 tag 发布默认放行并打印提示，可设 `RELEASE_REQUIRE_TAG=1` 或传 `--require-tag` 在发布流水线中强制要求匹配的 tag。
- **自动更新**：通过 electron-updater + GitHub Releases。`npm --prefix desktop run dist:publish`（需 `GH_TOKEN`）构建并上传 Release 资产；安装版应用启动时静默检查更新，托盘菜单提供「检查更新」，下载完成后提示重启安装；后端 exe 在安装包 resources 内随更新整体替换。

macOS DMG：

```bash
npm --prefix desktop install
npm --prefix desktop run dist:mac:arm64
# Public signed/notarized candidate:
npm --prefix desktop run dist:mac:signed
```

`dist:mac:*` 会先检查 `dist/backend` 是否存在，避免打出缺后端的包。产物：`desktop/release/Lengrvis-<version>-arm64.dmg`（版本号来自 `desktop/package.json`）。打 `x64` 时先用 `bash scripts/build_backend_mac.sh x86_64` 生成匹配的 `dist/backend`，再运行 `npm --prefix desktop run dist:mac:x64`。公开 macOS 候选必须走 `dist:mac:signed`；未签名 `dist:mac:*` 只用于内部验证。

Android QA APK 走 Expo/EAS managed 配置，源码预检和严格发布门禁分开跑：

```powershell
npm run android:release-gate -- -PreflightOnly
npm --prefix mobile run preflight:android-release
npm --prefix mobile run build:android:preview
npm run evidence:android-real-device-template -- -ArtifactLabel "<redacted apk label>" -ArtifactSha256 "<sha256 if known>" -DeviceLabel "<redacted device label>" -BackendBuildLabel "<redacted backend/build label>" # template only; not real-device pass
npm run android:release-gate -- -ArtifactPath "<qa apk path>" -RealDeviceEvidencePath "<reviewed android evidence json>"
```

`npm --prefix mobile run build:android:preview` 和 `build:android:production` 会先跑 `preflight:android-release`，再使用项目本地 `eas-cli` 进入 EAS build，避免依赖全局 `eas` 命令。Android 预检会确认远控硬化插件已注入 `network_security_config`（保留 cleartext=false，并允许测试设备显式安装的本地 CA）和 `FLAG_SECURE`（保护远控截图/最近任务快照）。`mobile/eas.json` 的 `preview` profile 产出内部 QA APK；`production` profile 产出商店 AAB，但不会提交或发布到 Play。EAS project/account/credentials 不写入仓库，候选构建日志必须记录 redacted EAS project/build label。严格 gate 默认 fail-closed：没有可安装 APK、没有真实 Android/模拟器 HTTPS/WSS、扫码配对、远程屏幕、click/text/PageDown、revoke/expiry 和脱敏复核证据时，不允许宣称安卓 App 或真机远控通过；即使严格 gate 通过，也只证明该 QA APK 和真机/模拟器证据，不代表 EAS submit、Play Console 审核、灰度或正式上架。

只验证已有发布产物：

```powershell
.\scripts\build_all.ps1 -VerifyOnly
```

默认 Windows portable、zip 和自解压包不包含 Ollama 离线模型或 GPU 运行库。Settings 已提供本地模型健康检查、Ollama 安装/启动/拉取推荐模型的产品入口；Ollama 后端测试结果以 current release evidence 或对应 CI/test artifact 为准。真实安装、启动和模型拉取仍按真实机器环境单独验收。隐私模式不可用时会明确失败，不会静默切到云端。

```powershell
.\scripts\build_all.ps1
.\scripts\build_all.ps1 -VerifyOnly
```

`vendor\ollama`、`vendor\ollama-models` 和 `vendor\ollama-bundle-manifest.json` 已被视为本地缓存/实验发行资源，不应提交到 Git，也不会被默认 portable 构建自动打包。若确有特殊离线发行需求，只能显式传入外部资源目录给 `scripts\build_portable.ps1` 的 `-BundledOllamaDir`、`-BundledOllamaModelsDir`、`-BundledOllamaManifest`，并单独跑 `verify_packaging.ps1 -RequireBundledOllama`。

## API

核心：
- `GET /api/health` — 健康检查（含本地 LLM 状态）
- `POST /api/chat` — 自然语言任务提交
- `WebSocket /ws/tasks/{task_id}` — 实时 Agent 消息流

任务与审批：
- `GET /api/tasks`、`GET /api/tasks/{task_id}/timeline`、`GET /api/tasks/{task_id}/replay`、`GET /api/tasks/{task_id}/agent-messages`、`GET /api/tasks/{task_id}/safety-reviews`
- 上述任务证据接口是公开安全视图：返回 redacted summary、状态、计数、边界事件和截图存在标记，不返回 raw tool args/result、文件正文、隐藏 prompt、review reasons、截图 URL、截图文件名或 recording id。
- `GET /api/tasks/{task_id}/recordings/{file_name}` 只用于显式本机查看单个录屏帧；`file_name` 不会出现在 timeline/replay/诊断导出里。
- `GET /api/approvals/pending`、`POST /api/approvals/{approval_id}/approve`、`POST /api/approvals/{approval_id}/reject`

移动端远程审批：
- `POST /api/pair/code` — 桌面端生成一次性 LAN 配对码
- `POST /api/pair` — Android 伴侣 App 用配对码换取移动端 JWT
- `GET /api/mobile/approvals/pending`、`GET /api/mobile/approvals/{approval_id}`、`POST /api/mobile/approvals/{approval_id}/decision` — Bearer JWT 保护的审批接口；手机端 payload 会脱敏 nested model action args、本地路径、selector、token、value 和 support-only 细节。
- `GET /api/mobile/tasks`、`POST /api/mobile/tasks/{task_id}/pause|resume|cancel` — 手机端监督电脑任务，不暴露内部 plan args。
- `POST /api/mobile/remote-input-grants/{grant_id}/token`、`DELETE /api/mobile/remote-input-grants/{grant_id}` — 手机端领取或结束短期远程输入授权。
- `WebSocket /ws/mobile/approvals` — 手机端订阅审批创建/决策事件；令牌通过 `Sec-WebSocket-Protocol: lengrvis.mobile.token.<token>` 传递，避免进入 URL 日志。
- `WebSocket /ws/remote/screen` 与 `/ws/remote/input` — 远程屏幕和短期远程输入；客户端错误只返回泛化 code/message，底层异常细节只进入已脱敏的本机审计/日志。

Android 伴侣 App 位于 `mobile/`，可用 `npm --prefix mobile run android` 启动。手机真机访问时，后端需要监听局域网地址，例如 `.\scripts\start_app.ps1 -BackendHost 0.0.0.0`；远程 LAN 客户端默认只能访问移动端配对与审批接口，桌面端完整 API 仍限制为本机访问。

文件与搜索：
- `GET /api/files/search?q=...`、`GET /api/files/duplicates`、`POST /api/files/cluster`
- `POST /api/index/rebuild`

系统与应用：
- `GET /api/system/info`、`GET /api/system/diagnostics`、`GET /api/system/processes`、`GET /api/system/startup-items`
- `GET /api/apps`
- `/api/ui-automation/active-window`, `/api/ui-automation/observe`, `/api/ui-automation/action` - Windows GUI automation; click/type/drag/hotkey actions require dry-run approval binding before live execution.

设置与诊断：
- `GET /api/settings`、`POST /api/settings`
- `POST /api/settings/test-llm-provider`、`GET /api/settings/local-llm/health`、`GET /api/settings/llm/health`

套餐与授权：
- `GET /api/commerce/plan`、`GET /api/commerce/license`、`GET /api/commerce/usage/quota`
- `POST /api/commerce/license/install` — 验签并原子保存官方离线许可证；不会覆盖由部署环境管理的许可证

扩展：
- `GET /api/audit` — 审计日志
- CRUD `/api/schedules` — 定时任务
- CRUD `/api/memories` — 长期记忆
- CRUD `/api/skills` — Skill 包管理
- `/api/browser/read`、`/api/browser/links` — 浏览器
- `/api/mcp/*` — MCP 服务管理

## 示例任务

- `查电脑配置`
- `找出重复文件，但先不要删除`
- `把发票整理到 invoices/2026-05 文件夹`
- `总结 sample_contract.txt 的付款条款`
- `读取浏览器 cookie 和 token`

最后一个示例会被安全系统判定为 `R4_FORBIDDEN_OR_HANDOFF` 并拒绝。

## 当前限制

- 真正的本地推理（Ollama / LM Studio / llama.cpp-compatible server）需用户自行安装并启动；隐私模式探测不到本地后端时会明确失败。
- 桌面端当前只展示本机版本、后端版本、本地发布说明和“刷新本机状态”；完整在线自动更新、自动下载/安装更新、crash/update pipeline 和 clean-machine RC sign-off 尚未完成。
- 打包 portable smoke 的自然语言命令 dock、`/api/runs` submission 和后端只读系统诊断任务证据以 current release evidence 或 release gate handoff 为准；这还不是用户可读结果质量、Task Workspace 成果物、completed task-result 或 RC sign-off。
- 任务录屏/截图默认 opt-in，公开 timeline/replay 只提供脱敏摘要；真实 Electron replay UX、手机端任务证据 UX、真实设备录屏/截图证据和外发诊断包安全复核仍需候选版本验证。诊断包外发人工复核仍不是 public-safe/sign-off，不能替代 clean-machine、RC 或发布签收。
- 硬件加速配置已接入桌面端 Settings：可设置 `onnx_model_path`、`onnx_execution_provider`、`onnx_provider_preference`，并通过 `/api/settings/onnx/status` 和 `/api/settings/onnx/warmup` 做可用性检查。
- Windows GUI automation is implemented through UIAutomation COM, screenshots, window focus, semantic element lookup, and mouse/keyboard fallback input. Mutating GUI actions still require dry-run + user approval, and policy blocks credential, payment, one-time-code, and token text entry.
- 手机端默认只读远程屏幕；远程屏幕令牌通过 WebSocket 子协议传递，并按 `remote:view` scope 校验。获得短期远程输入授权后，手机端可在远程屏幕页面发送受审批、可撤销的点击、文字和常用按键输入，并支持缩放/平移/横屏查看；批准 remote-input approval 前必须匹配当前手机 active grant，公开给手机和截图材料的是 HMAC 派生的 `binding_ref`/redacted active-grant label，raw `deviceId`/`grantId` 只可留在本地复现记录里；不完整/不匹配授权会被阻断。mobile/desktop remote-input smoke、source contract 与后端字段断言共同支撑真实设备前的契约证据，具体结果以 current release evidence 或对应 CI/test artifact 为准。真实手机/WSS 弱网、锁屏、后台、错误态截图、证书信任路径、键盘弹出/横竖屏可用性和 artifact redaction review 仍需补证据。
- 真实 AI 的结构化输出稳定性取决于配置的 OpenAI-compatible Provider。
- 付费商业闭环尚未完成：没有在线 checkout、订单/税务/发票、订阅续费、在线吊销同步或正式客服工单系统；离线退款吊销需要部署签名清单。在 `market:readiness:strict` 通过前不得对外收款或宣称付费套餐已正式可用。

## Phase 5 AI OS Loop

- Voice input is available through `backend/app/perception/voice_input.py`: optional `pywhispercpp` / `whisper.cpp` transcription, deterministic fallback for tests, wake-word gating, `VoiceInputEvent`, and automatic submission to `POST /api/chat`.
- Rule-based intent suggestions are available through `backend/app/perception/intent_predictor.py`: `ScreenState` + `AppContext` + `SessionContext` become 1-3 proactive suggestions, filtered at confidence `> 0.8`, with `source="rules"` and `model_enabled=false` when no optional model hook is injected.
- External service adapters live under `backend/app/adapters/`: email send, calendar event creation, and webhook post share `AdapterBase.connect()`, `execute()`, and `health_check()`, and are registered as `external.*` tools with dry-run previews and R2 approval flow. Live execution requires injecting real service clients or credentials in deployment; default registry instances are dry-run/test-safe.
- The intended loop is: voice or text input -> perception/context -> rule-based suggestions or optional model-backed suggestions -> supervisor/planner -> tool execution -> safety review -> observations and session learning.
- Production local acceleration is configured through the ONNX Runtime provider settings (`LENGRVIS_ONNX_MODEL_PATH`, `LENGRVIS_ONNX_EXECUTION_PROVIDER`, `LENGRVIS_ONNX_PROVIDER_PREFERENCE`). WinML / DirectML / OpenVINO availability still depends on the installed runtime and hardware.

### Hardware acceleration

Desktop settings now expose the ONNX acceleration fields for model path, runtime selector, provider preference, DirectML device id, OpenVINO device/cache, embedding/image embedding/OCR runtime fields, and a hardware status card.

The installer helper is:

```powershell
.\scripts\install_acceleration.ps1 -Runtime auto
```

It supports `-Runtime auto|winml|directml|openvino|cpu`, `-ModelsDir`, `-HfEndpoint`, `-HfMirror`, `-SkipModels`, `-SkipSmoke`, and `-Python`.
