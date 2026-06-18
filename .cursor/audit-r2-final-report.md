# Lengrvis/mavris 第二轮全项目代码审计报告

**审计日期：** 2026-06-11  
**范围：** 493 个 git 跟踪源码文件（全仓库，非 diff）  
**Agent 数：** 14（Wave0 主 Agent + 8 分片 + 4 全局透镜 + 覆盖门禁）  
**覆盖保证：** 每文件 ≥ 5 次 Agent 触达（1 分片 + 4 透镜）

---

## 1. 执行摘要

| 指标 | 第一轮 | 第二轮 |
|------|--------|--------|
| Agent 数 | 8（3+1 失败重试） | 14（全部成功） |
| 文件清单 | 估算 ~493 | 冻结 manifest 493 |
| 测试专审 | 抽样 | 独立 S8（139 文件） |
| 去重发现 | ~63 | **~72 去重后** |
| P0 项 | ~18 | **22**（含 4 条 NEW） |
| 依赖 SCA | 未跑 | desktop **2 critical**（shell-quote） |

**结论：** 第一轮 P0 锚点 **全部仍存在**（源码复核确认）。第二轮新增 Orchestrator 三套生命周期、cancel_run 不取消任务、cloud LLM SSRF、dev:web 安全分裂、Permission fail-open 等发现。项目在 Electron 生产路径与移动 LAN/TLS 设计上成熟度高；主要风险仍是 **并发可变状态**、**关闭链断裂**、**出站 SSRF 不对称**。

---

## 2. 与第一轮对比

| 状态 | 数量 | 说明 |
|------|------|------|
| **仍存在** | 42 | 两轮多 Agent CONFIRMED |
| **NEW（第二轮）** | 18 | 见 §4 |
| **PARTIAL/争议** | 5 | LAN+token、C2 单例、webhook SSRF 等 |
| **已修复** | 3 | vision 路径授权、mobile JWT require exp、browser SSRF |
| **REJECTED 误报** | 0 | — |

---

## 3. P0 发现表（去重合并，按优先级）

| ID | Severity | Location | Finding | Agents | Confidence |
|----|----------|----------|---------|--------|------------|
| P0-01 | High | `step_scheduler_handler.py:46,156` `os_execution_engine.py:590-591` | 并行 step 共享 `context`/`task`/`plan` 可变对象，竞态 | S1,G-Logic | 98% |
| P0-02 | High | `os_execution_engine.py:172,1116-1128` `run_service.py:538` | `run_plan_turn` 回落 `_orchestrator()` 单例；EngineRouter 全局单引擎 | S1,G-Logic,G-Arch | 95% |
| P0-03 | High | `os_execution_engine.py:108-120` | `cancel_run` 不 cancel 在途 asyncio.Task | G-Logic N1 | 95% |
| P0-04 | High | `os_execution_engine.py:587-621` | 并行 gather 遇 fatal 不中止同批步骤 | G-Logic N3 | 92% |
| P0-05 | High | `resource_state.py:83,228` | `_TASK_READ_STATES` 任务级共享无锁 | S1,G-Logic | 97% |
| P0-06 | High | `tool_runtime.py:1053-1054` | dry-run 跳过路径写锁（TOCTOU） | S1,G-Logic | 97% |
| P0-07 | High | `mcp/client.py:93-95` | MCP URL 无 SSRF 防护 | S4,G-Sec | 95% |
| P0-08 | High | `llm/openai_compatible.py:108-112` `registry.py:209-214` | Cloud LLM 任意 base_url + Bearer 外泄 | S4,G-Sec | 95% |
| P0-09 | High | `desktop_api.py:201-223` | Desktop API Token 明文落盘 | S2,G-Sec | 98% |
| P0-10 | High | `main.py:104-116` | lifespan 无 `TaskPool.shutdown()` | S5,G-Rel | 99% |
| P0-11 | High | `tool_runtime.py:1045-1048` | 工具执行无全局 timeout | S3,G-Rel | 99% |
| P0-12 | High | `main.ts:384` `backendProcess.ts:441-477` | 退出硬杀进程，跳过 background + drain | S6,S7,G-Rel | 95% |
| P0-13 | High | `apiClient.ts:183-187,1704-1726` | dev:web 无 desktop token / IPC deny-list | S6,G-Sec N-01 | 95% |
| P0-14 | High | `browser_activity_runtime.py:119-215` | 每次操作 sync Playwright 冷启动阻塞 loop | S3,G-Rel | 99% |
| P0-15 | Medium→P0 | `mobile_pairing_service.py:1028` | 配对码 6 hex（24 bit） | S3,G-Sec | 92% |
| P0-16 | Medium→P0 | `scripts/install_acceleration.ps1:60-68` | Runtime=auto 装三套互斥 ORT | S7,G-Logic | 95% |
| P0-17 | High | `orchestrator_agent.py` + `task_service.py` | Orchestrator 三套生命周期分裂 | G-Arch N1 | 95% |
| P0-18 | High | `tool_runtime.py:789-804` `policy_engine.py` | 策略双轨执行 | G-Arch N5 | 90% |

---

## 4. P1 发现表（精选 Top 20）

| ID | Location | Finding |
|----|----------|---------|
| P1-01 | `db.py:562-565` | `plans.created_at` INSERT OR REPLACE 每次覆盖 |
| P1-02 | `state_machine.py:72-88` | 默认 non-strict 仍写入非法迁移 |
| P1-03 | `step_scheduler_handler.py:254-256` | 全 SKIPPED → COMPLETED |
| P1-04 | `schemas.py:39` | DENIED 映射为 CANCELLED phase |
| P1-05 | `routes_guardian.py` vs `routes_mobile.py` | ~150 行路由重复，审批执行分叉 |
| P1-06 | `routes_system.py:284-288` | GET diagnostics 未脱敏路径 |
| P1-07 | `routes_audit.py:12-33` | 审计 API 无 redaction |
| P1-08 | `scheduler_service.py:76-85` | stop 不 await executions |
| P1-09 | `file_watcher.py:137` | 无界 asyncio.Queue |
| P1-10 | `config.py:275` | environment_event_retention_days 死配置 |
| P1-11 | `run_service.py:152-164` | prepare_for_background 始终 ok:true |
| P1-12 | `mobile/src/api/client.ts` | 全部 fetch 无超时 |
| P1-13 | `mobile/**` | wakeup API 零集成 |
| P1-14 | `browser_tools.py` / `workflow_tools.py` | approval 弱于 remote_tools |
| P1-15 | `electron-builder.yml:52` | verifyUpdateCodeSignature: false |
| P1-16 | `permissions.py:243-250` | Permission 默认 allow（fail-open） |
| P1-17 | `adapters/webhook.py:34-60` | Webhook URL 无 SSRF |
| P1-18 | `agent_bus.py:21-23` | 类级订阅表跨实例串扰 |
| P1-19 | `recovery_handler.py:154-170` | recovery step depends_on=[] 无 renumber |
| P1-20 | `apiClient.ts` / `db.py` / `SettingsPanel.tsx` | 上帝模块（5659/1674/3155 行） |

---

## 5. P2 发现表（架构/可维护性，精选）

- `context_management.py` 1554 行上帝模块
- `OfficeScene.tsx` 1954 行 UI 上帝组件
- `collect_release_evidence_packet.ps1` 2709 行
- `InMemoryRunStore` 无持久化
- `routes_tasks.py` 1025 行胖路由
- 前端无 Vitest 单元测试（仅 smoke）
- PS1 26 脚本无统一 redaction
- `test_runs_api.py` / `test_lengrvis_parity_e2e.py` Planner spy 假阳性

---

## 6. REJECTED / 已修复附录

| 项 | 状态 |
|----|------|
| vision_tools 路径 fallback 绕过 | **已修复** |
| mobile JWT 未 require exp | **已修复** |
| browser_activity SSRF 无私网拦截 | **已修复** |
| LAN desktop API 无 token 即可调用 /api/tasks | **REJECTED**（需 token，测试覆盖） |

---

## 7. 覆盖证明

- Manifest: `.cursor/audit-r2-manifest.txt`（493 文件）
- Gate: `.cursor/audit-r2-coverage-gate.md` — **PASS，0 缺失**
- 基线: `.cursor/audit-r2-wave0-baseline.md`

---

## 8. 测试盲区 TOP 10（S8 专审）

1. `plans.created_at` upsert 覆盖 — **零测试**
2. `strict_state_machine=True` orchestrator 全链路
3. 真实 `PlannerAgent.create_plan` E2E（非 spy）
4. 并行 `TaskRuntimeContext` 隔离
5. cleanup dry-run → execute TOCTOU
6. plan version + DB 持久化一致性
7. session_context + memory 同时传入真实 planner
8. non-strict safe_transition 静默 FAILED 传播
9. 并行写 + dry_run 审批绑定交叉
10. `test_agent_bus_state_machine.py` 错误 import 路径整文件 skip

---

## 9. 严重度统计（去重）

| 严重度 | 数量 |
|--------|------|
| P0 / High | 22 |
| P1 / Medium | 28 |
| P2 / Low | 22 |
| **合计** | **~72** |

---

## 10. 建议验证命令

```powershell
# 后端测试
cd backend; python -m pytest tests/ -q --tb=no

# 依赖审计（需 pip-audit）
npm run audit:deps

# 桌面 IPC smoke
cd desktop; npm run qa:gate
```

---

*本报告由 Round 2 合成 Agent 基于 14 个子 Agent 输出去重生成。修复计划见 `.cursor/audit-r2-fix-plan.md`。*
