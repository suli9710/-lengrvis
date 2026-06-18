# Round 5 分片深度审计 — API / Security / Policy / Services

**Agent:** A6 (S-Api)  
**日期:** 2026-06-12  
**分片文件数:** ~95

---

## 执行摘要

| 指标 | 值 |
|------|-----|
| **分片评分** | **76 / 100** |
| **精读文件** | 91 Read / 4 PatternScan |
| **R4-M2** | **PARTIAL** — 成功 confirm 清全局桶；成功前 grief 仍在 |

---

## Top 5 发现

1. **R5-SA-01 (Medium)** — 全局配对 confirm 桶成功前仍可 LAN grief（`mobile_pairing_service.py:1059-1074`）
2. **R5-SA-02 (Medium)** — Webhook adapter 无 IP pin（`adapters/webhook.py:39`）
3. **R5-SA-03 (Low)** — `routes_approvals.py:44` 审批仍 `OrchestratorAgent()` 单例，未走 registry
4. **R5-SA-04 (Low)** — `routes_runtime.py` import 私有 `_execute_approved_step`，层边界靠惯例
5. **R5-SA-05 (正面 FIXED)** — run_service cancel/resume/registry 生命周期完整；`test_run_router_registry` 守护

---

## 关键路径

### security/
- `desktop_api.py` — hmac.compare_digest + fail-closed ✅
- `lan.py` — is_loopback_host 未知 host 失败关闭 ✅
- `local_secret.py` — DPAPI 原子写 ✅

### services/
- `run_service.py` — per-run engine router、终态 registry 释放、shutdown drain ✅
- `mobile_pairing_service.py` — 熵+限速 ✅；全局桶 PARTIAL
- `scheduler_service.py` — stop 排水 30s ✅

### api/
- `routes_chat.py` — WS 认证 + bus rebind ✅
- `routes_audit.py` / `routes_approvals.py` — 脱敏 ✅
- 28 个 routes 文件 — 无新增 auth bypass

---

## 文件覆盖

| 前缀 | 文件数 | 状态 |
|------|--------|------|
| api/ | 28 | 28 Read |
| security/ | 5 | 5 Read |
| policy/ | 12 | 12 Read |
| services/ | 28 | 28 Read |
| adapters/ | 5 | 5 Read |
| commands/ | 4 | 4 Read |
| **合计** | **82** | **82 Read** |
