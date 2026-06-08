# Lengrvis

## 平台支持矩阵

| 平台 | 状态 | 当前交付 | 已知限制 |
| --- | --- | --- | --- |
| Windows 桌面 | Supported | Electron 桌面、FastAPI 后端、Windows portable/zip/SFX 打包、任务工作台、审批、文件/文档/系统工具 | 发布包已有 portable 首屏 smoke：packaged renderer 已观察到 `/api/system/diagnostics`，NL 命令 dock 已观察到 `/api/runs` 和后端只读系统诊断任务证据；仍需 clean-machine、真实设备和候选版本人工验收。 |
| Android Companion | Preview | 配对、移动审批、任务监督、暂停/继续/取消、只读屏幕流、受控远程输入授权 | 需要与电脑在可访问网络内；完整应用商店分发未完成。 |
| macOS 桌面 | Preview | macOS 后端构建脚本与 DMG 脚本存在 | 不作为 0-90 天主线，需在 macOS 主机验证。 |
| iOS Companion | Planned | 暂不交付 | 等 Android companion 闭环稳定后再排期。 |

## 普通用户快速开始

1. 双击 `启动 Lengrvis.cmd` 启动 Lengrvis。
2. 正式发布包会直接启动已打包好的产物，不会在第一次启动时现场运行 `pip install` 或 `npm install`。命令行窗口会显示“正在启动”“已启动”或失败原因。
3. 启动成功后会打开 Lengrvis 桌面窗口。首屏可以直接从“整理下载目录、总结本地文档、查找大文件、检查电脑状态、文档问答”开始，每个模板都会显示本机处理、云端边界、审批、回滚和预计耗时。
4. 如果启动失败，请先双击 `Start-Lengrvis-Debug.cmd`，它会把最近的错误日志打印出来；完整日志在 `logs` 文件夹。
5. 如果你下载的是源码或 Git 仓库，请先看下面的“源码开发 setup”；源码依赖安装不属于普通用户启动路径。

## 普通用户配置与诊断入口

- 配置 AI、隐私模式、本地模型、硬件加速和手机配对时，优先打开桌面窗口里的“设置”。普通用户不需要手动编辑 `.env` 或 `config.yaml`。
- 应用能打开但任务异常时，打开“系统信息”，先刷新只读诊断；需要反馈问题时再点“导出诊断包”。诊断包会包含版本、服务状态、本机路径、网络接口、进程和启动项摘要，但不包含你的文档正文或密钥。
- 应用打不开时，双击 `Start-Lengrvis-Debug.cmd`，它会显示已脱敏的最近启动日志摘要和下一步。

## 产品说明

这是一个 Windows 优先的本机 OS agent / 电脑管家原型。它不是某个竞品的替代品，也不主打云端万能工作台；当前最清晰的差异化是围绕用户自己的电脑做可审计、可扩展、可自托管的任务执行：用自然语言描述目标，系统通过多 Agent 协作理解任务、规划步骤、调用本地工具，并在修改文件或系统设置前进行安全审核和用户确认。

当前版本不是纯聊天机器人，也不是开发者控制台。桌面端第一屏已经改成消费级电脑助手体验：一句话任务入口、隐私/混合/效率模式、文件/文档/图片/电脑/应用/网页能力卡、手机审批与屏幕查看入口、Agent 进度和安全审批。与发布级 OS AI 产品相比，它仍需要补齐本地模型开箱即用、跨端分发、App 深度集成和真实设备验收。

## 架构

```text
desktop/                 Electron + React + TypeScript 桌面端
backend/app/             FastAPI 后端、Agent、策略、工具、索引、服务
backend/tests/           pytest 契约测试和 smoke 测试
scripts/                 PowerShell 启动、开发 setup、测试和打包脚本
test_data/               授权目录、策略和隐私测试数据
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
- Token 预算配置：`LENGRVIS_MODEL_CONTEXT_WINDOW`、`LENGRVIS_MODEL_AUTO_COMPACT_TOKEN_LIMIT`、`LENGRVIS_CONTEXT_*`。默认保留输出预算，接近阈值时自动摘要旧消息并保留最近消息尾部。

### 安全
- 风险等级：`R0_READ_ONLY`、`R1_OPEN_ONLY`、`R2_REVERSIBLE_MODIFY`、`R3_DESTRUCTIVE_OR_SYSTEM`、`R4_FORBIDDEN_OR_HANDOFF`。
- R2/R3 操作会生成 dry-run 预览和审批记录。
- R4 请求会直接拒绝，例如读取浏览器 cookie、token、密码。
- 路径沙盒：拦截符号链接逃逸、`..` 穿越、系统敏感路径。
- **SafetyReview 批量审查**：低风险消息走确定性快速通道，高风险消息批量送 LLM 审核。
- 全链路审计日志 + 自动 PII 脱敏。

### 文件与文档
- 授权目录文件搜索、FTS5 全文索引、重复文件检测。
- **向量语义搜索**：FTS5 候选召回 → Embedding rerank → cosine similarity → 按文件折叠。
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
.\scripts\run_tests.ps1
```

主测试入口会运行 backend pytest、desktop TypeScript typecheck、mobile TypeScript typecheck，以及 mobile token WebSocket smoke。
同时会运行 mobile remote-input grant smoke，避免发布门禁漏掉远程输入授权边界。

最近一次记录的核心门禁验证结果：

```text
backend: 1337 passed, 1 skipped
desktop typecheck passed
mobile typecheck passed
mobile token smoke passed
mobile remote-input grant smoke passed
desktop smoke passed
```

跳过项是当前 Windows shell 没有创建符号链接权限。

端到端 QA 和发布门禁见：

- `docs/qa/e2e-acceptance-matrix.md`：backend / desktop / mobile 的 P0-P2 验收矩阵。
- `docs/qa/release-gate.md`：发布前 `qa:gate`、产物 `release:check`、人工 P1 验收和 stop-ship 条件。

快速发布前置门禁：

```powershell
npm run qa:gate
```

已有 Windows 发布产物时再跑产物门禁：

```powershell
npm run release:check
```

发布候选若需要证明打包 GUI 首屏和只读任务入口，再跑 `npm run smoke:portable-first-screen`。最新开发工作区证据目录为 `.tmp\portable-first-screen-smoke\run-20260608-154045-41396-6013e259`：只读入口观察到 packaged renderer `/api/system/diagnostics`；自然语言 dock 观察到 `/api/runs` 与后端 read-only/system diagnostics task evidence。该证据不能替代 clean-machine、真实设备或人工 RC sign-off。移动/LAN 演示的 TLS 仅按显式设备信任路径记录，不代表系统级证书链已完成。

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

默认产物为 `dist\Lengrvis-win-portable`、`dist\Lengrvis-win-portable.zip` 和 `dist\Lengrvis-0.1.0-x64-self-extracting.exe`。发布到自定义目录时，这些参数会贯穿 backend、portable、zip、SFX 和最终验证：

```powershell
.\scripts\build_all.ps1 -DistDir release\win -PortableDir release\win\Lengrvis-win-portable -PortableZip release\win\Lengrvis-win-portable.zip -SelfExtractingExe release\win\Lengrvis-0.1.0-x64-self-extracting.exe
```

macOS DMG：

```bash
npm --prefix desktop install
npm --prefix desktop run dist:mac:arm64
```

`dist:mac:*` 会先检查 `dist/backend` 是否存在，避免打出缺后端的包。产物：`desktop/release/Lengrvis-0.1.0-arm64.dmg`。打 `x64` 时先用 `bash scripts/build_backend_mac.sh x86_64` 生成匹配的 `dist/backend`，再运行 `npm --prefix desktop run dist:mac:x64`。

只验证已有发布产物：

```powershell
.\scripts\build_all.ps1 -VerifyOnly
```

默认 Windows portable、zip 和自解压包不包含 Ollama 离线模型或 GPU 运行库。Settings 已提供本地模型健康检查、Ollama 安装/启动/拉取推荐模型的产品入口；当前仍按真实机器环境执行，隐私模式不可用时会明确失败，不会静默切到云端。

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
- `GET /api/tasks`、`GET /api/tasks/{task_id}/timeline`、`GET /api/tasks/{task_id}/agent-messages`、`GET /api/tasks/{task_id}/safety-reviews`
- `GET /api/approvals/pending`、`POST /api/approvals/{approval_id}/approve`、`POST /api/approvals/{approval_id}/reject`

移动端远程审批：
- `POST /api/pair/code` — 桌面端生成一次性 LAN 配对码
- `POST /api/pair` — Android 伴侣 App 用配对码换取移动端 JWT
- `GET /api/mobile/approvals/pending`、`POST /api/mobile/approvals/{approval_id}/decision` — Bearer JWT 保护的审批接口
- `GET /api/mobile/tasks`、`POST /api/mobile/tasks/{task_id}/pause|resume|cancel` — 手机端监督电脑任务，不暴露内部 plan args。
- `POST /api/mobile/remote-input-grants/{grant_id}/token`、`DELETE /api/mobile/remote-input-grants/{grant_id}` — 手机端领取或结束短期远程输入授权。
- `WebSocket /ws/mobile/approvals` — 手机端订阅审批创建/决策事件；令牌通过 `Sec-WebSocket-Protocol: lengrvis.mobile.token.<token>` 传递，避免进入 URL 日志。

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
- 硬件加速配置已接入桌面端 Settings：可设置 `onnx_model_path`、`onnx_execution_provider`、`onnx_provider_preference`，并通过 `/api/settings/onnx/status` 和 `/api/settings/onnx/warmup` 做可用性检查。
- Windows GUI automation is implemented through UIAutomation COM, screenshots, window focus, semantic element lookup, and mouse/keyboard fallback input. Mutating GUI actions still require dry-run + user approval, and policy blocks credential, payment, one-time-code, and token text entry.
- 手机端默认只读远程屏幕；远程屏幕令牌通过 WebSocket 子协议传递，并按 `remote:view` scope 校验。获得短期远程输入授权后，手机端可在远程屏幕页面发送受审批、可撤销的输入。
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
