# Round 5 逻辑透镜审计 — G-Logic

**Agent:** A4 (G-Logic)  
**日期:** 2026-06-12

---

## 逻辑正确性评分: **72 / 100**

---

## R4 发现复验

| ID | 状态 |
|----|------|
| R4-C1 审计 HMAC 自死锁 | **FIXED** — `db.py:1601` 锁外预取 |
| R4-C2 写竞争 | **FIXED** — `_EVENT_WRITE_LOCK` |
| P0-03 cancel drain | **FIXED** — 测试通过 |
| P0-04 并行 fatal 取消 | **FIXED** — 测试通过 |
| skipped 完成语义 | **FIXED** — 测试通过 |
| R4-H1 并行共享 | **PARTIAL** — PlanStep 隔离 ✅ |
| R4-H3 resume bus | **FIXED** — 测试通过 |
| SSRF TOCTOU | **PARTIAL** — Webhook 未 pin |

---

## 新发现逻辑缺陷: 6 项

### Medium (3)
1. 并行 batch 仍共享 Task — 终态覆盖风险
2. 并行 batch 仍共享 Plan — torn read 风险
3. Webhook SSRF pin 缺失

### Low (3)
4. `is_transition_allowed` strict 参数死代码
5. `_resume_engine_loop` 吞异常
6. Step SKIPPED→SUCCEEDED 相位映射掩盖非法迁移

---

## 文件覆盖

493 manifest 文件经状态机/不变量/边界条件模式扫描；db.py、state_machine、scheduler、outbound_url 深读。
