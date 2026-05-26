# Marvis Agent EXE

这是一个 Windows 优先的本地电脑 AI 管家原型，定位更接近 Marvis 式个人 OS Agent：用户用自然语言描述目标，系统通过多 Agent 协作理解任务、规划步骤、调用本地工具，并在修改文件或系统设置前进行安全审核和用户确认。

当前版本不是纯聊天机器人，也不是开发者控制台。桌面端第一屏已经改成消费级电脑助手体验：一句话任务入口、隐私/混合/效率模式、文件/文档/图片/电脑/应用/网页能力卡、手机远控预留入口、Agent 进度和安全审批。

## 架构

```text
desktop/                 Electron + React + TypeScript 桌面端
backend/app/             FastAPI 后端、Agent、策略、工具、索引、服务
backend/tests/           pytest 契约测试和 smoke 测试
scripts/                 PowerShell 开发、测试和打包脚本
test_data/               授权目录、策略和隐私测试数据
```

运行时流程：

```text
用户 -> OrchestratorAgent -> PlannerAgent -> SafetyReviewAgent
    -> 专家 Agent 评议 -> ToolRegistry -> Observation
    -> SafetyReviewAgent -> 下一步 / 审批 / 完成
```

## 已实现能力

### 核心架构
- 自然语言任务提交：桌面端或 `POST /api/chat`。
- 12 个运行时 Agent：Orchestrator、Planner、Supervisor、File、Document、Computer、App、Browser、Search、Memory、SafetyReview、HumanGate。
- **副 Agent 自主推理**：Orchestrator 在执行工具前调用所属副 Agent 的 `act()` 方法，副 Agent 可提议修正工具参数、请求重规划或跳过步骤。
- **Step 级并行执行**：Plan 中无依赖的步骤通过 `asyncio.gather` 并发执行，有依赖的步骤按拓扑排序串行。
- **34 个外部化 Prompt 文件**：所有 Agent system prompt 和 LLM 任务模板均存放在 `backend/app/llm/prompts/` 目录，可独立调整。

### LLM 与推理
- OpenAI-compatible 真实 AI 接入：`base_url`、`api_key`、`model`、`wire_api` 可配置；支持 `chat/completions` 与 `responses` 两种 OpenAI 格式。
- OpenAI-compatible `base_url` 可以填写裸域名或完整 `/v1` API base，例如 `https://api.example.com` 会自动归一化为 `https://api.example.com/v1`；已有 `/v1` 或自定义代理 path 不会重复改写。
- 三模式 Provider 路由：默认效率（云端）/ 隐私（本地）/ 混合（按任务类型分流）。
- 只有隐私模式或混合模式的本地任务会探测 Ollama、LM Studio、llama.cpp-compatible server；未检测到本地 LLM 时明确报错，不再静默回退 `MockProvider`。
- `MockProvider` 仅用于开发、测试和非隐私路径的演示兜底。
- ONNX Runtime Provider 框架（DirectML/NPU 路径预留）。
- 上下文管理运行时：所有 `get_provider()` 返回的 LLM provider 都会先经过统一 ContextManager，按 `tool result budget -> history snip -> micro-compact -> session memory -> auto-compact -> LLM call -> prompt-too-long reactive retry` 控制模型可见上下文；原始 AgentBus/DB 历史不删除。
- Token 预算配置：`MARVIS_MODEL_CONTEXT_WINDOW`、`MARVIS_MODEL_AUTO_COMPACT_TOKEN_LIMIT`、`MARVIS_CONTEXT_*`。默认保留输出预算，接近阈值时自动摘要旧消息并保留最近消息尾部。

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

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -r requirements-dev.txt
npm --prefix desktop install
```

可选真实 AI 配置：

```powershell
Copy-Item .env.example .env
notepad .env
```

设置：

```text
MARVIS_PROVIDER_NAME=openai_compatible
MARVIS_BASE_URL=https://api.openai.com/v1
MARVIS_API_KEY=your-key
MARVIS_MODEL=gpt-4o-mini
MARVIS_WIRE_API=chat_completions
```

OpenAI-compatible 网关也可以写裸域名：

```text
MARVIS_BASE_URL=https://api.example.com
```

运行时会自动请求 `https://api.example.com/v1/chat/completions`。如果网关支持 OpenAI Responses API，可改为：

```text
MARVIS_WIRE_API=responses
```

`MARVIS_API_KEY`、`MARVIS_JWT_SECRET` 等敏感值应通过 `.env`、环境变量或外部配置提供，不要提交到仓库，也不要通过 Settings API 持久化。

不配置 `MARVIS_API_KEY` 时，效率/混合模式可按 `MARVIS_ALLOW_MOCK_FALLBACK` 使用 `MockProvider` 做开发演示。隐私模式始终需要真实本地 LLM 后端。

## 运行

启动后端：

```powershell
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

启动桌面端：

```powershell
npm --prefix desktop run dev
```

桌面端默认连接 `http://127.0.0.1:8000`。

## 测试

```powershell
.\scripts\run_tests.ps1
```

当前验证结果：

```text
backend: 855 passed, 1 skipped
desktop build passed
mobile typecheck passed
```

跳过项是当前 Windows shell 没有创建符号链接权限。

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

可选架构参数为 `arm64`、`x86_64`、`universal2`；也可以用 `MAVRIS_BACKEND_TARGET_ARCH=arm64 bash scripts/build_backend_mac.sh`。产物：`dist/backend`，Electron Builder 会打进 `Mavris.app/Contents/Resources/backend/backend`。

桌面端 installer/portable：

```powershell
.\scripts\build_desktop.ps1
```

macOS DMG：

```bash
npm --prefix desktop install
npm --prefix desktop run dist:mac:arm64
```

`dist:mac:*` 会先检查 `dist/backend` 是否存在，避免打出缺后端的包。产物：`desktop/release/Mavris-0.1.0-arm64.dmg`。打 `x64` 时先用 `bash scripts/build_backend_mac.sh x86_64` 生成匹配的 `dist/backend`，再运行 `npm --prefix desktop run dist:mac:x64`。

完整构建：

```powershell
.\scripts\build_all.ps1
```

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
- `WebSocket /ws/mobile/approvals?token=...` — 手机端订阅审批创建/决策事件

Android 伴侣 App 位于 `mobile/`，可用 `npm --prefix mobile run android` 启动。手机真机访问时，后端需要监听局域网地址，例如 `.\scripts\start_app.ps1 -BackendHost 0.0.0.0`；远程 LAN 客户端默认只能访问移动端配对与审批接口，桌面端完整 API 仍限制为本机访问。

文件与搜索：
- `GET /api/files/search?q=...`、`GET /api/files/duplicates`、`POST /api/files/cluster`
- `POST /api/index/rebuild`

系统与应用：
- `GET /api/system/info`、`GET /api/system/diagnostics`、`GET /api/system/processes`、`GET /api/system/startup-items`
- `GET /api/apps`

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
- NPU 加速（WinML / OpenVINO / DirectML）尚未集成，本地推理走 CPU。
- pywinauto / 复杂 GUI 自动化是预留接口。
- 手机远控目前是产品入口预留，还没有真实跨端通道。
- 真实 AI 的结构化输出稳定性取决于配置的 OpenAI-compatible Provider。

## Phase 5 AI OS Loop

- Voice input is available through `backend/app/perception/voice_input.py`: optional `pywhispercpp` / `whisper.cpp` transcription, deterministic fallback for tests, wake-word gating, `VoiceInputEvent`, and automatic submission to `POST /api/chat`.
- Intent prediction is available through `backend/app/perception/intent_predictor.py`: `ScreenState` + `AppContext` + `SessionContext` become 1-3 proactive suggestions, filtered at confidence `> 0.8`, with a quiet floating suggestion card in the desktop chat panel.
- External service adapters live under `backend/app/adapters/`: email send, calendar event creation, and webhook post share `AdapterBase.connect()`, `execute()`, and `health_check()`, and are registered as `external.*` tools with dry-run previews and R2 approval flow. Live execution requires injecting real service clients or credentials in deployment; default registry instances are dry-run/test-safe.
- The intended loop is: voice or text input -> perception/context -> intent prediction -> supervisor/planner -> tool execution -> safety review -> observations and session learning.
- Production local acceleration is configured through the existing ONNX Runtime provider settings (`MARVIS_ONNX_MODEL_PATH`, `MARVIS_ONNX_EXECUTION_PROVIDER`). DirectML/NPU provider selection is wired as a deploy-time setting; actual provider availability still depends on the installed runtime and hardware.
