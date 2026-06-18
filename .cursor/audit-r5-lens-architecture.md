# Round 5 架构与生产就绪透镜 — G-Arch

**Agent:** A3 (G-Arch)  
**日期:** 2026-06-12  
**基线:** R4 架构 61/100

---

## 综合评分: **68 / 100** (+7)

| 维度 | R4 | R5 |
|------|----|----|
| Modularity | 63 | **68** |
| Testability | 57 | **62** |
| Deployability | 67 | **75** |
| Maintainability | 64 | **66** |

---

## Top 3 架构风险

1. **`mappers.ts`（2414 行）+ `db.py`（1919 行）上帝模块** — 拆分前端但未减总复杂度
2. **Guardian 审批双轨执行** — HTTP wake vs 进程内 OrchestratorAgent
3. **`InMemoryRunStore` 全局单例** — 阻碍多 worker 扩展

---

## R4→R5 迁移

| 项 | R5 状态 |
|----|---------|
| RunState 持久化 | **SUBSTANTIAL** — 每 turn 写 runs.state + crash recovery |
| Guardian 共享 approval helpers | **PARTIAL+** — import 收敛，执行仍双轨 |
| CI 骨架 | **SUBSTANTIAL** — pytest + golden + vitest/smokes + PR audit |
| Desktop/mobile API 重构 | **DONE（结构）** — facade + 分层 client |

---

## CI 评估: 显著改善

- ✅ 全量 pytest + golden ≥95% + `LENGRVIS_AUDIT_HMAC_SECRET`
- ✅ Desktop Vitest + 15 behavior smokes
- ✅ Mobile wakeup smoke + PR npm audit gate
- ❌ 仍缺 ruff/eslint、覆盖率门、signed backend CI job

---

## 文件覆盖

493 manifest 文件经模块边界、依赖方向、CI/打包模式扫描。
