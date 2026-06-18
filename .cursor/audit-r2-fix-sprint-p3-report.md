# PR-A 架构残留 — Sprint P3 报告

**日期：** 2026-06-11

---

## 实现摘要

| 项 | 文件 | 改动 |
|----|------|------|
| A9 OrchestratorRegistry | `orchestrator_registry.py` | 按 task/run 缓存 orchestrator 与 bus |
| A4 Run router 复用 | `run_service.py` | `_RUN_ENGINE_ROUTERS`；`cancel_run` 复用创建时的 router/engine |
| A10 AgentBus 实例隔离 | `agent_bus.py` | 订阅表改为实例字段 |
| OS engine 绑定 | `os_execution_engine.py` | start_run/process_plan/cancel 走 registry |
| Task/Chat 接线 | `task_service.py`, `routes_chat.py` | 绑定 orchestrator；WS 使用 registry.bus 或 fallback |

## 新增测试

- `test_orchestrator_registry.py` — 复用、release、bus 隔离
- `test_run_router_registry.py` — cancel 复用 tracked router
- `test_websocket_stream.py` — 更新为 `chat_bus` 发布（实例隔离后）

## 验证

```
18 passed (registry + websocket + parallel + cancel)
```

## 剩余 PR-D

- `routes_guardian` router factory 去重
- RunStore SQLite 持久化
- `apiClient.ts` 拆分
