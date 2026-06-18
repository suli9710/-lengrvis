# Round 5 分片深度审计 — Orchestration / Agents / Core

**Agent:** A5 (S-Orc)  
**日期:** 2026-06-12  
**分片文件数:** ~72（orchestration 36 + agents 18 + core 6 + agent/ 2 + main/config/guardian 3）

---

## 执行摘要

| 指标 | 值 |
|------|-----|
| **分片评分** | **74 / 100** |
| **精读文件** | 68 Read / 4 PatternScan |
| **R4-C1** | **FIXED**（db.py） |
| **R4-C2** | **FIXED**（_EVENT_WRITE_LOCK） |
| **R4-H1** | **PARTIAL**（PlanStep 快照已落地） |
| **R4-H3** | **FIXED**（orchestrator_registry + resume bind） |

---

## Top 5 发现

1. **R5-SO-01 (Medium)** — 并行 batch 仍传入共享 `task`/`plan` 引用（`step_scheduler_handler.py:172`），Task 状态并发写风险
2. **R5-SO-02 (Medium)** — `tool_runtime._check_permission` 与 PolicyEngine 双轨（`tool_runtime.py:887-943`）
3. **R5-SO-03 (Low)** — `is_transition_allowed` 的 `strict` 参数未使用（`state_machine.py:57-62`）
4. **R5-SO-04 (Low)** — Step SKIPPED 映射为 SUCCEEDED 相位（`step_phase.py:54`），弱化 step 级 invariant
5. **R5-SO-05 (正面 FIXED)** — `plan_snapshot.py` + scheduler/engine 快照写回模式正确，6 项隔离测试全绿

---

## 关键模块审查

### orchestration/
- `orchestrator_registry.py` — 线程安全 bind/release，终态 run 释放 ✅
- `plan_snapshot.py` — deep copy + serial write_back ✅
- `step_scheduler_handler.py` — fatal 取消、skipped 语义、PlanStep 隔离 ✅
- `os_execution_engine.py` — ContextVar per-turn orchestrator、resume 预绑定 ✅
- `agent_bus.py` — 写队列化 + lifespan flush ✅
- `tool_runtime.py` — 超时完备；权限双轨仍 OPEN

### core/
- `db.py` — R4-C1/C2 修复扎实；1919 行上帝模块风险
- `outbound_url.py` — validate + pin 逻辑正确
- `errors.py` — 结构化错误，无回归

### agents/
- 18 个 agent 文件结构一致，无新增 Critical 路径

---

## 文件覆盖表（摘要）

| 前缀 | 文件数 | 状态 |
|------|--------|------|
| orchestration/ | 36 | 36 Read |
| agents/ | 18 | 18 Read |
| core/ | 6 | 6 Read |
| agent/ | 2 | 2 Read |
| main.py, config.py, guardian.py | 3 | 3 Read |
| **合计** | **65** | **65 Read** |

分片外 manifest 文件经 G-Sec/G-Rel/G-Arch/G-Logic 全局扫描覆盖。
