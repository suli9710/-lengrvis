# Round 3 透镜审计 — 架构 / 生产就绪

**日期：** 2026-06-12  
**透镜：** Architecture & Production Readiness（严格）  
**基线：** Round 2 终报（`.cursor/audit-r2-final-report.md`）+ Sprint P1/P2/P3 修复报告  
**验证：** 源码行数统计、路由对照、`npm audit`、打包配置、生命周期代码路径

---

## 1. 执行摘要

| 指标 | Round 2 | Round 3 |
|------|---------|---------|
| 上帝模块（>1000 行） | 6+ | **9**（后端 6 + 桌面 3） |
| 路由重复面 | guardian ≈ pair+mobile+approvals | **仍 OPEN**（~150 行逻辑镜像未收敛） |
| `InMemoryRunStore` | 无持久化 | **仍 OPEN**（生产 run 状态易失） |
| 桌面关闭链 | 硬杀、无 drain | **PARTIAL**（`taskkill /T` + `before-quit` 异步停子进程；退出仍跳过 background drain） |
| `electron-builder` | `verifyUpdateCodeSignature: false` | **FIXED**（`true` + Electron Fuses） |
| desktop `shell-quote` | 2 critical | **仍 OPEN**（`concurrently@9.2.1` → `shell-quote@1.8.3`） |
| 移动端成熟度 | wakeup 零集成、无 fetch 超时 | **PARTIAL**（WakeupsScreen + `fetchWithTimeout`；仍无单元测试 / 导航框架） |
| 前端 Vitest | 无 | **仍 OPEN**（0 个 `*.test.ts(x)`；仅 15 条 desktop smoke + Playwright 依赖未接线） |

| 得分 | Round 2（估） | Round 3 |
|------|-------------|---------|
| **架构得分** | ~52 | **58 / 100** |
| **生产得分** | ~55 | **64 / 100** |

Sprint 后 orchestrator registry、出站 SSRF 统一、`TaskPool.shutdown`、桌面 token DPAPI、更新签名校验等抬高了生产分；**模块体量、路由镜像、InMemoryRunStore、前端测试真空** 仍严重拖累架构分。

---

## 2. 上帝模块行数（God Modules）

### 2.1 后端 Python（`backend/app/`，Top 15）

| 行数 | 文件 | 职责混杂度 | 风险 |
|------|------|-----------|------|
| **1693** | `core/db.py` | **极高** — schema 迁移、CRUD、审计链 HMAC、settings hook、20+ 表 upsert | 单点变更影响全栈；测试需整库夹具 |
| **1554** | `context_management.py` | 高 — 上下文压缩/窗口/记忆接线 | 与 `context_usage.py`(732)、`context_compaction.py` 边界模糊 |
| **1410** | `services/ollama_service.py` | 高 — 进程管理、模型探测、bundled path | 服务 + 子进程编排 |
| **1321** | `perception/ui_automation.py` | 高 — UIA + 截图 + 坐标 | 感知层巨石 |
| **1192** | `integrations/lengrvis_code.py` | 中 — 外部集成 | 可接受但难测 |
| **1153** | `policy/policy_engine.py` | 高 — 与 `tool_runtime` 双轨 | P0-18 架构债 |
| **1121** | `orchestration/os_execution_engine.py` | 中高 — 执行 + 调度 + cancel | Sprint 后改善，仍偏大 |
| **1064** | `orchestration/tool_runtime.py` | 中高 | 工具 + 锁 + 审批 + 超时 |
| **1025** | `api/routes_tasks.py` | **高** — HTTP + 业务编排 | 胖路由反模式 |
| **1000** | `services/mobile_pairing_service.py` | 高 — 配对 + JWT + 审批 + 设备 | 移动域上帝服务 |
| **870** | `api/routes_system.py` | 中高 | 诊断/硬件/隐私混合 |
| **787** | `services/run_service.py` | 中高 | run CRUD + engine 路由 + `default_run_store` |
| **751** | `services/browser_activity_runtime.py` | 中 | Playwright 同步适配 |

**阈值：** 项目内无强制行数上限；**>800 行** 模块共 **18** 个，**>1000 行** 共 **9** 个。

### 2.2 桌面 TypeScript（`desktop/src/`，Top 10）

| 行数 | 文件 | 职责混杂度 | 风险 |
|------|------|-----------|------|
| **5765** | `renderer/lib/apiClient.ts` | **极高** — ~100 个 API 封装 + WS + dev:web 分支 + 类型 re-export | 任何 API 变更冲突热点；无法按域懒加载 |
| **3155** | `renderer/components/SettingsPanel.tsx` | **极高** — UI + 设置写回 + 模型/Ollama/加速 | 组件即特性墙 |
| **1954** | `renderer/features/office/OfficeScene.tsx` | 高 — 3D/任务可视化 | 渲染 + 状态 |
| **1857** | `renderer/components/FileSearchPanel.tsx` | 高 | 搜索 + 索引 UI |
| **1720** | `renderer/App.tsx` | 中高 | 根路由 + 全局状态 |
| **1624** | `main/ipc.ts` | 高 — 全 IPC 面 | 安全边界集中 |
| **1560** | `shared/types.ts` | 中 — 全栈 DTO | 可接受为生成目标 |
| **1113** | `main/browserHost.ts` | 中高 | CDP + 安全 |
| **629** | `main/backendProcess.ts` | 中 — 已拆分较好 | 子进程 + 日志脱敏 |

### 2.3 移动 TypeScript

| 行数 | 文件 | 说明 |
|------|------|------|
| **1384** | `src/api/client.ts` | 单文件承载配对/审批/远程/唤醒；较桌面 apiClient 小但仍偏胖 |
| **~340** | `App.tsx` | 手写 screen 状态机（无 React Navigation） |
| **22** | `src/**` 源文件总数 | 功能面完整但结构扁平 |

### 2.4 架构判决

| 严重度 | 发现 |
|--------|------|
| **P1** | `db.py` 应拆为 `schema/`、`repositories/`、`audit_store.py`；当前 80+ 函数/类混于一文件 |
| **P1** | `apiClient.ts`（5765 行）为全项目最大模块；Sprint P3 已列拆分待办，**未执行** |
| **P2** | `routes_tasks.py` / `routes_system.py` 应下沉到 service 层，路由仅做 DTO + 授权 |
| **P2** | `mobile_pairing_service.py`（1000 行）与 `routes_guardian` 审批逻辑三角耦合 |

---

## 3. 路由重复（Routes Duplication）

### 3.1 三套 HTTP 面

| 应用 | 入口 | 配对 | 审批 | 移动任务 | 备注 |
|------|------|------|------|---------|------|
| Full backend | `main.py` | `routes_pair.py`（61 行） | `routes_approvals.py`（179 行） | `routes_mobile.py`（628 行） | 标准 `/api` 前缀 |
| Guardian shell | `guardian.py` → `routes_guardian.py`（**521 行**） | **内联重复** `/api/pair/*` | **内联重复** `/api/approvals/*` + wake | **内联重复** `/api/mobile/*` | 含 proxy + 本地执行分叉 |
| 兼容垫片 | `routes_pairing.py` | re-export `routes_pair` | — | — | 6 行，可删 |

### 3.2 重复热点（估算 ~180–220 行有效重复）

1. **配对七端点** — `routes_pair.py` 与 `routes_guardian.py:65–103` 均调 `mobile_pairing_service`，路径与 handler 逐行镜像。
2. **审批执行分叉** — `routes_approvals._execute_approved_step` vs `routes_guardian._wake_full_backend_for_approval` + `approval_execution_response`；guardian 多一层唤醒全后端，**行为不等价**（生产隐患）。
3. **移动设备** — `routes_mobile` `/mobile/devices` vs guardian `/api/mobile/devices` vs pair `/pair/devices`；三套列表/撤销语义需人工同步。
4. **WebSocket 双挂载** — `main.py:244–254` 每个 `ws_router` 注册 **两次**（根路径 + `/api` 前缀），共 **10** 次 `include_router`；兼容历史客户端，增加路由表噪音与测试矩阵。

### 3.3 胖路由文件

| 文件 | 行数 | 问题 |
|------|------|------|
| `routes_tasks.py` | 1025 | task/run/plan/chat 编排堆叠 |
| `routes_system.py` | 870 | 诊断导出 + 硬件 + 隐私 + Ollama |
| `routes_remote.py` | 716 | 远程桌面 + WS |
| `routes_mobile.py` | 628 | 移动全功能单文件 |

### 3.4 架构判决

| 严重度 | 发现 |
|--------|------|
| **P1** | `routes_guardian` 应改为 **router factory** 复用 `routes_pair` / `routes_approvals` / `routes_mobile` 子路由（P3 报告 PR-D 待办） |
| **P1** | 审批 guardian vs full **执行路径不一致** — 架构上应单一 `ApprovalExecutionService` |
| **P2** | WS 双前缀应文档化弃用时间表，或中间件统一 strip |
| **P3** | `routes_pairing.py` 死垫片，删除或合并 |

---

## 4. InMemoryRunStore

### 4.1 现状

```35:66:backend/app/orchestration/execution_engine.py
class InMemoryRunStore:
    """Small shared store for v1 engine skeletons.
    API persistence can replace this at the boundary without changing the
    execution engine contract.
    """
    ...
default_run_store = InMemoryRunStore()
```

- **生产路径：** `run_service.py` 在 engine run 生命周期内读写 `default_run_store`（`put`/`get` 多处）；与 SQLite `runs` 表 **并行存在**，无同步保证。
- **进程边界：** 重启、guardian→full 切换、多 worker（若未来）均 **丢 run 中间态**。
- **并发：** `RLock` + `model_copy(deep=True)` 仅保证单进程线程安全；不解决多实例。
- **测试：** 仅 `test_execution_engines.py` / `test_cancel_run_drains_tasks.py` 用隔离实例；**无** 持久化契约测试。

### 4.2 与 DB 的关系

| 存储 | 内容 | 生命周期 |
|------|------|---------|
| SQLite `runs` / `run_events` | 用户可见 run 记录、事件流 | 持久 |
| `InMemoryRunStore` | `RunState`（engine 内部步进态） | 进程内存 |
| `orchestrator_registry` | per-task/run orchestrator + bus | 进程内存（Sprint 新增） |

**裂缝：** API 层查 DB，engine 层查 memory store；cancel/resume 跨层时易出现 **状态漂移**（R2 P0-02 部分修复后仍依赖内存 store 为 source of truth）。

### 4.3 架构判决

| 严重度 | 发现 |
|--------|------|
| **P1** | 生产应实现 `SqliteRunStore` 或废弃 engine store、统一以 DB + event bus 为准 |
| **P2** | `default_run_store` 全局单例阻碍并行测试与多租户 |
| **P2** | 文档仍标 "v1 skeleton"，但已走生产路径 — **技术债标签失真** |

---

## 5. 桌面关闭（Desktop Shutdown）

### 5.1 改进（相对 R2）

| 项 | 状态 | 证据 |
|----|------|------|
| PyInstaller 子进程树 | **FIXED** | `terminateProcessTree` — Windows `taskkill /T /F`，非 Windows SIGTERM→SIGKILL（`backendProcess.ts:438–475`） |
| `before-quit` 异步清理 | **FIXED** | `preventDefault` + `await backend.stop()` 再 `app.quit()`（`main.ts:365–389`） |
| 托盘 background 切换 | **存在** | `enterTrayBackground` → `backend.enterBackground`（`main.ts:139–155`） |

### 5.2 仍存缺口

| 严重度 | 发现 |
|--------|------|
| **P1** | **退出路径不调用** `enterBackground` / `prepare_for_background` — `before-quit` 直接 `backend.stop()`，跳过 `run_service.prepare_for_background` 的 pause/drain（`run_service.py:150–167`） |
| **P1** | `backend.stop()` 对 Windows Service 托管后端 **显式不停止**（`backendProcess.ts:216–218`）— 设计如此，但用户以为"退出=全停" |
| **P2** | 无超时：若 `taskkill` 挂起，`before-quit` 可无限阻塞 |
| **P2** | Full backend `lifespan` drain（`TaskPool.shutdown`）与桌面杀进程 **竞态** — 先杀子进程则 Python finally 可能来不及跑完 |

### 5.3 架构判决

关闭链已从"硬杀"升级为 **PARTIAL**；缺 **语义化下线**（foreground → background → drain runs → stop pool → terminate）。

---

## 6. electron-builder 生产配置

### 6.1 亮点（相对 R2 FIXED）

| 项 | 配置 |
|----|------|
| Electron Fuses | `runAsNode: false`、`onlyLoadAppFromAsar: true`、cookie 加密、禁用 NODE_OPTIONS/inspect（`electron-builder.yml:18–24`） |
| 更新签名 | `verifyUpdateCodeSignature: true`（L51） |
| ASAR | `asar: true`，排除 source map（L8–13） |
| 发布 | GitHub Releases + `electron-updater`（L33–37） |
| 多平台 extraResources | backend 二进制 + capabilities JSON（L52–78） |

### 6.2 生产缺口

| 严重度 | 发现 |
|--------|------|
| **P1** | 注释明确：**`backend.exe` 不在 electron-builder 自动签名范围** — 需 CI 单独 `signtool`（L48–49）；未签名则 SmartScreen / 更新信任链断裂 |
| **P2** | `publish.repo: "-lengrvis"` — 仓库名以连字符开头，易与 org/repo 混淆 |
| **P2** | macOS / Linux 无 `verifyUpdateCodeSignature` 等效项（平台差异未文档化） |
| **P3** | 本地 `npm run dist` 无签名时 `verifyUpdateCodeSignature: true` 可能导致 **本地更新测试失败**（注释 L50 提到 signed yml 分流） |

### 6.3 生产判决

打包安全基线 **成熟**；**后端二进制签名与关闭链** 是上线前必检门。

---

## 7. npm audit — shell-quote（Desktop）

**执行：** `cd desktop && npm audit`（2026-06-12）

```
shell-quote 1.1.0 - 1.8.3  Severity: critical
  concurrently 9.2.1 → shell-quote
2 critical severity vulnerabilities
```

| 项 | 评估 |
|----|------|
| 可达性 | **低（生产）** — `concurrently` 仅 `npm run dev` 开发脚本 |
| 可达性 | **中（供应链）** — lockfile 仍拉取 vulnerable 版本；CI 若跑 `npm audit` 会 **FAIL** |
| 修复 | `npm audit fix` 可用；R2 fix-plan Dev 项 **未合入** |
| mobile | `shell-quote@1.8.4`（transitive）— **不在 critical 范围**；另有 `joi` moderate（`eas-cli` dev） |

---

## 8. 移动端成熟度（Mobile Maturity）

### 8.1 能力矩阵

| 能力 | R2 | R3 |
|------|----|----|
| 配对（QR/手动） | ✅ | ✅ `PairScreen` |
| 审批列表/详情/决策 | ✅ | ✅ + 通知深链 |
| 远程输入 grant | ✅ | ✅ `RemoteScreen` + 过期 reducer |
| 唤醒（Wakeup） | ❌ 零集成 | ✅ `WakeupsScreen` + `client.ts` API + `wakeup-contract-smoke.cjs` |
| Fetch 超时 | ❌ | ✅ `fetchWithTimeout`（默认超时 + `FetchTimeoutError`） |
| 会话恢复 | 基础 | ✅ 加载/失败/重试/重新配对 UX（`App.tsx`） |
| Android 发布门 | ✅ `verify_android_release_gate.ps1` | ✅ `gate:android-release` / EAS profiles |
| 导航架构 | — | ❌ 手写 `activeScreen` 状态，无 React Navigation / Expo Router |
| 单元测试 | ❌ | ❌ 0 个 Jest/Vitest 测试文件 |
| 任务伴侣 | smoke only | `smoke:task-companion` 存在，无自动化 UI 测试 |

### 8.2 源文件规模

- **22** 个 `src/**` 文件（含 screens、api、store）
- 最大单文件：`client.ts` **1384** 行（较 R2 增长 — 唤醒/任务 API 并入）
- **无** `__tests__` / `*.test.ts(x)`

### 8.3 架构判决

| 严重度 | 发现 |
|--------|------|
| **P2** | 功能已达 **MVP+ 伴侣应用**；结构仍为 **单包扁平**，难扩展多模块 |
| **P2** | `client.ts` 应拆 `pairingApi` / `approvalsApi` / `wakeupsApi` |
| **P3** | 无 Expo Router，深链/通知路由靠手动 state，长期维护成本高 |

---

## 9. 无 Vitest / 前端单元测试真空

### 9.1 扫描结果

| 范围 | Vitest/Jest | 实际测试形态 |
|------|-------------|-------------|
| `desktop/` | **无** devDependency | **15** 个 `scripts/*smoke*.cjs`；`@playwright/test` 在 package.json **无 test script 引用** |
| `mobile/` | **无** | **3** smoke（token、task-companion、remote-input-grant）+ wakeup contract |
| `backend/` | — | **149** pytest 文件（架构上后端测试充分） |

### 9.2 风险

- `apiClient.ts` / `SettingsPanel.tsx` / `ipc.ts` 等 **零单元覆盖**；回归全靠 smoke 与人工。
- Smoke 脚本 **不进入** CI 统一门（除非 `scripts/run_tests.ps1` 显式调用）；与后端 pytest 成熟度 **严重不对称**。
- 类型安全仅靠 `tsc --noEmit`；**无** 组件/钩子行为测试。

### 9.3 架构判决

| 严重度 | 发现 |
|--------|------|
| **P1** | 桌面/renderer 应引入 Vitest + MSW 或 IPC mock，至少覆盖 `apiClient` 分模块后核心路径 |
| **P2** | Playwright 已安装未接线 — 浪费依赖或应补 `e2e/` |
| **P2** | mobile 关键 reducer（`remoteInputGrant`）无单测 |

---

## 10. Sprint 后架构相关 FIXED（本透镜）

| R2 ID | 发现 | R3 状态 |
|-------|------|---------|
| P1-05 | guardian 路由重复 | **OPEN**（未 factory 化） |
| P1-15 | `verifyUpdateCodeSignature: false` | **FIXED** → `true` |
| P1-20 | 上帝模块 | **OPEN**（行数略增：apiClient 5765） |
| P2 | `InMemoryRunStore` | **OPEN** |
| P2 | 无 Vitest | **OPEN** |
| P1-12 | 桌面硬杀 | **PARTIAL** |
| P1-13 | wakeup 零集成 | **FIXED**（mobile） |
| P1-12 (mobile) | fetch 无超时 | **FIXED** |
| P0-07/08 | SSRF | **FIXED**（`outbound_url.py` 统一） |
| P0-17 | Orchestrator 分裂 | **PARTIAL**（registry；见 reliability 透镜） |

---

## 11. 透镜得分

### 11.1 架构得分：**58 / 100**

| 维度 | 权重 | 得分 | 说明 |
|------|------|------|------|
| 模块分解 | 30% | 14/30 | 9 个 >1000 行模块；`db.py` + `apiClient.ts` 阻断演进 |
| API / 路由层 | 20% | 10/20 | guardian 镜像未收敛；胖路由；WS 双挂载 |
| 状态与数据 | 20% | 11/20 | SQLite 成熟但 `InMemoryRunStore` 裂缝；registry 改善 |
| 前端结构 | 15% | 8/15 | 无 Vitest；Settings/Office 巨石组件 |
| 跨切面一致性 | 15% | 15/15 | SSRF/权限模式有测试；policy 双轨仍 OPEN（扣在模块项） |

**加权：** 14+10+11+8+15 = **58**

### 11.2 生产得分：**64 / 100**

| 维度 | 权重 | 得分 | 说明 |
|------|------|------|------|
| 打包 / 发布 | 25% | 18/25 | Fuses + 更新签名校验；`backend.exe` 签名外置 |
| 生命周期 / 关闭 | 20% | 11/20 | taskkill 树 + async quit；无 background drain |
| 依赖 / 供应链 | 20% | 11/20 | desktop 2 critical（dev 可达性低但 audit 红灯） |
| 移动端交付 | 20% | 15/20 | EAS gate + 核心流程 + smoke；无自动化 UI 测试 |
| 可观测 / 运维 | 15% | 9/15 | 后端 pytest 强；前端 smoke 碎片化；InMemory 丢态难排查 |

**加权：** 18+11+11+15+9 = **64**

---

## 12. 优先修复（架构透镜 Top 8）

| 序 | 动作 | 预期收益 |
|----|------|---------|
| 1 | `routes_guardian` → 复用子 router + 单一 `ApprovalExecutionService` | 消 ~200 行重复；行为一致 |
| 2 | `SqliteRunStore` 或 engine store 废弃路线图 | 生产 run 可恢复 |
| 3 | 拆分 `apiClient.ts`（按 `tasks`/`settings`/`browser`/`pairing`） | 降冲突；可接 Vitest |
| 4 | `db.py` → schema migrations + repository 模块 | 后端可测性 / 新人上手 |
| 5 | 桌面 `before-quit` 先 `enterBackground` + `prepare_for_background` 再 `stop` | 生产关闭可预期 |
| 6 | `npm audit fix` 升级 `concurrently` / `shell-quote` | CI 绿灯 |
| 7 | 引入 Vitest（desktop renderer 优先） | 补齐架构测试金字塔 |
| 8 | CI 对 `backend.exe` signtool 签名后再 `extraResources` | 与 `verifyUpdateCodeSignature` 对齐 |

---

## 13. 中文摘要

**架构（58 分）：** 项目功能面广、后端 pytest 厚实，但 **模块化不足** 是主矛盾。`db.py`（1693 行）与 `apiClient.ts`（5765 行）构成双极上帝模块；`routes_guardian` 与 full backend 之间 **约 200 行路由/审批逻辑重复且行为分叉**。`InMemoryRunStore` 仍在生产路径中持有 engine 状态，与 SQLite 双轨，重启即丢。**前端完全没有 Vitest**，桌面/mobile 仅靠 smoke，与后端 149 个测试文件形成 **测试金字塔倒置**。

**生产（64 分）：** `electron-builder` 已达较成熟水位（Electron Fuses、更新签名校验、asar）。桌面退出已从硬杀改为 **异步停子进程树**，但仍 **不经过 background drain**，与 `run_service.prepare_for_background` 脱节。**npm audit 仍有 2 个 critical**（`shell-quote`←`concurrently`，仅 dev 脚本可达）。移动端从 R2 的「唤醒未接、无超时」进步到 **可用伴侣应用**（审批/远程/唤醒/超时/EAS 门），但 **无导航框架与单测**。

**总判：** 适合 **内测/小范围分发**；要达到 **公开发布级架构**，需先收敛路由重复、持久化 run store、拆分上帝模块，并补齐前端单元测试与后端二进制签名闭环。

---

*本报告由 Round 3 架构/生产透镜 Agent 基于源码度量与 R2 基线对照生成。关联透镜：`.cursor/audit-r3-lens-reliability.md`（可靠性 74 分）。*
