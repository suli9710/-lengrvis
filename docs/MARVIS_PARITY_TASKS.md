# Mavris 对齐 Marvis 任务拆分

> 来源审计报告：`docs/MARVIS_PARITY.md`（2026-05-25 实证修订版）
> 更新日期：2026-05-25
> 状态：基于代码实证重新评估，原 T01-T10、T12 已在代码中实现

---

## 已完成任务（代码已实现，无需再投入资源）

以下任务在最新代码审计中确认已实现，从活跃任务列表中移出：

| 原 ID | 能力 | 验证位置 | 状态 |
|---|---|---|---|
| T01 | 领域 Agent `act()` 路由、边界与 prompt | `orchestrator_agent.py` `_consult_subagent()`；`agents/base.py` deterministic fast path；`llm/prompts/*.md` 38 个文件 | ✅ 路由与 prompt 已完成；不是 6 个独立自主推理 Agent |
| T02 | 隐私模式本地 LLM Provider | `local_provider.py` 探测链 + `registry.py:86` raises `LocalBackendUnavailable` | ✅ 已完成 |
| T04 | 文档 AI 摘要/问答/报告 | `document_service.py` map-reduce + QA + report 全接 LLM | ✅ 已完成 |
| T05 | 运行时监督批量化 | `safety_review_agent.py:16` `BatchMessageReview` | ✅ 已完成 |
| T06 | 向量索引与语义搜索 | `vector_index.py` FTS5 + embedding rerank + cosine | ✅ 已完成 |
| T07 | 状态机审计/严格模式 | `state_machine.py` 默认审计同步，`strict_state_machine=true` 时 `StateTransitionError` | ✅ 已完成 |
| T08 | 离线 OCR 与 PDF OCR | `ocr_service.py` Tesseract + 元数据 + PDF 图片 OCR | ✅ 已完成 |
| T09 | Skill 包加载器与沙盒 | `skills/loader.py` + `sandbox.py` + `schemas.py` | ✅ 已完成 |
| T10 | WebSocket 实时任务流 | `routes_chat.py:27` `/ws/tasks/{task_id}` + `agent_bus.subscribe()` | ✅ 已完成 |
| T12 | Step 级并行执行 | `orchestrator_agent.py:149-254` `_build_step_graph` + `asyncio.create_task` | ✅ 已完成 |

---

## 活跃任务

### T-NEW-01 — P0 端侧模型开箱即用

**优先级：** P0
**前置依赖：** 无

#### 要构建什么

让用户安装 Mavris 后能清晰进入隐私模式准备流程。当前本地 provider 探测链已完整（Ollama/LM Studio/llama.cpp），Ollama 运行时和模型默认不随包携带，需要在设置页按需安装/拉取。

#### 当前进展

- 已新增本地模型 `setup-plan` 后端接口，能返回硬件条件、运行时、服务、模型四步状态和下一步动作。
- 设置页已产品化为“隐私模式开箱检查”，普通用户能看到一键开启隐私模式、本机条件、Ollama/模型准备步骤，以及“不会静默退回云端”的边界说明。
- 隐私检查清单已串联主行动作：开启隐私模式后，下一步直接变成“一键准备本地 AI”，复用安装 Ollama、启动服务、下载模型的完整进度流。
- 已明确默认发布策略：不在 Git 或普通安装包中携带 Ollama 离线模型、GPU 运行库或 bundle manifest。
- 后端 setup-plan 会引导用户检查硬件、安装 Ollama、启动服务、拉取推荐模型；隐私模式不会静默回退云端。
- portable 构建不再自动发现 `vendor/ollama` 或 `vendor/ollama-models`；只有显式传入外部目录时才允许构建特殊离线包。
- `vendor/ollama`、`vendor/ollama-models` 和 `vendor/ollama-bundle-manifest.json` 已作为本地缓存/实验发行资源忽略，避免大二进制进入仓库。

#### 方案

- 首次启动或切换隐私模式时检测 Ollama 缺失，并引导一键安装（Windows 使用 winget，其他平台提示手动安装）。
- Settings 面板保留“一键拉取推荐模型”按钮 + 进度条，模型仅在用户需要隐私模式时下载。
- portable 构建默认不复制 Ollama runtime/model；特殊离线发行必须显式提供外部目录与 manifest，并单独执行 `verify_packaging.ps1 -RequireBundledOllama`。

#### 预计涉及文件

- `scripts/build_all.ps1`（打包流程）
- `desktop/src/renderer/components/SettingsPanel.tsx`（UI 引导）
- `backend/app/main.py`（启动时 Ollama 健康检查）
- `scripts/bundle_ollama.ps1`（准备随包 Ollama runtime/model + manifest）
- `scripts/prepare_ollama_release.ps1`（特殊离线资源准备入口 + 显式门禁验证）
- `scripts/build_portable.ps1`（复制随包 Ollama runtime/model）

#### 验收标准

- [ ] 全新 Windows 系统上安装 → 首次启动 → 切隐私模式 → 设置页引导安装 Ollama 并下载推荐模型
- [ ] Ollama 已存在时跳过安装步骤
- [ ] 安装失败时有明确错误提示，不影响效率模式

---

### T-NEW-02 — P1 图片聚类升级（人像/场景/地点）

**优先级：** P1
**前置依赖：** 无

#### 要构建什么

把图片聚类从 hash + 简单标签升级为语义标签驱动的多维度分组，对齐 Marvis "AI 图库"能力。

#### 方案

- `vision_tools.describe_image` 给每张图打结构化标签（人物数 / 场景 / 可见物体）
- 用 embedding 向量化标签 + EXIF 元数据 → HDBSCAN 聚类
- 前端聚类视图加"按维度切换"

#### 预计涉及文件

- `backend/app/tools/cluster_tools.py`
- `backend/app/tools/vision_tools.py`
- `backend/app/indexer/embedding_service.py`
- 桌面端聚类视图

#### 验收标准

- [ ] 每张图片得到结构化标签
- [ ] 聚类可基于语义维度分组
- [ ] 既有文件/应用聚类不被破坏

---

### T-NEW-03 — P1 文件监视器

**优先级：** P1
**前置依赖：** 无

#### 要构建什么

把 `indexer/file_watcher.py` 从占位替换为真实的文件变动监听 + 增量索引更新。

#### 方案

- 使用 `watchdog` 库（Windows ReadDirectoryChangesW）
- 文件变动事件触发增量 FTS5 + embedding 索引更新
- 防抖处理（同一文件短时间多次变动合并为一次更新）

#### 预计涉及文件

- `backend/app/indexer/file_watcher.py`（重写）
- `backend/requirements.txt`（加 watchdog）

#### 验收标准

- [ ] 监视目录中的文件创建/修改/删除能被检测到
- [ ] 变动触发增量索引更新
- [ ] 不会因大量文件变动导致性能问题

---

### T-NEW-04 — P1 通知服务

**优先级：** P1
**前置依赖：** 无

#### 要构建什么

把 `services/notification_service.py` 从占位替换为真实桌面通知。

#### 方案

- Electron main process 使用 `Notification` API
- 后端通过 WebSocket 推送通知事件
- 定时任务完成、长任务完成、需要审批时触发通知

#### 预计涉及文件

- `backend/app/services/notification_service.py`（重写）
- `desktop/src/main/main.ts`（Notification API）

#### 验收标准

- [ ] 定时任务完成后弹出桌面通知
- [ ] 需要用户审批时弹出通知
- [ ] 通知点击可跳转到对应任务

---

### T-NEW-05 — P1 第三方 App 深度集成

**优先级：** P1
**前置依赖：** 无

#### 要构建什么

基于已有 Skill 系统定义 App Integration Protocol，扩展可控应用范围。

#### 方案

- 定义标准化 App Skill 格式
- 优先做 2-3 个 Windows 常用应用 Skill
- 利用 COM 自动化或 Accessibility API

#### 预计涉及文件

- `backend/app/tools/app_tools.py`
- 新建 Skill 包（`.marvis_data/skills/`）

---

### T-NEW-06 — P1 Mac 客户端打包

**优先级：** P1
**前置依赖：** 需要 macOS CI 环境

#### 要构建什么

产出 Mac backend binary + DMG 安装包。

#### 方案

- `bash scripts/build_backend_mac.sh arm64` → `dist/backend`
- `npm --prefix desktop run dist:mac:arm64` → DMG
- electron-builder 配置已就绪

---

## P2 Backlog

| 项 | 方向 | 备注 |
|---|---|---|
| NPU/ONNX 加速 | `onnxruntime-directml` + 量化模型 | ONNX provider 框架已有 |
| Android 伴侣 App | React Native, LAN 配对 + JWT | 先做远程审批 |
| iOS 伴侣 App | 与 Android 同步 | |
| 手机接管 PC | WebSocket + 屏幕流 | |
| PC 操作手机 | scrcpy 集成 | R3 高风险 |
| analyze_xlsx LLM 化 | 同 document_service 模式 | |
| 任务录屏回放 | 每步截屏 → 播放器 | `capture_step_screenshot` 已有 |

---

## 建议执行顺序

1. **T-NEW-01**（端侧模型开箱即用）— 最紧迫的用户体验差距
2. **T-NEW-03 + T-NEW-04** 可并行（文件监视器 + 通知服务）
3. **T-NEW-02**（图片聚类升级）
4. **T-NEW-05 + T-NEW-06** 可并行（App 集成 + Mac 打包）
