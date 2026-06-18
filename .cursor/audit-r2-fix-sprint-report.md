# 4开发 + 4审核 协同修复报告

**日期：** 2026-06-11  
**模式：** Dev-1~4 并行实现 → Review-1~4 交叉审核 → 主 Agent 合并审核意见

## 开发线摘要

| Dev | 范围 | 测试 |
|-----|------|------|
| **Dev-1** | 并行 `deepcopy(context)`、step 级 read state、`cancel_run` 任务追踪 | 3 passed |
| **Dev-2** | lifespan `TaskPool.shutdown`、scheduler drain、tool `wait_for` 超时 | 2 passed |
| **Dev-3** | `outbound_url.py`、MCP/LLM/Webhook SSRF | 23+ passed |
| **Dev-4** | `plans` ON CONFLICT 保留 `created_at`、non-strict 非法迁移不写入 | 2 new + integration |

## 审核矩阵（交叉）

| Reviewer | 主审 Dev | 交叉审 Dev | 裁决 |
|----------|----------|------------|------|
| R1 | Dev-1 | Dev-3 SSRF | APPROVE_WITH_NOTES（P0-04 未修） |
| R2 | Dev-2 | Dev-4 state_machine | APPROVE_WITH_NOTES（tool_timeout 配置） |
| R3 | Dev-3 | Dev-1 cancel_run API | CONDITIONAL PASS |
| R4 | Dev-4 | Dev-2 lifespan 顺序 | APPROVE WITH NOTES |

## 审核后已合并修复（主 Agent）

- `main.py`：关闭顺序改为 `scheduler.stop()` → `pool.shutdown()`
- `config.py` + `registry.py` + `settings_service.py`：`tool_timeout_seconds` 正式入配置
- `openai_compatible.py`：`follow_redirects=False`；cloud 路径 `allow_private` 收紧
- `run_service.py`：API `cancel_run` 调度 `router.cancel_run()`
- `test_state_machine_integration.py`：断言对齐 non-strict 新语义
- `test_cloud_llm_ssrf.py`：新增 cloud base_url 阻断测试

## 合并验证

```
43 passed (sprint 相关测试套件)
```

## 仍待跟进（非阻塞合并）

| 优先级 | 项 | 负责建议 |
|--------|-----|----------|
| P1 | P0-04：fatal 后 cancel 兄弟并行 step | Dev-1 跟进 |
| P2 | DNS rebinding connect-time 复检 | Dev-3 |
| P2 | `test_cancel_run_drains_tasks.py` | Dev-1 + Dev-3 |
| P2 | dry-run 路径锁（P0-06） | 独立 PR |
