# Lengrvis

Windows 优先的本机 OS Agent / 电脑助手：自然语言描述目标，多 Agent 协作规划与执行，修改文件或系统设置前经过策略审查与用户确认。

**仓库：** [github.com/suli9710/-lengrvis](https://github.com/suli9710/-lengrvis)

| 组件 | 技术栈 |
| --- | --- |
| 桌面端 | Electron · React · TypeScript · Vite · Zustand |
| 后端 | Python 3.12 · FastAPI · SQLite · Playwright |
| 移动伴侣 | Expo · React Native（Android Preview） |
| CI | GitHub Actions（hygiene · pytest · golden gate · typecheck） |

## 平台支持矩阵

| 平台 | 状态 | 当前交付 | 已知限制 |
| --- | --- | --- | --- |
| Windows 桌面 | Supported | Electron 桌面、FastAPI 后端、Windows portable/zip/SFX 打包、任务工作台、审批、文件/文档/系统工具 | 发布包已有 portable 首屏 smoke：packaged renderer 已观察到 `/api/system/diagnostics`，NL 命令 dock 已观察到 `/api/runs` 和后端只读系统诊断任务证据；这不是任务结果完成签收，仍需 clean-machine、真实设备和候选版本人工验收。 |
| Android Companion | Preview | 配对、移动审批、任务监督、暂停/继续/取消、只读屏幕流、受控远程输入授权 | 移动审批和远程 WS 脱敏已有后端目标证据；真机 LAN/WSS、证书信任路径和完整应用商店分发未完成。 |
| macOS 桌面 | Preview | macOS 后端构建脚本与 DMG 脚本存在 | 不作为 0-90 天主线，需在 macOS 主机验证。 |
| iOS Companion | Planned | 暂不交付 | 等 Android companion 闭环稳定后再排期。 |

## 普通用户快速开始

1. 双击 `启动 Lengrvis.cmd` 启动 Lengrvis。
2. 正式发布包会直接启动已打包好的产物，不会在第一次启动时现场运行 `pip install` 或 `npm install`。命令行窗口会显示“正在启动”“已启动”或失败原因。
3. 启动成功后会打开 Lengrvis 桌面窗口。首屏可以直接从“整理下载目录、总结本地文档、查找大文件、检查电脑状态、文档问答”开始，每个模板都会显示本机处理、云端边界、审批、回滚和预计耗时。
4. 如果启动失败，请先双击 `Start-Lengrvis-Debug.cmd`，它会把最近的错误日志打印出来；完整日志在 `logs` 文件夹。
5. 如果你下载的是源码或 Git 仓库，请先看下面的“源码开发 setup”；源码依赖安装不属于普通用户启动路径。
6. 完整的上手步骤、FAQ 与故障排查见 `docs/user-guide.md`。

## 普通用户配置与诊断入口

- 配置 AI、隐私模式、本地模型、硬件加速和手机配对时，优先打开桌面窗口里的“设置”。普通用户不需要手动编辑 `.env` 或 `config.yaml`。
- 删除本机个人数据：当前提供 API 入口 `POST /api/system/privacy/erase-local-data`（需显式确认词），会删除任务、对话、录屏、配对、索引与已导出诊断包，保留防篡改审计链并记录删除事件；桌面设置页按钮入口尚未提供。数据清单与合规自查见 `docs/compliance/pipl-gdpr-checklist.md`（法务定稿未完成，不得对外宣称合规）。
- 应用能打开但任务异常时，打开“系统信息”。这里会显示桌面版本、后端版本、服务状态、日志目录、只读系统诊断和本地发布说明入口。
- “刷新本机状态”只刷新当前安装版本、后端状态和诊断快照；当前没有完整在线自动更新、下载更新或自动安装更新通道。
- 需要反馈问题时再点“导出诊断包”。诊断包会写入本机数据目录下的 `diagnostic-packages`，包含版本、服务状态、本机范围摘要、网络接口、进程、启动项和最近失败统计；导出内容会尽量把 data/database/log 绝对路径、进程用户名、密钥、任务正文、设备名、配对码、grant id 和模型路径脱敏。
- 诊断包不是“可公开发布材料”：界面可能显示本机保存位置方便你打开文件，分享前仍应确认没有不该外发的路径、日志片段或组织信息。即使外发前做了人工内容复核，也只代表检查过该包内容，不代表 `public_safe=true`、clean-machine 验收、候选版本 RC sign-off 或发布签收；自动测试和 evidence helper 只能作为脱敏契约或 handoff 模板证据。
- 应用打不开时，双击 `Start-Lengrvis-Debug.cmd`，它会显示已脱敏的最近启动日志摘要和下一步；完整日志位置通常在仓库 `logs` 目录或应用数据目录的 `logs` 目录，能打开应用时也可在“系统信息”里查看。

## 任务证据与录屏隐私

- 任务步骤录屏/截图默认不采集。只有你明确开启任务录屏（开发/测试环境可用 `LENGRVIS_TASK_RECORDING_ENABLED=true`，测试可用 `LENGRVIS_TASK_RECORDING_FORCE=1`）时，才会把截图作为本机 task recording 写入数据目录；不要在含私人资料的 profile 上随手开启。
- 任务 timeline、replay、任务列表、agent messages、safety reviews、progress 和 explain 接口只返回 redacted summary、状态、计数和边界标签。它们不会返回截图 URL、截图文件名、recording id、raw tool args/result、隐藏 prompt、任务 metadata、review reasons 或文件正文。
- 诊断包导出只保留 task recording 的状态边界，例如是否开启和默认 opt-in 策略；不会把录屏图片、截图文件名或 task recording 路径放进支持包。原始截图只能通过显式本机文件名路线读取，不能从公开 timeline/replay 自动发现。

## 产品说明

这是一个 Windows 优先的本机 OS agent / 电脑管家原型。它不是某个竞品的替代品，也不主打云端万能工作台；当前最清晰的差异化是围绕用户自己的电脑做可审计、可扩展、可自托管的任务执行：用自然语言描述目标，系统通过多 Agent 协作理解任务、规划步骤、调用本地工具，并在修改文件或系统设置前进行安全审核和用户确认。

当前版本不是纯聊天机器人，也不是开发者控制台。桌面端第一屏已经改成消费级电脑助手体验：一句话任务入口、隐私/混合/效率模式、文件/文档/图片/电脑/应用/网页能力卡、手机审批与屏幕查看入口、Agent 进度和安全审批。与发布级 OS AI 产品相比，它仍需要补齐本地模型开箱即用、跨端分发、App 深度集成和真实设备验收。

## 架构

```text
mavris/
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

不配置 `LENGRVIS_API_KEY` 时，效率/混合模式可按 `LENGRVIS_ALLOW_MOCK_FALLBACK` 使用 `MockProvider` 做开发演示。隐私模式始终需要真实本地 LLM 后端。

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

主测试入口会运行 backend pytest、desktop TypeScript typecheck、mobile TypeScript typecheck，以及 mobile token WebSocket smoke、mobile task companion smoke 和 mobile remote-input grant smoke。
这些 mobile smoke 都是本地行为桩/客户端契约证据，避免发布门禁漏掉移动任务监督和远程输入授权边界；它们不等同于真机 LAN/WSS 或证书信任路径验收。

**CI 与本地差异：** `.github/workflows/ci.yml` 在 push/PR 上跑 hygiene、deps:verify、backend pytest、golden gate、desktop/mobile typecheck 和 mobile smokes；**不包含** desktop Playwright smoke 与 `release:check`。完整发布前请本地跑 `npm run qa:gate`。每周 SCA 见 `.github/workflows/security-audit.yml`。

### 最近一次全量验证（2026-06-11，本机 Windows）

| 门禁 | 结果 |
| --- | --- |
| `npm run hygiene` + `deps:verify` | 通过 |
| `npm --prefix desktop run typecheck` | 通过 |
| `npm --prefix mobile run typecheck` + 3 smokes | 通过 |
| `npm run golden:gate` | **35/35（100%）** |
| `python -m pytest backend/tests -q --maxfail=1` | **137 passed 后 1 failed**（见下） |
| `npm --prefix desktop run smoke` | **12/13 通过**（`browser-activity` 超时） |

已知失败（修复中/需跟进）：

- `backend/tests/test_browser_writes.py::test_browser_act_is_classified_by_nested_action_kind` — nested `browser.act` observe 动作的风险分级与 PolicyEngine 预期不一致。
- `desktop/scripts/browser-activity-smoke.cjs` — 等待 `Task Workspace` 可见超时（导航/时序，非 CSS 回归）。

此前完整 qa:gate 记录（2026-06-10）供对照：

```text
backend: 1569 passed, 1 skipped
desktop typecheck passed
mobile typecheck + smokes passed
desktop smoke passed（全 14 项）
```

诊断和产品化边界的针对性证据：

```powershell
python -m pytest backend\tests\test_system_diagnostics.py -q
python -m pytest backend\tests\test_remote_desktop.py -q
python -m pytest backend\tests\test_mobile_pairing.py -q
npm --prefix desktop run smoke:system-diagnostics-ui
npm --prefix desktop run smoke:settings-local-model
npm --prefix desktop run smoke:first-launch
npm run smoke:portable-first-screen
git diff --check
```

本轮目标结果包括 desktop/mobile typecheck、desktop mobile-pairing QR 和 remote-input grant smokes、mobile token/task-companion/remote-input smokes、backend mobile+remote targeted combined run `132 passed`，以及当前集成中的 desktop/mobile targeted smokes/typechecks 通过。scheduler/preflight 定向检查只有在附上 exact command/log 时才可引用；不要把未绑定命令的 `9 passed` 作为可复用证据计数。`git diff --check` exit 0，只有 LF-to-CRLF working-copy conversion warnings。backend mobile+remote targeted 结果覆盖移动审批脱敏、token scope、设备绑定、LAN TLS metadata、companion task 边界、远程屏幕/输入 WebSocket 泛化错误、grant revoke/expiry/disable 行为、文字/按键远控输入审批契约；active-grant guard 还需要结合 mobile/desktop remote-input smoke/source contract 来说明 remote-input approval 必须匹配当前手机 active grant。共享证据用 HMAC `binding_ref` 或 redacted active-grant label 说明匹配结果，raw `deviceId`/`grantId` 只保留在本地复现记录。Ollama 结果覆盖后端 status/setup-plan/install/start/pull/install-local-model 契约，但不代表真实机器已经完成本地模型安装、启动或拉取。上述证据还覆盖诊断 payload/export 脱敏、Vite 预览中的版本与本机刷新 UI、first-screen read-only 模板，以及 packaged portable 的只读诊断/命令 dock 证据；它们不等同于完整 crash/update pipeline、在线自动更新、真机 LAN/WSS 验收、clean-machine RC sign-off、completed task-result sign-off、自然语言结果质量/Task Workspace 签收或外发诊断包人工安全复核。诊断包 helper、pytest、typecheck、source/client smoke、UI smoke 和 `git diff --check` 只能证明契约字段、脱敏种子、handoff 模板或格式卫生；外发人工复核仍不是 public-safe/sign-off，source/smoke 证据也不能替代真实设备 LAN/WSS、证书信任路径、弱网/锁屏/后台和截图日志复核证据。

跳过项是当前 Windows shell 没有创建符号链接权限。

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

已有 Windows 发布产物时再跑产物门禁：

```powershell
npm run release:check
```

证据 helper 新手入口（只整理材料，不产生签收）：

```powershell
npm run evidence:release # template only; not a pass
npm run evidence:rc-handoff -- -CandidateCommit "<commit SHA>" -BuildId "<build id>" -Platform "<platform>" -ArtifactLabel "<redacted artifact label>" -GateCommand "<exact command>" -GateExit "<exit code/status>" -StrictStateSource "<strict state source>" -ManualP1Check "<check/status/artifact label>" -Waiver "<none or owner/reason/expiry/follow-up>" -ResidualRisk "<risk/owner/follow-up>" # template only; not a pass
npm run evidence:result-quality-review -- -TaskArtifactLabel "<task/run/status-log label>" -ResultArtifactLabel "<user-visible result/artifact label>" -UserVisibleResultReview "<review notes>" -SourceArtifactCheck "<source/artifact check>" -NextStepActionabilityCheck "<next-step/actionability check>" -Reviewer "<reviewer label>" -ReviewedAtUtc "<UTC timestamp>" -BlockedReason "none" # template only; not a pass
npm run evidence:mobile-lan-wss # prerequisite template only; not real-device pass
npm run android:release-gate -- -PreflightOnly # source/config check only; not APK or real-device pass
npm run evidence:android-real-device-template -- -ArtifactLabel "<redacted apk label>" -ArtifactSha256 "<sha256 if known>" -DeviceLabel "<redacted device label>" -BackendBuildLabel "<redacted backend/build label>" # template only; not real-device pass
npm run android:release-gate -- -ArtifactPath "<qa apk path>" -RealDeviceEvidencePath "<reviewed android evidence json>" # strict gate; requires APK + real-device evidence
npm run evidence:local-model-template -- -EvidenceMode clean-machine -Runtime "<runtime>" -RuntimeVersion "<version>" -Model "<model>" -ModelVersion "<version>" -BlockedReason "<redacted blocked reason>" # template only; not a pass
npm run evidence:diagnostics-review # template only; not public-safe/signoff
```

这组顶层 npm 命令只是包装现有 helper：`evidence:release` 生成 release evidence packet 索引，`evidence:rc-handoff` 只整理候选 commit/build、platform、artifact label、gate command/exit、strict state source、manual P1、waiver 和 residual risk 的 handoff 模板字段，`evidence:result-quality-review` 只整理自然语言结果质量 review checklist，`evidence:mobile-lan-wss` 是无手机/无真 WSS 的 prerequisite preflight，`android:release-gate -PreflightOnly` 只检查 Android source/build 配置，`evidence:android-real-device-template` 只生成 fail-closed 真机证据模板，严格 `android:release-gate` 需要真实 APK 和已复核的手机/模拟器远控证据，`evidence:local-model-template` 只填 clean-machine handoff 模板字段，`evidence:diagnostics-review` 只整理诊断包外发复核模板/状态。输出只能作为 evidence/template/preflight 记录，不是 clean-machine pass、real-device pass、`public_safe=true`、public-safe/signoff、result-quality signoff、RC signoff、发布签收或 completed task-result signoff；即使模板字段都填完，也必须附上完整 gate 日志、人工 P1 证据、waiver/risk 处理记录，并由 release owner 明确人工批准后，才可以进入 RC 或发布签收。

新手只看这张缺口表即可，不需要额外流程：

| 看到的 helper/preflight 输出 | 不能称为 | 下一步真实证据 |
| --- | --- | --- |
| `npm run android:release-gate -- -PreflightOnly` | Android release pass、APK install pass、real-device pass | 生成 QA APK，安装到目标 Android/模拟器，附上已复核的 camera QR、HTTPS/WSS、证书信任、远程屏幕/输入、revoke/expiry 和 artifact redaction evidence，再跑严格 `android:release-gate`。 |
| `npm run evidence:android-real-device-template` | Android real-device pass、remote-control pass | 把它当成 `android-real-device-evidence.redacted.json` 的 fail-closed 起点；template only, not real-device pass；只有真实 APK 安装、真机/模拟器 WSS、证书信任、输入审批和脱敏复核都完成后，才能把对应字段改成 passed/true。 |
| `npm run evidence:mobile-lan-wss` | real-device LAN/WSS pass | 按 `real-device-evidence-checklist.redacted.md` 在真实手机/模拟器上补 camera QR、approval WSS、remote screen WSS、remote input WSS、设备证书信任和截图/日志复核。 |
| `npm run evidence:local-model-template` | clean-machine local model pass | 在干净机器或干净 profile 上记录 artifact/build/profile、runtime/model/version、install/start/pull/task-smoke outcome，或记录明确 blocked reason。 |
| `npm run evidence:diagnostics-review` | `public_safe=true`、可外发诊断包、发布签收 | 对实际导出的诊断包做人工内容复核，记录包路径 label、日志/路径/task/model/device 检查、reviewer、timestamp、decision 和 blocked reason。 |
| `npm run evidence:rc-handoff` 或 `npm run evidence:release` | RC signoff、release signoff | 命令输出 not a pass/signoff；补齐 candidate commit/build/platform、完整 gate 日志、manual P1、waiver/residual risk 处理，并由 release owner 单独批准。 |

生成 release packet 后，先打开 `.tmp\release-evidence-packet\...\release-evidence-packet.redacted.md` 给新人看缺口，再逐项处理 `release_readiness_blockers`：clean-machine local model、真实设备 LAN/WSS、自然语言结果质量、诊断包实际内容复核和 RC handoff。所有 blocker 都有对应证据并完成 release owner 人审签收前，不要打 tag、发布、公告或对外共享诊断包。

发布候选若需要收集打包 GUI 首屏和只读任务入口证据，再跑 `npm run smoke:portable-first-screen`。最新开发工作区证据目录为 `.tmp\portable-first-screen-smoke\run-20260608-154045-41396-6013e259`：只读入口观察到 packaged renderer `/api/system/diagnostics`；自然语言 dock 观察到 `/api/runs` 与后端 read-only/system diagnostics task evidence。该证据只覆盖 packaged command-dock 提交和只读任务证据，不能替代 clean-machine、真实设备、人工 RC sign-off 或 completed task-result sign-off。诊断包外发前的人工内容复核也只是实际包内容检查，不是 `public_safe` 批准、clean-machine/RC sign-off 或发布签收；相关 helper/自动测试只能作为模板或契约证据。移动/LAN 演示的 TLS 仅按显式设备信任路径记录，不代表系统级证书链已完成。

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
python -m pip install -U pip
python -m pip install -r requirements-dev.txt
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

- **签名**：本地 `npm --prefix desktop run dist` 不签名（仅内部分发）。持 OV/EV PFX 证书时设置 `WIN_CSC_LINK` / `WIN_CSC_KEY_PASSWORD` 环境变量后照常构建即可；走 Azure Trusted Signing 时用 `npm --prefix desktop run dist:signed`（配置见 `desktop/electron-builder.signed.yml`，需 `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET`）。随包的 `backend.exe` 需在打包前单独用 signtool 签名。
- **自动更新**：通过 electron-updater + GitHub Releases。`npm --prefix desktop run dist:publish`（需 `GH_TOKEN`）构建并上传 Release 资产；安装版应用启动时静默检查更新，托盘菜单提供「检查更新」，下载完成后提示重启安装；后端 exe 在安装包 resources 内随更新整体替换。

macOS DMG：

```bash
npm --prefix desktop install
npm --prefix desktop run dist:mac:arm64
```

`dist:mac:*` 会先检查 `dist/backend` 是否存在，避免打出缺后端的包。产物：`desktop/release/Lengrvis-<version>-arm64.dmg`（版本号来自 `desktop/package.json`）。打 `x64` 时先用 `bash scripts/build_backend_mac.sh x86_64` 生成匹配的 `dist/backend`，再运行 `npm --prefix desktop run dist:mac:x64`。

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

默认 Windows portable、zip 和自解压包不包含 Ollama 离线模型或 GPU 运行库。Settings 已提供本地模型健康检查、Ollama 安装/启动/拉取推荐模型的产品入口；Ollama 后端测试当前记录为 `53 passed`，覆盖产品入口和服务契约，但真实安装、启动和模型拉取仍按真实机器环境单独验收。隐私模式不可用时会明确失败，不会静默切到云端。

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
- 打包 portable smoke 已证明自然语言命令 dock 的 `/api/runs` submission 与后端只读系统诊断任务证据；这还不是用户可读结果质量、Task Workspace 成果物、completed task-result 或 RC sign-off。
- 任务录屏/截图默认 opt-in，公开 timeline/replay 只提供脱敏摘要；真实 Electron replay UX、手机端任务证据 UX、真实设备录屏/截图证据和外发诊断包安全复核仍需候选版本验证。诊断包外发人工复核仍不是 public-safe/sign-off，不能替代 clean-machine、RC 或发布签收。
- 硬件加速配置已接入桌面端 Settings：可设置 `onnx_model_path`、`onnx_execution_provider`、`onnx_provider_preference`，并通过 `/api/settings/onnx/status` 和 `/api/settings/onnx/warmup` 做可用性检查。
- Windows GUI automation is implemented through UIAutomation COM, screenshots, window focus, semantic element lookup, and mouse/keyboard fallback input. Mutating GUI actions still require dry-run + user approval, and policy blocks credential, payment, one-time-code, and token text entry.
- 手机端默认只读远程屏幕；远程屏幕令牌通过 WebSocket 子协议传递，并按 `remote:view` scope 校验。获得短期远程输入授权后，手机端可在远程屏幕页面发送受审批、可撤销的点击、文字和常用按键输入，并支持缩放/平移/横屏查看；批准 remote-input approval 前必须匹配当前手机 active grant，公开给手机和截图材料的是 HMAC 派生的 `binding_ref`/红acted active-grant label，raw `deviceId`/`grantId` 只可留在本地复现记录里；不完整/不匹配授权会被阻断。本轮 backend mobile+remote targeted combined run 为 `132 passed`，覆盖 auth/scope、query-token rejection、remote view/input 交叉 scope rejection、revoke/expiry/disable close behavior、invalid control、screen capture failure、unsupported input、policy/tool rejection、文字/按键输入审批契约、remote input unexpected exception redaction 和移动审批脱敏；active-grant approval guard 另由 mobile/desktop remote-input smoke/source contract 与后端字段断言共同支撑。scheduler/preflight 计数只有在附 exact command/log 时才可引用。这些仍是真实设备前的契约证据，真实手机/WSS 弱网、锁屏、后台、错误态截图、证书信任路径、键盘弹出/横竖屏可用性和 artifact redaction review 仍需补证据。
- 真实 AI 的结构化输出稳定性取决于配置的 OpenAI-compatible Provider。

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
