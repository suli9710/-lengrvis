# Mavris vs 腾讯 Marvis 差距审计报告

> 版本：2026-05-25 实证修订版
> 审计方法：对 `backend/`、`desktop/`、`scripts/` 全部源码逐模块静态分析，与腾讯 Marvis 公开能力做逐项对比
> 审计范围：backend（Python 多 Agent runtime）+ desktop（Electron 桌面端）+ scripts（构建打包）+ 22 个 pytest 通过、self-extracting EXE 已产出

## 一、对标对象速览：腾讯 Marvis（2026/05/20 上线）

| 维度 | Marvis 公开事实 |
|---|---|
| 定位 | 操作系统级个人 AI 助手，深度嵌入 OS 层 |
| 团队 | 腾讯应用宝团队；https://marvis.qq.com |
| 平台 | Windows / macOS / Android 已发布；iOS 推进中 |
| 智能体 | **1 PM Agent + 5 专项 Agent**（File / Computer / App / Browser / Search） |
| 模型 | 云端 Hunyuan + DeepSeek V4；端侧 Qwen 自研 |
| 加速 | Microsoft Foundry + WinML + Intel OpenVINO NPU |
| 核心能力 | AI 智能体 / 定时任务 / 文件聚类 / 图片聚类 / 应用聚类 / 磁盘管理 |
| 多模态 | OCR / 人像识别 / 主题识别 / 场景识别 / 地点识别 |
| 隐私 | 端侧大模型，断网可用；门槛 16 核 / 32G / 16G 显存 / 35G SSD |
| 跨端 | 手机实时接管 PC（含锁屏解锁）；PC 通过安卓 VM 操作手机 App |
| 安全 | L2 硬垂询：批量删除 / 系统核心配置弹窗确认；支付强制本人操作 |
| UX | 6 只 Agent "虚拟办公室"（打盹 / 喝咖啡 / 健身 / 坐工位） |
| 商业 | 每用户每天 1000 万 Token 免费 |

---

## 二、Mavris 当前实证盘点（2026-05-25 代码状态）

### A. 已生产可用（38 项能力）

| # | 能力 | 实现位置 | 状态 |
|---|---|---|---|
| 1 | 多 Agent 编排状态机 + 安全监督全链路 | `orchestrator_agent.py` | 完整：R0-R4 风险 × dry-run × 审批 Gate × 运行时消息审查 |
| 2 | **副 Agent act() 自主推理路由** | `orchestrator_agent.py:294` `_consult_subagent()` | **完整**：orchestrator 在工具执行前调副 Agent，正确处理 propose_tool / request_revision / done 三种 AgentAction |
| 3 | **6 个副 Agent 领域 prompt** | `backend/app/llm/prompts/{file,document,computer,app,browser,search}_agent.md` | **完整**：34 个 .md prompt 文件，全部外部化 |
| 4 | 5 级风险分级 + 敏感词检测 + 浏览器写二审 | `policy_engine.py` | 完整 |
| 5 | 路径沙盒（符号链接逃逸 / `..` 穿越 / 系统路径） | `paths.py` | 完整 |
| 6 | 文件工具集 15 个 + send2trash 真实回收站 | `file_tools.py` | 完整 |
| 7 | **文档 AI 摘要**（map-reduce 分块 + LLM） | `document_service.py:149-213` | **完整**：单 chunk 直接摘要，多 chunk map-reduce，extractive fallback |
| 8 | **文档 AI QA**（chunk 检索 + rank + LLM + 引用） | `document_service.py:216-277` | **完整**：chunk_document → rank_chunks → LLM 回答 + citation labels |
| 9 | **文档 AI 报告生成**（LLM 结构化） | `document_service.py:302-342` | **完整**：分块 + prompt 模板 + extractive fallback |
| 10 | 文档抽取 PDF/DOCX/XLSX/PPTX/CSV | `document_tools.py:16-87` | 完整 |
| 11 | 系统读取（psutil / winreg / 磁盘 / 电池 / 启动项） | `system_tools.py` | 完整 |
| 12 | 应用扫描 + MSI 卸载 | `app_tools.py` | 完整 |
| 13 | 浏览器只读（Playwright + httpx fallback） | `browser_tools.py:65-156` | 完整 |
| 14 | 浏览器写操作（navigate / click / fill / submit / wait + dry_run） | `browser_tools.py:168-309` | 完整 |
| 15 | MemoryAgent（embed + cosine + DB + TTL + tags） | `memory_agent.py` | 完整 |
| 16 | MCP 客户端 + Registry（JSON-RPC 2.0 over HTTP） | `mcp/client.py` + `registry.py` | 完整 |
| 17 | 视觉工具（describe / OCR / compare via vision endpoint） | `vision_tools.py` | 完整 |
| 18 | 聚类工具（k-means + hashing trick；文件/应用/图片） | `cluster_tools.py` | 完整 |
| 19 | 回滚工具（逆序重放 rollback_info） | `rollback_tools.py` | 完整 |
| 20 | 定时调度器（croniter + async tick + 真实执行） | `scheduler_service.py:51-194` | 完整 |
| 21 | TaskPool（并发限流，默认 3） | `task_pool.py` | 完整 |
| 22 | **本地 LLM Provider 探测链** | `local_provider.py` | **完整**：ONNX → Ollama → LM Studio → llama.cpp → `LocalBackendUnavailable`（不再静默回退 MockProvider） |
| 23 | **隐私模式拒绝 Mock fallback** | `registry.py:86` `_build_local_provider()` | **完整**：raises `LocalBackendUnavailable`，前端可显示明确错误 |
| 24 | **本地 LLM 健康检查 API** | `local_provider.py:87` `health_snapshot()` | **完整**：返回可用性 + 选中后端 + 探测顺序 + ONNX 状态 |
| 25 | **向量索引 + 语义搜索** | `vector_index.py` | **完整**：FTS5 候选召回 → embedding rerank → cosine similarity → 按文件折叠 |
| 26 | **Embedding 服务** | `embedding_service.py` | **完整**：provider.embed + hashing fallback |
| 27 | **Step 级并行执行** | `orchestrator_agent.py:149-254` | **完整**：`_build_step_graph` + `asyncio.create_task` + `asyncio.wait`，含依赖拓扑 |
| 28 | **Skill 包加载 + 安全审查 + 沙盒** | `skills/loader.py` + `sandbox.py` + `schemas.py` | **完整**：YAML manifest / R4 阻断 / 路径逃逸 / 敏感 header 检测 / 动态注册 |
| 29 | **WebSocket 实时任务流** | `routes_chat.py:27` `/ws/tasks/{task_id}` | **完整**：`agent_bus.subscribe()` + asyncio.Queue |
| 30 | **离线 OCR** | `ocr_service.py` | **完整**：本地 Tesseract → 元数据 OCR → 云 vision fallback；PDF 图片自动 OCR |
| 31 | **严格状态机模式** | `state_machine.py:97` | **完整**：`is_transition_allowed(strict=True)` + `StateTransitionError` |
| 32 | **SafetyReview 批量审查** | `safety_review_agent.py:16` `BatchMessageReview` | **完整**：fast_path_count / slow_review_count / short_circuited |
| 33 | 三模式 Provider 路由（efficiency / privacy / hybrid） | `llm/registry.py` | 完整 |
| 34 | OpenAI 兼容 LLM 双 API（chat/completions + responses） | `openai_compatible.py` | 完整 |
| 35 | FTS5 全文索引 + 重复文件检测 | `fts_index.py` | 完整 |
| 36 | 审计日志（自动 PII 脱敏，写 SQLite） | `audit.py` + `redaction.py` | 完整 |
| 37 | Excel COM 自动化（status / read / write_cell） | `app_excel.py` | 完整 |
| 38 | Electron 桌面端（7 PonyAgent 办公室 + 7 视图） | `App.tsx` | 完整 |

### B. 半成 / 占位（4 项）

| # | 能力 | 位置 | 当前状态 | 缺口 |
|---|---|---|---|---|
| 1 | **文件监视器** | `indexer/file_watcher.py` | `return {"watching": paths, "note": "reserved"}` | 缺 inotify/ReadDirectoryChangesW |
| 2 | **通知服务** | `services/notification_service.py` | `return {"queued": False, "message": message}` | 缺真实通知投递 |
| 3 | `document.analyze_xlsx` | `document_tools.py:138-141` | 仅 `text[:2000]` 预览 | 未接 LLM 分析 |
| 4 | `human_gate_agent.py` | 类壳 | HITL 通过 `Approval` 模型已走通 | 无实际影响 |

### C. 完全缺失（与 Marvis 存在量级差距）

| # | 维度 | Marvis | Mavris | 缺口规模 |
|---|---|---|---|---|
| 1 | **端侧模型开箱即用** | 内置 Qwen 端侧，装完即用 | 需用户自行安装 Ollama + 拉取模型 | **体验差距大** |
| 2 | **NPU 硬件加速** | WinML + OpenVINO + DirectML | ONNX 框架在，无实际量化模型集成 | 大 |
| 3 | **跨端 Android** | 已发布 | 完全无 | 大 |
| 4 | **跨端 iOS** | 推进中 | 完全无 | 大 |
| 5 | **手机远程接管 PC** | 实时视觉控制 + 锁屏解锁 | 前端 placeholder | 大 |
| 6 | **PC 操作手机 App** | 安卓 VM，已 demo 同花顺等 | 完全无 | 大 |
| 7 | **第三方 App 深度授权** | 4+ 商业 App 已对接 | 仅 Excel COM + notepad/calc | 中 |
| 8 | **实时桌面视觉控制** | 手机端看 PC 桌面 | 无 | 大 |

### D. Mavris 反而占优

| 维度 | Mavris 实现 | 备注 |
|---|---|---|
| 5 级精细风险分级（R0-R4） | `policy_engine.py` | Marvis 只到 L2 硬垂询 |
| SQLite 全链路审计 + 自动 PII 脱敏 | `audit.py` + `redaction.py` | Marvis 未公开同等审计 |
| 7 只 PonyAgent 办公室可视化 | `App.tsx` | 比 Marvis 6 只更细（多 DocumentAgent） |
| 路径沙盒 + 符号链接逃逸拦截 | `paths.py` | Marvis 未公开 |
| Skill 安全审查（R4 阻断 / 路径逃逸 / 敏感 header） | `skills/loader.py:138-206` | Marvis 未公开同等审查 |
| 文档 QA 带引用（chunk citation labels） | `document_service.py` | Marvis 未公开引用机制 |
| MockProvider 离线兜底（保证演示和测试不崩） | `llm/mock_provider.py` | Marvis 无（依赖云端） |

---

## 三、对比矩阵

| 维度 | Marvis | Mavris 实证 | 差距 | 优先级 |
|---|---|---|---|---|
| 主 Agent 编排 | 1+5 | Orchestrator+Planner+Supervisor+SafetyReview+Memory+6 副 | 相当 | — |
| 副 Agent 自主推理 | 5 个独立推理 | `_consult_subagent()` + act() 已接通 | 相当 | — |
| 副 Agent 领域 prompt | 5 套专家 prompt | 34 个 .md 文件已外部化 | 相当 | — |
| 文档 AI 摘要/QA/报告 | LLM 驱动 | map-reduce + QA + report 已接 LLM | 相当 | — |
| 长期记忆 | 个人 KB + 索引 | MemoryAgent 完整 | 相当 | — |
| 多模型路由 | 三模式 | Privacy/Efficiency/Hybrid 完整 | 相当 | — |
| **本地推理** | Qwen 端侧 开箱即用 | 探测链完整，需用户自装 Ollama | **中** | **P0** |
| NPU 加速 | WinML + OpenVINO | ONNX 框架在，无实际模型 | 大 | P2 |
| 多模态视觉 | OCR + 人像 + 场景 | vision_tools + Tesseract OCR + 云 fallback | 小 | — |
| 文件聚类 | AI 主题聚类 | k-means 已实现 | 相当 | — |
| **图片聚类** | 人像/节日/地点 | hash + 简单标签 | **中** | **P1** |
| 应用聚类 | 智能分组 | 关键词分类已实现 | 相当 | — |
| 磁盘清理 | 清理建议 | find_large_files 已实现 | 相当 | — |
| 定时任务 | 监控机票/抓更新 | Scheduler 完整 | 相当 | — |
| 浏览器写操作 | 接管/填表 | navigate/click/fill/submit + dry_run | 相当 | — |
| **App 深度接入** | 4+ 商业 App | Excel COM + notepad/calc | **大** | **P1** |
| Skill 包系统 | 一键安装 | YAML manifest + 安全审查 + 沙盒 | 相当 | — |
| HITL 硬垂询 | L2 强制弹窗 | Approval 表 + 前端弹窗 | 相当 | — |
| 安全沙盒 | 路径+敏感词 | 5 级 + 路径沙盒 + 浏览器二审 | mavris 占优 | — |
| 审计日志 | 未公开 | 自动脱敏 + SQLite + 全事件 | mavris 占优 | — |
| 隐私模式 | 断网可用 | 需安装 Ollama，不再静默 Mock | 中 | P0 |
| 向量搜索 | AI 文档库/图库 | FTS5 + embedding rerank | 相当 | — |
| Step 并行 | 多 Agent 并行 | 拓扑 + asyncio.gather | 相当 | — |
| WebSocket 推送 | 手机端介入 | `/ws/tasks/{task_id}` | 相当 | — |
| 离线 OCR | 端侧模型 | Tesseract + PDF fallback | 相当 | — |
| 状态机严格模式 | 未公开 | strict + StateTransitionError | 相当 | — |
| 跨端 Mac | 推进中 | electron-builder 配置在，缺 backend binary | 中 | P1 |
| **跨端 Android/iOS** | Android 已发布 | 完全无 | **大** | **P2** |
| **手机接管 PC** | 已支持 | placeholder | **大** | **P2** |
| PC 操作手机 | 安卓 VM | 完全无 | 大 | P2 |
| **文件监视器** | 实时索引 | 占位 | **中** | **P1** |
| **通知服务** | 桌面/推送通知 | 占位 | **中** | **P1** |
| 任务可视化 | 6 小马办公室 | 7 PonyAgent 办公室 | mavris 占优 | — |
| 回滚 | 未公开 | rollback_tools | 相当 | — |
| 监督性能 | 未公开 | BatchMessageReview 批量审查 | 相当 | — |

---

## 四、改进路线图

### P0 — 立即

#### P0-1 端侧模型开箱即用体验

**问题**：Marvis 内置 Qwen 端侧模型，装完即用。Mavris 本地 provider 探测链已完整，但需用户自行安装 Ollama + 拉取模型。这是**体验差距**而非技术差距。

**方案**：
- 安装包中内嵌 Ollama 二进制 + 预下载 `qwen2.5:3b-instruct-q4` 模型（~2GB）
- 首次启动检测 Ollama 未安装时，引导用户一键安装
- Settings 面板加"一键拉取推荐模型"按钮

**关键文件**：`scripts/build_all.ps1`、`desktop/src/renderer/components/SettingsPanel.tsx`、`backend/app/main.py`

#### P0-2 更新过时文档和文案

**问题**：README 的限制说明列出已实现功能为"预留"，会误导用户和开发者。

**方案**：更新 `README.md` 当前限制、`.env.example` 说明。

### P1 — 本季度

| 项 | 方向 | 关键文件 |
|---|---|---|
| **P1-1 图片聚类升级** | vision describe → embedding → HDBSCAN | `cluster_tools.py`、`vision_tools.py` |
| **P1-2 文件监视器** | watchdog + 增量索引 | `indexer/file_watcher.py` |
| **P1-3 通知服务** | Electron Notification API + WebSocket 推送 | `notification_service.py`、`main.ts` |
| **P1-4 App 深度集成扩展** | 定义 App Integration Protocol | `app_tools.py`、新建 Skill 包 |
| **P1-5 Mac 客户端打包** | `build_backend_mac.sh arm64` → DMG | `build_backend_mac.sh`、`package.json` |

### P2 — 后续迭代

| 项 | 方向 | 备注 |
|---|---|---|
| NPU 硬件加速 | `onnxruntime-directml` + 量化模型 | ONNX provider 框架已有 |
| Android 伴侣 App | React Native 极简版，LAN 配对 + JWT | 先做远程审批 |
| iOS 伴侣 App | 与 Android 同步 | |
| 手机远程接管 PC | WebSocket + 屏幕流 + 输入注入 | |
| PC 操作手机 App | 集成 scrcpy | R3 高风险 |
| analyze_xlsx LLM 化 | 同 summarize/QA 模式 | 框架在 document_service |
| 任务录屏回放 | 每步截屏 → TaskTimeline 播放器 | `task_recording_service.py` 已有 `capture_step_screenshot` |

---

## 五、验证方案

### 自动化
- 既有 22 个测试保持通过
- 重点回归：`test_marvis_parity_e2e.py`

### 手动验证（按 Marvis 公开 demo 复现）
1. 启动 `pnpm dev` → "帮我整理下载夹" → 验证副 Agent 路由 + 并行执行
2. 切隐私模式 → 断网 → 输入任务 → 期望：明确报错"未检测到本地模型"
3. "总结这份 PDF 的要点" → 验证文档 AI 摘要（LLM + 引用）
4. "我上个月聊了什么" → 验证 MemoryAgent recall
5. 设定时任务"每周一归档截图" → 验证 Scheduler
6. 安装 demo Skill → 验证 Skill 加载 + 安全审查

### 性能基线
- 启动到首屏 < 3s
- 单工具调用 + SafetyReview < 800ms（不含 LLM）
- 向量搜索 1000 文档 < 2s

---

## 六、项目健康度评估

| 指标 | 评分 | 说明 |
|---|---|---|
| 架构完整度 | **A** | 多 Agent + 状态机 + 安全审查 + 工具注册全链路完整 |
| 功能覆盖率（vs Marvis） | **B+** | 38 项对齐，4 项占位，8 项缺失（主要跨端 + 硬件加速） |
| 代码质量 | **A-** | Pydantic v2 类型完备，22+ pytest，清晰模块边界 |
| 安全机制 | **A+** | 5 级风险 + 路径沙盒 + PII 脱敏 + Skill 安全审查，**优于 Marvis 公开标准** |
| 开箱即用体验 | **C** | 隐私模式需用户自装 Ollama |

> 数据来源：Marvis 公开信息来自 marvis.qq.com、AIBase、TechNode、科技日报、中关村在线等（2026 年 5 月）；Mavris 实现细节来自仓库全量源码静态分析。
