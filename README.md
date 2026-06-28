# Lengrvis

Lengrvis 是一个 Windows 优先的本地电脑助手。你用一句话描述任务，它把任务拆成可审计的步骤，再调用本机文件、文档、浏览器、系统信息和桌面工具。凡是会写文件、删除内容或改变系统状态的操作，都会先给出预览，等用户批准，并留下回滚信息。

它适合处理这些日常活：整理下载目录、总结本地文档、查找大文件、检查电脑状态、做文档问答，或者把审批和任务监督交给 Android companion。默认思路很朴素：能在本机处理的留在本机；需要动手前先问；出事以后能追踪。

- 仓库：[github.com/suli9710/-lengrvis](https://github.com/suli9710/-lengrvis)
- 当前版本：[v0.1.1](https://github.com/suli9710/-lengrvis/releases/tag/v0.1.1)
- License：BUSL-1.1

| 组件 | 技术栈 |
| --- | --- |
| 桌面端 | Electron、React、TypeScript、Vite、Zustand |
| 后端 | Python 3.12、FastAPI、SQLite、Playwright |
| 移动伴侣 | Expo、React Native（Android Preview） |
| 自动化验证 | GitHub Actions、pytest、golden tasks、typecheck、smoke、IPC/Skill/MCP/settings security |

## 平台状态

| 平台 | 状态 | 当前交付 | 还没收口的地方 |
| --- | --- | --- | --- |
| Windows 桌面 | Supported | Electron 桌面、FastAPI 后端、portable zip、自解压包、任务工作台、审批、文件/文档/系统工具 | v0.1.1 产物未签名；clean-machine、升级/回滚、真实设备联动还需要候选版本证据。 |
| Android Companion | Preview | 配对、移动审批、任务监督、暂停/继续/取消、只读屏幕流、短期远程输入授权 | 适合内部预览和联调；真机 LAN/WSS、证书信任路径和应用商店分发还没完成。 |
| macOS 桌面 | Preview | macOS 后端构建脚本和 DMG 脚本 | 需要在 macOS 主机上完成打包、签名和 notarization 验证。 |
| iOS Companion | Planned | 暂不交付 | Android companion 稳定后再排期。 |

## 安装与快速开始

1. 打开 [Releases](https://github.com/suli9710/-lengrvis/releases)。
2. 下载 `Lengrvis-0.1.1-win-portable.zip` 或 `Lengrvis-0.1.1-x64-self-extracting.exe`。
3. portable zip 解压后运行 `Lengrvis.exe`；自解压包双击后会释放到本机用户目录并启动。
4. 启动后，在桌面窗口里输入任务，例如“找出重复文件，但先不要删除”。
5. 会改动本机状态的步骤会进入预览和审批。文件写入、编辑等操作会记录回滚信息。

源码仓库不是普通用户启动路径。要从源码运行，请看下面的“源码开发 setup”。完整上手、FAQ 和故障排查在 `docs/user-guide.md`。

## 配置、隐私与诊断

- AI Provider、隐私模式、本地模型、硬件加速和手机配对优先在桌面“设置”里配置。普通用户不需要手动编辑 `.env` 或 `config.yaml`。
- “设置 -> 套餐与授权”显示 Free / Pro / Max 能力、云端额度窗口、许可证主体和到期时间。默认 token 护栏为 Free 滚动 5 小时 500 万 + 7 天 2,000 万、Pro 滚动 24 小时 1,000 万、Max 滚动 24 小时 1 亿。离线许可证可以本机验签；在线购买、订阅、退款自动降级和吊销同步仍在建设中。
- “设置 -> 本机数据与隐私”可以删除本机数据。删除前需要确认短语和系统确认；任务、对话、录屏、配对、索引和已导出诊断包会被清掉，防篡改审计链会保留删除事件。日志目录仍需手动清理。
- “系统信息”显示桌面版本、后端版本、服务状态、日志目录、只读系统诊断和本地发布说明。
- “导出诊断包”用于支持排查。诊断包写入本机数据目录下的 `diagnostic-packages`，会尽量脱敏路径、用户名、密钥、任务正文、设备名、配对码、grant id 和模型路径。
- 诊断包默认不是公开材料。外发前仍要人工检查里面有没有不该分享的路径、日志片段或组织信息。
- 应用打不开时，可以运行 `Start-Lengrvis-Debug.cmd` 看最近启动日志摘要。完整日志通常在仓库 `logs` 目录或应用数据目录的 `logs` 目录。

## 任务证据和录屏

任务录屏/截图默认关闭。只有明确开启任务录屏时，截图才会作为本机 task recording 写入数据目录；开发/测试环境可用 `LENGRVIS_TASK_RECORDING_ENABLED=true`，测试可用 `LENGRVIS_TASK_RECORDING_FORCE=1`。不要在含私人资料的 profile 上随手开启。

公开任务视图只返回脱敏摘要、状态、计数和边界标签。`timeline`、`replay`、任务列表、agent messages、safety reviews、progress 和 explain 接口不会返回截图 URL、截图文件名、recording id、raw tool args/result、隐藏 prompt、任务 metadata、review reasons 或文件正文。诊断包也只记录 task recording 的状态边界，不会打包录屏图片或截图路径。

## 它怎么工作

```text
lengrvis/
├── backend/app/         FastAPI 后端、Agent、策略、工具、索引、服务
├── backend/tests/       pytest 契约测试与 golden tasks
├── desktop/src/         Electron main/preload + React 渲染层
├── desktop/scripts/     Playwright UI smoke
├── mobile/              Expo Android companion
├── scripts/             PowerShell 启动、开发 setup、测试和打包
├── test_data/           授权目录、策略和隐私测试数据
└── docs/                用户手册、QA 门禁、发布与合规文档
```

运行时大致是这条链路：

```text
用户 -> OrchestratorAgent -> PlannerAgent -> SafetyReviewAgent
    -> domain shell agent act() / PolicyEngine / ToolRuntime
    -> SafetyReviewAgent -> 下一步 / 审批 / 完成
```

## 功能概览

### 核心架构

- 自然语言任务提交：桌面端或 `POST /api/chat`。
- Agent 分层：Orchestrator、Planner、Supervisor、SafetyReview、OSExecutionEngine 和 Memory 负责推理与编排；PolicyEngine、ToolRuntime、审批绑定、路径沙盒和 schema validation 负责确定性安全约束。
- Domain shell agents：File、Document、Computer、App、Browser、Search 等领域 Agent 主要声明 owner、prompt 和 allowed tools，并共享 `BaseAgent.act()`。
- fast path：常规成功路径会先走 tool owner、required args、schema、dry-run/approval 等确定性检查，不是每一步都让 LLM 自主推理。
- LLM 介入点：Planner/Supervisor、文档摘要/问答/报告、失败恢复、复杂改参，或 fast path 无法确定时。
- Step 级并行执行：无依赖步骤用 `asyncio.gather` 并发，有依赖步骤按拓扑顺序执行。
- Prompt 外部化：Agent system prompt 和 LLM 任务模板放在 `backend/app/llm/prompts/`。

### LLM 与推理

- OpenAI-compatible 接入：`base_url`、`api_key`、`model`、`wire_api` 可配置。
- 支持 `chat/completions` 与 `responses` 两种 OpenAI 格式。
- `base_url` 可以写裸域名或完整 `/v1` API base。`https://api.example.com` 会归一化为 `https://api.example.com/v1`；已有 `/v1` 或自定义代理 path 不会重复改写。
- Provider 路由有三种模式：效率（云端）、隐私（本地）、混合（按任务类型分流）。
- 隐私模式和混合模式里的本地任务会探测 Ollama、LM Studio、llama.cpp-compatible server。没有本地 LLM 时会明确报错，不会静默切到 `MockProvider`。
- `MockProvider` 只用于开发、测试和非隐私路径的演示兜底。
- ONNX Runtime Provider 框架支持 WinML、DirectML、OpenVINO 和 CPU。
- ContextManager 统一处理 LLM provider 的上下文：tool result budget、history snip、micro-compact、session memory、auto-compact、LLM call、prompt-too-long retry。原始 AgentBus/DB 历史不会被删。
- Token 预算由 `LENGRVIS_MODEL_CONTEXT_WINDOW`、`LENGRVIS_MODEL_AUTO_COMPACT_TOKEN_LIMIT`、`LENGRVIS_CONTEXT_*` 控制。CJK 文本使用更密的 token 估算，避免 auto-compact 太早或太晚。

### 安全

- 风险等级：`R0_READ_ONLY`、`R1_OPEN_ONLY`、`R2_REVERSIBLE_MODIFY`、`R3_DESTRUCTIVE_OR_SYSTEM`、`R4_FORBIDDEN_OR_HANDOFF`。
- R2/R3 操作会生成 dry-run 预览和审批记录。
- R4 请求会直接拒绝，例如读取浏览器 cookie、token、密码。
- 路径沙盒拦截符号链接逃逸、`..` 穿越和系统敏感路径。
- SafetyReview 对低风险消息走确定性快速通道，高风险或模糊场景再进入完整审核。
- 审计日志全链路记录，并做自动 PII 脱敏。

### 文件、文档和工具

- 授权目录文件搜索、FTS5 全文索引（含 CJK trigram 迁移）、重复文件检测。
- 向量语义搜索：FTS5 候选召回、Embedding rerank、cosine similarity、按文件折叠。
- 文档 AI：摘要、问答、报告生成，支持 map-reduce 分块和 extractive fallback。
- 文档文本提取：PDF、DOCX、XLSX、PPTX、CSV。
- 离线 OCR：本地 Tesseract、元数据 OCR、云 vision fallback；PDF 图片可自动 OCR。
- 文件、应用、图片聚类：k-means + hashing trick。
- 浏览器自动化：只读抓取，以及带 dry-run 的 navigate / click / fill / submit / wait。
- 系统信息读取：psutil、winreg、磁盘、电池、启动项。
- 应用扫描、MSI 卸载、Excel COM 自动化、视觉 describe/OCR/compare。
- MCP 客户端与 Registry：JSON-RPC 2.0 over HTTP。

### 扩展

- Skill 包系统：声明式 `skill.yaml`、安全审查、Python / Shell 沙盒执行、动态工具注册。
- Skill 签名：`skill.yaml` 可声明 Ed25519 签名；配置 `LENGRVIS_SKILL_TRUSTED_PUBLIC_KEYS` 后，manifest digest 或签名不匹配会 fail-closed。
- 定时调度器：croniter + async tick + 真实任务执行。
- 长期记忆：MemoryAgent（embed、cosine、DB、TTL、tags）。
- WebSocket 实时推送：`/ws/tasks/{task_id}`。
- 回滚工具：逆序重放 `rollback_info`。
- 状态机审计/严格模式：默认同步审计，strict 模式下非法转移会抛错。

## 源码开发 setup

正式发布包不需要执行本节；普通用户解压完整发布包后直接双击 `启动 Lengrvis.cmd`。只有从源码或 Git 仓库运行时，才需要安装开发依赖：

```powershell
.\scripts\setup_dev.ps1
```

`setup_dev.ps1` 会创建 `.venv`，安装 Python 开发依赖，并按 `desktop/package-lock.json` 安装桌面/前端依赖。

可选安装 pre-commit 钩子：

```powershell
python -m pip install pre-commit
pre-commit install
```

密钥扫描使用严格配置 `.gitleaks-ci.toml`，扫描 Git source snapshot，并显式绕过
`.gitleaksignore` 行号指纹；本机需安装 `gitleaks` 或 Go（脚本会用
`go run github.com/zricethezav/gitleaks/v8@v8.28.0`）：

```powershell
npm run security:secrets
```

手动排查时，可以按下面的等价命令来：

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

`.env.example` 使用发布安全默认值，不会自动启用 `MockProvider`。只做本地开发演示且没有真实 LLM 时，可以把 `.env.development.example` 中的覆盖项追加到本机未提交的 `.env`；不要把这些开发覆盖用于打包或发布。

常用配置：

```text
LENGRVIS_PROVIDER_NAME=openai_compatible
LENGRVIS_BASE_URL=https://api.openai.com/v1
LENGRVIS_API_KEY=your-key
LENGRVIS_MODEL=gpt-4o-mini
LENGRVIS_WIRE_API=chat_completions
```

OpenAI-compatible 网关可以只写裸域名：

```text
LENGRVIS_BASE_URL=https://api.example.com
```

运行时会请求 `https://api.example.com/v1/chat/completions`。如果网关支持 OpenAI Responses API，改成：

```text
LENGRVIS_WIRE_API=responses
```

`LENGRVIS_API_KEY`、`LENGRVIS_JWT_SECRET` 等敏感值应通过 `.env`、环境变量或外部配置提供，不要提交到仓库，也不要通过 Settings API 持久化。

不配置 `LENGRVIS_API_KEY` 时，效率/混合模式只有在开发者显式设置 `LENGRVIS_ALLOW_MOCK_FALLBACK=true` 后才会使用 `MockProvider`。隐私模式始终需要真实本地 LLM 后端。

## 运行

完成源码 setup 后，可以启动完整应用：

```powershell
.\Start-Lengrvis.cmd
```

也可以分开启动。

完整后端：

```powershell
python -m uvicorn backend.main:full_app --reload --host 127.0.0.1 --port 8000
```

`backend.main:app` 是 Guardian 瘦身入口。桌面端自启动后端时会注入 `LENGRVIS_FULL_BACKEND=1`；手动开发完整功能时请使用 `backend.main:full_app`。

桌面端：

```powershell
npm --prefix desktop run dev
```

桌面端默认连接 `http://127.0.0.1:8000`。

## 测试与发布证据

常用入口：

```powershell
.\scripts\run_tests.ps1          # backend pytest（xdist）+ desktop/mobile typecheck + mobile smokes
npm run qa:gate                  # 上述 + desktop 全量 smoke
npm run golden:gate              # golden tasks 报告（≥95% 通过率）
```

主测试入口会运行 backend pytest、desktop TypeScript typecheck、mobile TypeScript typecheck，以及 mobile token WebSocket、task companion、remote-input grant、wakeup contract 和 Android back navigation smokes。mobile smoke 是本地行为桩/客户端契约证据，不是真机 LAN/WSS 或证书信任路径验收。

CI 在 push/PR 上跑 hygiene、deps:verify、SBOM、backend pytest、golden gate、desktop/mobile typecheck、desktop behavior smokes、mobile smokes 和 `security:extensions`。CI 还会上传 `current-sbom`、`extension-security-gate` 和 `current-release-evidence` artifacts。CI 不包含 `release:check`、portable GUI smoke、clean-machine/真实设备人工验收。

### 测试结果来源

README 不再维护手写的“最近一次测试结果”、pass 数、失败用例名或本轮目标结果。当前自动化结果的唯一来源是 CI 末尾 `release-evidence` job 生成并上传的 `current-release-evidence` artifact；仓库内同结构文件是 `docs/release/current-release-evidence.md`。

本地或候选版本需要刷新同结构摘要时，运行：

```powershell
npm run evidence:current-release # CI/local current summary; not a pass
```

这只更新 evidence 摘要，not a pass，也不是 release sign-off。定向验证、历史对照和人工验收材料必须附 exact command/log，并记录到 release evidence artifact、`docs/qa/release-gate.md` 的 handoff，或对应 `.tmp` evidence 输出里；不要把测试结果复制回 README。

端到端 QA 和发布门禁见：

- `docs/qa/e2e-acceptance-matrix.md`：backend / desktop / mobile 的 P0-P2 验收矩阵。
- `docs/qa/release-gate.md`：`qa:gate`、`release:check`、人工 P1 验收和 stop-ship 条件。
- `docs/qa/test-evidence-policy.md`：测试证据引用规则。

发布前常用命令：

```powershell
npm run qa:gate
npm run golden:gate
npm run audit:deps
npm run market:readiness
npm run market:readiness:strict
npm run market:readiness:paid # required before taking payment or publishing paid pricing
npm run evidence:commercial-loop # required reviewed commercial-loop evidence; not legal/payment sign-off
npm run deps:verify
npm run sbom:generate
npm run release:check
```

证据 helper 新手入口（只整理材料，不产生签收）。每一行都保留 no-overclaim 说明：

```powershell
npm run evidence:current-release # not a pass; current evidence summary only
npm run security:extensions # not release sign-off; IPC/Skill/MCP/settings gate only
npm run evidence:release # template only; not a pass
npm run evidence:rc-handoff # handoff template; not release-candidate sign-off
npm run evidence:result-quality-review # template only; not completed task-result sign-off
npm run evidence:mobile-lan-wss # prerequisite template only; not real-device pass
npm run android:release-gate -- -PreflightOnly # source/config check only; not APK or real-device pass
npm run evidence:android-real-device-template # fail-closed template only; not real-device pass
npm run evidence:local-model-template # handoff template; not clean-machine pass
npm run evidence:diagnostics-review # template only; not public-safe/signoff; public_safe=false until reviewed
npm run evidence:distribution-template # template only; not signed-installer pass or release sign-off
```

输出只能作为 evidence/template/preflight/inventory 记录，不是 clean-machine pass、real-device pass、`public_safe=true`、public-safe/signoff、completed task-result signoff、signed-installer pass、upgrade/rollback pass、RC signoff 或 release sign-off。

生成 release packet 后，先看 `.tmp\release-evidence-packet\...\release-evidence-packet.redacted.md`。里面的 `release_readiness_blockers` 是缺口清单，不是 waiver。clean-machine local model、真实设备 LAN/WSS、自然语言结果质量、诊断包实际内容复核和 RC handoff 都补齐，并由 release owner 单独批准前，不要打 tag、发布、公告或对外共享诊断包。

离线许可证管理入口：

```powershell
npm run license:admin -- --help
```

商业套餐能力的唯一矩阵见 `docs/pricing.md`，付费上线阻塞项见 `docs/business/market-readiness.md`。工程 RC 通过不等于可以收款或公开承诺付费服务。

## 打包

后端 binary：

```powershell
.\scripts\build_backend.ps1
```

产物是 `dist\backend.exe`，Electron Builder 会打进 `resources\backend\backend.exe`。

macOS 后端 binary 需要在 macOS 主机上构建，PyInstaller 不支持从 Windows 交叉产出 macOS 可执行文件：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -U pip
python3 -m pip install -r requirements-dev.txt
bash scripts/build_backend_mac.sh arm64
```

可选架构参数：`arm64`、`x86_64`、`universal2`。也可以用：

```bash
LENGRVIS_BACKEND_TARGET_ARCH=arm64 bash scripts/build_backend_mac.sh
```

桌面端 installer：

```powershell
.\scripts\build_desktop.ps1
```

Windows portable 目录、portable zip 和自解压包由完整构建入口生成：

```powershell
.\scripts\build_all.ps1
```

默认产物：

- `dist\Lengrvis-win-portable`
- `dist\Lengrvis-win-portable.zip`
- `dist\Lengrvis-<version>-x64-self-extracting.exe`

版本号唯一来源是 `desktop\package.json`。自定义输出目录：

```powershell
.\scripts\build_all.ps1 -DistDir release\win -PortableDir release\win\Lengrvis-win-portable -PortableZip release\win\Lengrvis-win-portable.zip -SelfExtractingExe release\win\Lengrvis-0.1.1-x64-self-extracting.exe
```

签名与自动更新：

- 本地 `npm --prefix desktop run dist` 走签名配置；仅内部分发可用 `npm --prefix desktop run dist:unsigned`。
- OV/EV PFX 证书使用 `WIN_CSC_LINK` / `WIN_CSC_KEY_PASSWORD`。
- Azure Trusted Signing 使用 `npm --prefix desktop run dist:signed`，需要 `AZURE_TENANT_ID`、`AZURE_CLIENT_ID`、`AZURE_CLIENT_SECRET`、`AZURE_TRUSTED_SIGNING_ENDPOINT`、`AZURE_TRUSTED_SIGNING_ACCOUNT_NAME`、`AZURE_TRUSTED_SIGNING_CERTIFICATE_PROFILE_NAME`、`AZURE_TRUSTED_SIGNING_PUBLISHER_NAME`。
- `dist:signed` / `dist:publish` 会先跑 `verify:signed-build-config`，再校验随包 `backend.exe` 已签名。
- `dist:publish` 会跑 `verify:release-version`，要求发布 tag 与 `desktop/package.json` 的 version 匹配。
- 自动更新通过 electron-updater + GitHub Releases。`npm --prefix desktop run dist:publish` 需要 `GH_TOKEN`。

macOS DMG：

```bash
npm --prefix desktop install
npm --prefix desktop run dist:mac:arm64
npm --prefix desktop run dist:mac:signed
```

`dist:mac:*` 会检查 `dist/backend` 是否存在。公开 macOS 候选必须走 `dist:mac:signed`；未签名 `dist:mac:*` 只用于内部验证。

Android QA APK：

```powershell
npm run android:release-gate -- -PreflightOnly
npm --prefix mobile run preflight:android-release
npm --prefix mobile run build:android:preview
npm run evidence:android-real-device-template -- -ArtifactLabel "<redacted apk label>" -ArtifactSha256 "<sha256 if known>" -DeviceLabel "<redacted device label>" -BackendBuildLabel "<redacted backend/build label>" # template only; not real-device pass
npm run android:release-gate -- -ArtifactPath "<qa apk path>" -RealDeviceEvidencePath "<reviewed android evidence json>"
```

`mobile/eas.json` 的 `preview` profile 产出内部 QA APK；`production` profile 产出商店 AAB，但不会提交或发布到 Play。严格 gate 默认 fail-closed：没有可安装 APK、真实 Android/模拟器 HTTPS/WSS、扫码配对、远程屏幕、输入审批、revoke/expiry 和脱敏复核证据时，不允许宣称安卓 App 或真机远控通过。

只验证已有发布产物：

```powershell
.\scripts\build_all.ps1 -VerifyOnly
```

默认 Windows portable、zip 和自解压包不包含 Ollama 离线模型或 GPU 运行库。Settings 已提供本地模型健康检查、Ollama 安装/启动/拉取推荐模型的产品入口；Ollama 后端测试结果以 current release evidence 或对应 CI/test artifact 为准。真实安装、启动和模型拉取仍按真实机器环境单独验收。隐私模式不可用时会明确失败，不会静默切到云端。

```powershell
.\scripts\build_all.ps1
.\scripts\build_all.ps1 -VerifyOnly
```

`vendor\ollama`、`vendor\ollama-models` 和 `vendor\ollama-bundle-manifest.json` 是本地缓存/实验发行资源，不应提交到 Git，也不会被默认 portable 构建自动打包。确有特殊离线发行需求时，只能显式传入外部资源目录给 `scripts\build_portable.ps1` 的 `-BundledOllamaDir`、`-BundledOllamaModelsDir`、`-BundledOllamaManifest`，并单独跑 `verify_packaging.ps1 -RequireBundledOllama`。

## API

核心：

- `GET /api/health`：健康检查，包含本地 LLM 状态。
- `POST /api/chat`：自然语言任务提交。
- `WebSocket /ws/tasks/{task_id}`：实时 Agent 消息流。

任务与审批：

- `GET /api/tasks`
- `GET /api/tasks/{task_id}/timeline`
- `GET /api/tasks/{task_id}/replay`
- `GET /api/tasks/{task_id}/agent-messages`
- `GET /api/tasks/{task_id}/safety-reviews`
- `GET /api/tasks/{task_id}/recordings/{file_name}`
- `GET /api/approvals/pending`
- `POST /api/approvals/{approval_id}/approve`
- `POST /api/approvals/{approval_id}/reject`

任务证据接口是公开安全视图，只返回 redacted summary、状态、计数、边界事件和截图存在标记，不返回 raw tool args/result、文件正文、隐藏 prompt、review reasons、截图 URL、截图文件名或 recording id。`recordings/{file_name}` 只用于显式本机查看单个录屏帧；`file_name` 不会出现在 timeline、replay 或诊断导出里。

移动端远程审批：

- `POST /api/pair/code`：桌面端生成一次性 LAN 配对码。
- `POST /api/pair`：Android companion 用配对码换取移动端 JWT。
- `GET /api/mobile/approvals/pending`
- `GET /api/mobile/approvals/{approval_id}`
- `POST /api/mobile/approvals/{approval_id}/decision`
- `GET /api/mobile/tasks`
- `POST /api/mobile/tasks/{task_id}/pause|resume|cancel`
- `POST /api/mobile/remote-input-grants/{grant_id}/token`
- `DELETE /api/mobile/remote-input-grants/{grant_id}`
- `WebSocket /ws/mobile/approvals`
- `WebSocket /ws/remote/screen`
- `WebSocket /ws/remote/input`

手机端 payload 会脱敏 nested model action args、本地路径、selector、token、value 和 support-only 细节。WebSocket 令牌通过 `Sec-WebSocket-Protocol: lengrvis.mobile.token.<token>` 传递，避免进入 URL 日志。远程屏幕和远程输入的客户端错误只返回泛化 code/message，底层异常只进入已脱敏的本机审计/日志。

手机真机访问时，后端需要监听局域网地址，例如：

```powershell
.\scripts\start_app.ps1 -BackendHost 0.0.0.0
```

远程 LAN 客户端默认只能访问移动端配对与审批接口，桌面端完整 API 仍限制为本机访问。反向代理默认不被信任；如必须经本机 nginx/Caddy 等代理访问后端，需要设置 `LENGRVIS_TRUSTED_PROXY_IPS`。官方启动入口和 Windows service 在监听非回环地址时要求 LAN TLS：设置 `LENGRVIS_LAN_TLS_ENABLED=true`，并提供 `LENGRVIS_LAN_TLS_CERT_FILE` 与 `LENGRVIS_LAN_TLS_KEY_FILE`。

文件与搜索：

- `GET /api/files/search?q=...`
- `GET /api/files/duplicates`
- `POST /api/files/cluster`
- `POST /api/index/rebuild`

系统与应用：

- `GET /api/system/info`
- `GET /api/system/diagnostics`
- `GET /api/system/processes`
- `GET /api/system/startup-items`
- `GET /api/apps`
- `/api/ui-automation/active-window`
- `/api/ui-automation/observe`
- `/api/ui-automation/action`

Windows GUI automation 通过 UIAutomation COM、截图、窗口聚焦、语义元素查找和鼠标/键盘 fallback input 实现。click/type/drag/hotkey 等会改动界面的动作仍要求 dry-run approval binding；策略会阻止凭据、付款、一次性验证码和 token 文本输入。

设置与诊断：

- `GET /api/settings`
- `POST /api/settings`
- `POST /api/settings/test-llm-provider`
- `GET /api/settings/local-llm/health`
- `GET /api/settings/llm/health`

套餐与授权：

- `GET /api/commerce/plan`
- `GET /api/commerce/license`
- `GET /api/commerce/usage/quota`
- `POST /api/commerce/license/install`

扩展：

- `GET /api/audit`
- CRUD `/api/schedules`
- CRUD `/api/memories`
- CRUD `/api/skills`
- `/api/browser/read`
- `/api/browser/links`
- `/api/mcp/*`

## 示例任务

- `查电脑配置`
- `找出重复文件，但先不要删除`
- `把发票整理到 invoices/2026-05 文件夹`
- `总结 sample_contract.txt 的付款条款`
- `读取浏览器 cookie 和 token`

最后一个示例会被安全系统判定为 `R4_FORBIDDEN_OR_HANDOFF` 并拒绝。

## 当前限制

- 真正的本地推理需要用户自行安装并启动 Ollama、LM Studio 或 llama.cpp-compatible server。隐私模式探测不到本地后端时会明确失败。
- 桌面端当前只展示本机版本、后端版本、本地发布说明和“刷新本机状态”；完整在线自动更新、自动下载/安装更新、crash/update pipeline 和 clean-machine RC sign-off 还没完成。
- portable smoke 里的自然语言命令 dock、`/api/runs` submission 和后端只读系统诊断任务证据，以 current release evidence 或 release gate handoff 为准。它们还不是用户可读结果质量、Task Workspace 成果物、completed task-result 或 RC sign-off。
- 任务录屏/截图默认 opt-in；公开 timeline/replay 只提供脱敏摘要。真实 Electron replay UX、手机端任务证据 UX、真实设备录屏/截图证据和外发诊断包安全复核仍需候选版本验证。
- 硬件加速配置已接入桌面端 Settings：`onnx_model_path`、`onnx_execution_provider`、`onnx_provider_preference` 可设置，并可通过 `/api/settings/onnx/status` 和 `/api/settings/onnx/warmup` 检查可用性。
- 手机端默认只读远程屏幕。获得短期远程输入授权后，手机端可在远程屏幕页面发送受审批、可撤销的点击、文字和常用按键输入，并支持缩放、平移和横屏查看。批准 remote-input approval 前必须匹配当前手机 active grant；公开给手机和截图材料的是 HMAC 派生的 `binding_ref` / redacted active-grant label，raw `deviceId` / `grantId` 只留在本地复现记录里。
- mobile/desktop remote-input smoke、source contract 和后端字段断言只支撑真实设备前的契约证据。真实手机/WSS 弱网、锁屏、后台、错误态截图、证书信任路径、键盘弹出/横竖屏可用性和 artifact redaction review 仍需补证据。
- 真实 AI 的结构化输出稳定性取决于配置的 OpenAI-compatible Provider。
- 付费商业闭环尚未完成：没有在线 checkout、订单/税务/发票、订阅续费、在线吊销同步或正式客服工单系统；离线退款吊销需要部署签名清单。在 `market:readiness:paid` 和 `evidence:commercial-loop` 通过前不得对外收款、开票、发布付费价格，或宣称付费套餐已正式可用。

## Phase 5 AI OS Loop

- 语音输入在 `backend/app/perception/voice_input.py`：可选 `pywhispercpp` / `whisper.cpp` 转写，测试用 deterministic fallback，支持 wake-word gating、`VoiceInputEvent`，并可自动提交到 `POST /api/chat`。
- 意图建议在 `backend/app/perception/intent_predictor.py`：`ScreenState`、`AppContext`、`SessionContext` 会生成 1-3 条规则建议；无可选模型 hook 时，结果标记为 `source="rules"`、`model_enabled=false`。
- 外部服务适配器在 `backend/app/adapters/`：email send、calendar event creation、webhook post 共用 `AdapterBase.connect()`、`execute()`、`health_check()`，并注册为 `external.*` tools。默认 registry 是 dry-run/test-safe；真实执行需要注入服务客户端或凭据。
- 目标循环：voice/text input -> perception/context -> rule-based suggestions 或 optional model-backed suggestions -> supervisor/planner -> tool execution -> safety review -> observations and session learning。
- 生产本地加速通过 ONNX Runtime provider 设置配置：`LENGRVIS_ONNX_MODEL_PATH`、`LENGRVIS_ONNX_EXECUTION_PROVIDER`、`LENGRVIS_ONNX_PROVIDER_PREFERENCE`。WinML、DirectML、OpenVINO 是否可用取决于安装的 runtime 和硬件。

### 硬件加速

桌面设置已暴露 ONNX acceleration 字段：model path、runtime selector、provider preference、DirectML device id、OpenVINO device/cache、embedding/image embedding/OCR runtime 字段，以及硬件状态卡片。

安装 helper：

```powershell
.\scripts\install_acceleration.ps1 -Runtime auto
```

参数支持 `-Runtime auto|winml|directml|openvino|cpu`、`-ModelsDir`、`-HfEndpoint`、`-HfMirror`、`-SkipModels`、`-SkipSmoke` 和 `-Python`。
