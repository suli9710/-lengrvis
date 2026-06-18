# Round 5 安全审查报告 — Lengrvis/mavris

**审查对象:** 当前工作树（493 manifest 文件）  
**基线:** `.cursor/audit-r4-lens-security.md` (83), `.cursor/audit-r4-final-report.md`  
**方法:** 全库安全模式扫描 + 关键锚点逐行核实  
**Agent:** A1 (G-Sec)  
**日期:** 2026-06-12

---

## 执行摘要

| 指标 | 值 |
|------|-----|
| **评分** | **87 / 100** (+4 vs R4) |
| **OPEN Critical** | **0** |
| **OPEN High** | **0** |
| **OPEN Medium** | **3** |
| **OPEN Low** | **4** |
| **R4-C1** | **FIXED** |

---

## R4 必核项状态

| ID | 项目 | R4 | R5 | 证据 |
|----|------|----|----|------|
| R4-C1 | 审计 HMAC 锁自死锁 | OPEN | **FIXED** | `db.py:1597-1601` 锁外预取 secret |
| R4-M1 | SSRF DNS TOCTOU | OPEN | **PARTIAL** | MCP/LLM pin；Webhook 仍 validate-only |
| R4-M2 | 全局配对 confirm 桶 grief | OPEN | **PARTIAL** | 成功 confirm 清全局桶；成功前仍可 grief |
| R4-M3 | 权限双轨 | OPEN | **OPEN(改善)** | backstop 收敛；tool policy 与 PolicyEngine 仍双轨 |

---

## 发现项（按严重度）

### Medium

**R5-M1 — Webhook 出站无 connect-time IP pin**  
- 证据: `webhook.py:39` 仅 `validate_outbound_http_url`；`mcp/client.py:98-107`、`openai_compatible.py:166-172` 已 pin  
- 修复: WebhookClient 接入 `pin_outbound_http_url`

**R5-M2 — 配对 confirm 全局桶成功前仍可 LAN grief**  
- 证据: `mobile_pairing_service.py:1059-1074` 全局 32/60s；`:1085` 成功清全局  
- 修复: 全局桶改 per-subnet 或移除

**R5-M3 — 权限判定双轨**  
- 证据: `tool_runtime.py:887-943` vs `policy_engine.py`  
- 修复: 单轨委托 PermissionStore

### Low

- R5-L1: GET diagnostics 保留 local_paths（loopback 后，设计取舍）
- R5-L2: `198.18.0.0/15` benchmark 网段放行
- R5-L3: MCP 固定 `allow_private=False`
- R5-L4: dev 链 shell-quote critical（desktop/package.json override 已部分缓解）

---

## 评分维度

| 维度 | 权重 | R5 |
|------|------|-----|
| 出站 SSRF | 30% | 26/30 |
| 认证/秘密 | 25% | 23/25 |
| 数据暴露/脱敏 | 20% | 18/20 |
| 执行控制面/策略 | 15% | 12/15 |
| 可用性/滥用 | 10% | 9/10 |

**总分: 87/100** — 无 OPEN Critical/High，不触发封顶

---

## 文件覆盖

493/493 manifest 文件经全库模式扫描；28 个安全关键路径锚点逐行核实。
