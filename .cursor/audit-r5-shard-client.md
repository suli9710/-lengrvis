# Round 5 分片深度审计 — Desktop / Mobile / Tests / Scripts / CI

**Agent:** A8 (S-Client)  
**日期:** 2026-06-12  
**分片文件数:** ~356（desktop 114 + mobile 20 + tests 150 + scripts 40 + test_data 10 + CI 1）

---

## 执行摘要

| 指标 | 值 |
|------|-----|
| **分片评分** | **71 / 100** |
| **精读文件** | 42 Read / 314 PatternScan |
| **R4-C1 生产路径** | **FIXED（代码级）** — 锁外预取；desktop 仍不注入 secret |
| **CI gap** | CI 已设 `LENGRVIS_AUDIT_HMAC_SECRET`；desktop 启动链未设 |

---

## Top 5 发现

1. **R5-SC-01 (Medium)** — `backendProcess.ts` 不注入 `LENGRVIS_AUDIT_HMAC_SECRET`（纵深依赖 db 锁外预取 + secret 文件 bootstrap）
2. **R5-SC-02 (Medium)** — desktop 退出 `main.ts:365-389` 硬杀不经 `prepare_for_background`
3. **R5-SC-03 (Low)** — mobile 零单测，428 行 endpoints 仅靠 smoke/typecheck
4. **R5-SC-04 (Low)** — `mappers.ts` 2414 行成新上帝模块（API 拆分后转移）
5. **R5-SC-05 (正面 FIXED)** — DPAPI token（localSecret.ts）、Electron 三件套、Vitest 进 CI、15 desktop smokes 进 CI

---

## Desktop 安全链

| 组件 | 状态 |
|------|------|
| localSecret.ts DPAPI | ✅ FIXED |
| backendProcess.ts token 脱敏 | ✅ |
| autoUpdater verifyUpdateCodeSignature | ✅ |
| contextIsolation + CSP | ✅ |
| audit secret 注入 | ❌ 未注入（代码级 C1 已修，纵深不足） |

---

## Mobile

- `store/auth.ts` — SecureStore ✅
- `api/client/` — 分层 refactor ✅
- `WakeupsScreen.tsx` — 新功能，无安全回归

---

## 测试 / CI

| 套件 | R4 | R5 |
|------|----|-----|
| 守护 pytest（7 文件） | 3 FAIL + 5 挂起 | **42/42 PASS** |
| CI audit secret | ❌ | ✅ `ci.yml:57` |
| Desktop Vitest | ❌ | ✅ 2 套件 |
| Mobile 单测 | 0 | 0 |

---

## 文件覆盖

| 前缀 | 文件数 | Read | PatternScan |
|------|--------|------|-------------|
| desktop/ | 114 | 18 | 96 |
| mobile/ | 20 | 8 | 12 |
| backend/tests/ | 150 | 12 | 138 |
| scripts/ | 40 | 4 | 36 |
| test_data/ | 10 | 0 | 10 |
| .github/workflows/ci.yml | 1 | 1 | — |
| **合计** | **335** | **43** | **292** |

全部分片文件均经 PatternScan；关键路径 43 文件深读。
