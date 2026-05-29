# Mavris Fusion Plan

更新时间：2026-05-27

## 目标

把 `C:\Users\Suli\Desktop\mavris` 作为新系统主体，融合现有 Marvis/XiaoMa 包的产品结构、办公室工作台体验和多 Agent 协作方式。

## 边界

- 新系统代码以 `mavris` 仓库为准。
- `F:\马维斯\Marvis\Application\1.60.1000.21\reverse-ui` 只作为规格、行为和内部资产参考。
- 不迁入闭源 bundle、二进制、模型、DLL、EXE、品牌 logo 或原始 spritesheet。
- 已放入 `desktop/src/renderer/assets/xiaoma-agent` 和 `office-analysis` 的资产仅建议本机或内部验证使用；公开发布前应重绘或替换。

## 前端融合入口

- `desktop/src/renderer/App.tsx`：主组合器，按 zustand `activeView` 切换工作台。
- `desktop/src/renderer/features/shell/ShellFrame.tsx`：导航与标题。
- `desktop/src/renderer/features/office/OfficeScene.tsx`：首页办公室工作台。
- `desktop/src/renderer/features/agents/AgentOpsView.tsx`：新增多 Agent 指挥台。
- `desktop/src/renderer/components/ChatPanel.tsx`：自然语言入口。

## 后端融合入口

优先使用新边界：

- `POST /api/runs`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/timeline`
- `GET /api/runs/{run_id}/progress`
- `WebSocket /ws/runs/{run_id}`

兼容旧边界：

- `POST /api/chat`
- `WebSocket /ws/tasks/{task_id}`

不要让前端直接调用 `OrchestratorAgent` 内部类；通过 API 层保留 engine、审批、事件流和工具运行时的稳定边界。

## 多 Agent 映射

| UI Agent | 后端/任务信号 |
|---|---|
| Mavris / PM | Orchestrator, Planner, Supervisor, execution engine |
| 文件 Agent | FileAgent, DocumentAgent, file/document/index/cleanup tools |
| 电脑 Agent | ComputerAgent, system tools |
| 应用 Agent | AppAgent, app tools |
| 浏览器 Agent | BrowserAgent, browser tools |
| 搜索 Agent | SearchAgent, search tools |
| 安全审核 Agent | SafetyReview, HumanGate, approval queue |

## 下一步建议

1. 把 `AgentOpsView` 中的静态 agent 匹配逐步替换成 `/api/agents` 和 tool registry 元数据。
2. 将 run event 统一映射为 message part：text / reasoning / tool_call / approval / subagent。
3. 为公开发布准备原创小马替代资产，替换内部验证 GIF。
4. 增加端侧模型安装引导，这是当前开箱体验差距最大的部分。
