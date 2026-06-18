# Round 4 修复冲刺报告

**日期：** 2026-06-12
**基线：** `.cursor/audit-r4-final-report.md`（总评 60/C，OPEN Critical R4-C1）
**范围：** R4 终报「建议下一步」优先级 1–5 项

---

## 修复明细

### R4-C1（Critical）：审计 HMAC 自死锁 — FIXED ✅

- `backend/app/core/db.py`：`_prepare_audit_event_locked` 已在进入 `_AUDIT_CACHE_LOCK` 之前预取 `hmac_secret = _audit_hmac_secret()`，消除锁内重入自死锁（含解释性注释）。
- **验证（不设 `LENGRVIS_AUDIT_HMAC_SECRET`）：**
  - R4 报告中 5 个挂起文件（test_lifespan_shutdown / test_permission_policy / test_state_machine_nonstrict / test_mobile_pairing / test_lan_api_guard）：**134 passed，14s，无挂起**。
  - **全量后端套件：1771 passed / 3 skipped / 0 failed，4m12s**（R4 时同一套件会挂到 90min 超时）。
- **纵深：** `ci.yml` 后端 pytest 步骤显式注入 `LENGRVIS_AUDIT_HMAC_SECRET`，CI 不再依赖秘密文件引导路径。Desktop 不注入（生产链使用 DPAPI 秘密文件持久化，注入临时 env 反而会破坏跨重启的链校验一致性）。

### R4-C2（High）：审计/run_events 写与长持写事务冲突 — FIXED ✅

- `db.py` 新增进程级 `_EVENT_WRITE_LOCK`（RLock）串行化两条 BEGIN IMMEDIATE 热写路径：
  - `_insert_audit_event_record`（锁覆盖整个事务含 commit）；
  - 新增 `_insert_run_event_record`，`insert_run_event` 与 `upsert_model("run_events", ...)` 统一走该路径（原 `_upsert_run_events` handler 的锁外事务已移除）。
- 取消风暴中 `step.invalid_transition_audited` 逐条写不再互相竞争 busy_timeout；跨进程仍由 WAL + busy_timeout 覆盖。
- **验证：** R4 的 3 个确定性红测试（parallel fatal cancel / cancel drain / skipped 完成语义）**全绿**，全量套件无回归 → **R3 的 P0-03/P0-04/skipped 语义 FIXED 恢复采信**。

### shell-quote critical — 已解除 ✅

- `desktop/package-lock.json` 中 `node_modules/shell-quote` 已解析至 **1.8.4**（已知 critical 影响 <1.7.3），树内无其他 shell-quote 副本。每周安全扫描门禁恢复信号价值。

### R4-H3（High）：resume 路径 orchestrator/bus 失配 — FIXED ✅（复核确认）

- `os_execution_engine.resume_run` 在 resume 时即物化 orchestrator 并写入全局 `orchestrator_registry`；`run_service` bridge 通过 `orchestrator_registry.bus_for_task` 订阅同一 bus。
- 守护测试 `test_resume_bus_binding.py` 绿。

### R4-H1（High，P0-01 三轮遗留）：并行 step 共享可变 Task/Plan/PlanStep — FIXED ✅

- 新增 `backend/app/orchestration/plan_snapshot.py`：`snapshot_step`（deep copy）+ `write_back_step`（全字段串行写回）。
- `step_scheduler_handler.py`：`_launch_ready_steps` 给每个并行 executor 发隔离快照；`_ScheduleState.running` 改为 `_RunningStep(step, snapshot)`；`_collect_finished_steps` / `_drain_running_after_stop` 在**单收集协程内**串行写回后才跑 recovery；`_cancel_running_steps` 在真实 step 上标记 FAILED。
- `os_execution_engine.py`：`_execute_selected_steps` 同样改为快照执行 + 完成时串行写回，results 配对真实 step。
- 效果：兄弟 step 在途的半更新状态不再可被并发观察/落库（`_persist_plan_update` 持久化的 plan 中，在途 step 保持启动时状态直至串行写回）。
- **新增隔离测试（test_parallel_context_isolation.py）：**
  - `test_parallel_scheduler_steps_execute_on_isolated_step_snapshots_and_write_back`
  - `test_os_engine_parallel_steps_execute_on_isolated_step_snapshots_and_write_back`
  - 断言：executor 收到的不是真实 step 对象；在途变更对真实 plan 不可见；完成后状态/args/描述全量写回。

---

## 验证汇总

| 命令 | 结果 |
|------|------|
| 5 个 R4 挂起文件（无环境变量，隔离重跑） | 134 passed |
| 3 个 R4 红测试 | 7 passed（含同文件其他用例） |
| R4 定向 15 文件套件 | 154 passed |
| **全量 `pytest backend/tests`（无环境变量）** | **1771 passed / 3 skipped / 0 failed（4m12s）** |

## 状态迁移

| 项目 | R4 | 本冲刺后 |
|------|----|----------|
| R4-C1 审计自死锁 | OPEN Critical | **FIXED（全量套件实证）** |
| R4-C2 取消路径审计写冲突 | OPEN High | **FIXED（写串行化 + 3 测试绿）** |
| P0-03/04/skipped 语义 | 未验证 | **FIXED 恢复采信** |
| R4-H1 并行共享 Task/Plan（P0-01） | OPEN High | **FIXED（快照 + 串行写回 + 测试）** |
| R4-H3 resume bus 失配 | OPEN High | **FIXED** |
| shell-quote critical | OPEN | **FIXED（1.8.4）** |

## 剩余建议（R5 候选）

1. 门禁补线：desktop smoke 接 CI；npm audit 挡 PR。
2. R4-H2 收口：`run_plan_turn` 全链 per-state orchestrator + 并发双 run 集成测试。
3. 架构专项：`apiClient.ts`（6242 行）域拆分 + 前端 Vitest 起步；guardian router factory；崩溃 RUNNING run 启动自动恢复。
4. Medium 清理：SSRF connect-time IP pin、全局配对 confirm 桶 grief、`orchestrator_registry._by_task` 释放、AgentBus 同步 DB 写。
