# Round 5 可靠性/生命周期审计报告 — mavris

**Agent:** A2 (G-Rel)  
**日期:** 2026-06-12  
**基线:** R4 可靠性维 50（C1 封顶）

---

## 执行摘要

| 指标 | 值 |
|------|-----|
| **评分** | **82 / 100** |
| **R4-C1** | **FIXED** |
| **R4-C2** | **FIXED** |
| **守护 pytest** | **42/42 PASSED**（本轮实测） |

---

## R4 遗留项核实

| ID | 状态 | 证据 |
|----|------|------|
| R4-C1 审计 HMAC 自死锁 | **FIXED** | `db.py:1597-1601` 锁外预取 |
| R4-C2 取消路径 database locked | **FIXED** | `db.py:40` `_EVENT_WRITE_LOCK` 串行化 audit/run_events |
| R4-H1 并行共享可变对象 | **PARTIAL** | PlanStep 快照 `plan_snapshot.py` ✅；Task/Plan 仍共享 |
| R4-H3 resume bus 失配 | **FIXED** | `os_execution_engine.py:111-118` + `test_resume_bus_binding.py` |
| lifespan drain | **FIXED** | `main.py:125-129` → `shutdown_runs()` |
| WS 早连 bus 失配 | **FIXED** | `routes_chat.py:74-86` 轮询重绑 |
| orchestrator_registry 泄漏 | **FIXED** | `run_service.py:485-510` 终态释放 |

---

## 实测验证（Round 5 Gate）

```
pytest tests/test_cancel_run_drains_tasks.py \
       tests/test_parallel_context_isolation.py \
       tests/test_skipped_completion_semantics.py \
       tests/test_lifespan_shutdown.py \
       tests/test_resume_bus_binding.py \
       tests/test_orchestrator_registry.py \
       tests/test_outbound_url.py
→ 42 passed in 2.23s
```

R4 的 3 红测 + 5 挂起测全部转绿。

---

## 仍 OPEN 项

| ID | 严重度 | 说明 |
|----|--------|------|
| R5-M1 | Medium | 并行 batch 仍共享 Task/Plan 对象 |
| R5-M2 | Medium | 无并发双 run 集成测试 |
| R5-M3 | Medium | desktop 退出不经 prepare_for_background |
| R5-M4 | Medium | 跨进程 SQLite 写竞争（非 audit 路径） |
| R5-M5 | Low | `_resume_engine_loop` 吞异常无日志 |

---

## 评分

| 维度 | 权重 | R5 |
|------|------|-----|
| 并行正确性 | 30% | 27 |
| 取消/关闭 | 20% | 18 |
| 资源/TOCTOU | 20% | 17 |
| 超时/背压 | 15% | 13 |
| 架构隔离 | 15% | 14 |

**总分: 82/100**（较 R4 的 50 封顶 +32）

---

## 文件覆盖

493 manifest 文件经 lifecycle/cancel/resume/concurrency/SQLite 模式扫描；orchestration/、run_service、db、step_phase 全文精读。
