# 4开发 + 4审核 — PR-B/C P1 协同修复报告

**日期：** 2026-06-11  
**模式：** Dev-1~4 并行实现 → Review-1~4 交叉审核 → 主 Agent 合并审核意见

---

## 开发线摘要

| Dev | 范围 | 关键改动 |
|-----|------|----------|
| **Dev-1** | Desktop token (B5-B6, B12-B13) | DPAPI token 持久化、`verifyUpdateCodeSignature: true`、dev:web 写 API token 门控 |
| **Dev-2** | API 脱敏 (B8-B11) | GET diagnostics / audit / approvals 脱敏；permissions 默认 deny |
| **Dev-3** | Mobile (C6-C8) | `fetchWithTimeout`、Wakeups 屏、`auth.ts` legacy token 擦除 |
| **Dev-4** | Pairing + scripts (B7, C4, C9-C10) | 8 位配对码、全局限速、SKIPPED→FAILED、`install_acceleration.ps1` 单 ORT |

---

## 审核矩阵

| Reviewer | 主审 | 裁决 | 关键发现 |
|----------|------|------|----------|
| **R1** | Dev-1 | APPROVE_WITH_NOTES | DPAPI 失败静默重生成、memory token、dev:web WS 无 subprotocol |
| **R2** | Dev-2 | **BLOCK** → 已修 | 空 policy 默认 deny 导致全工具封锁 |
| **R3** | Dev-3 | **BLOCK** → 已修 | 配对码 6 vs 8 不一致；`RemoteScreen` 缺 import |
| **R4** | Dev-4 | CONDITIONAL FAIL → 已修 | 移动端 8 位对齐；manifest `revision: main` 仍浮动 |

---

## 主 Agent 合并修复

| 项 | 修复 |
|----|------|
| P0 配对码契约 | `PAIRING_CODE_LENGTH=8`；`PairScreen` / `pairingPayload.ts` / smoke 对齐 |
| P0 RemoteScreen | `App.tsx` 补 import |
| P0 permissions | 混合模型：有 allow 规则时 allow-list deny；无 allow 规则时 deny-list allow |
| P1 approval 脱敏 | `redact_public_text` 替代 `redact_value` 用于异常字符串 |
| P1 PairRedeemRequest | `min_length=8, max_length=8, pattern=^[a-f0-9]{8}$` |
| P1 wakeup smoke | 新增 `mobile/scripts/wakeup-contract-smoke.cjs` |

---

## 仍待跟进（非阻塞）

| 优先级 | 项 | 负责 |
|--------|-----|------|
| P1 | Desktop DPAPI 解密失败 fail-closed | Dev-1 |
| P1 | `source: "memory"` 打包构建硬失败 | Dev-1 |
| P1 | dev:web WebSocket subprotocol | Dev-1 |
| P2 | model_manifest `revision: main` → commit SHA | Dev-4 |
| P2 | 混合 success + blocked-skip → COMPLETED 语义 | Dev-4 |

---

## 验证命令

```powershell
# Backend
cd backend
python -m pytest tests/test_permission_policy.py tests/test_system_diagnostics.py tests/test_mobile_pairing.py -q

# Mobile smoke
cd mobile
node scripts/mobile-token-smoke.cjs
node scripts/wakeup-contract-smoke.cjs
```
