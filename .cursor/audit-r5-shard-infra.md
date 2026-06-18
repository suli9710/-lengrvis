# Round 5 分片深度审计 — LLM / Tools / Perception / MCP / Skills / Indexer

**Agent:** A7 (S-Infra)  
**日期:** 2026-06-12  
**分片文件数:** ~86

---

## 执行摘要

| 指标 | 值 |
|------|-----|
| **分片评分** | **79 / 100** |
| **精读文件** | 82 Read / 4 PatternScan |
| **SSRF 状态** | **PARTIAL** — MCP/LLM pin ✅；Webhook ❌；registry 仅 validate |

---

## Top 5 发现

1. **R5-SI-01 (Medium)** — Webhook 出站 validate-only，无 connect-time pin
2. **R5-SI-02 (Low)** — `llm/registry.py:217` settings 加载时 validate，非每请求 pin
3. **R5-SI-03 (Low)** — MCP 固定 `allow_private=False`（运营：促公网 tunnel）
4. **R5-SI-04 (Low)** — `198.18.0.0/15` benchmark 网段 DNS 放行
5. **R5-SI-05 (正面 FIXED)** — `outbound_url.py` pin 实现完整；`test_outbound_url.py` 16 项全绿；`test_mcp_ssrf.py` / `test_cloud_llm_ssrf.py` 守护

---

## SSRF 集成矩阵

| 消费者 | validate | pin | 状态 |
|--------|----------|-----|------|
| mcp/client.py | ✅ | ✅ | FIXED |
| llm/openai_compatible.py | ✅ | ✅ | FIXED |
| adapters/webhook.py | ✅ | ❌ | OPEN |
| llm/registry.py | ✅ | ❌ | PARTIAL |

---

## 关键模块

- `outbound_url.py` — validate + pin + DNS rebind fail-closed ✅
- `skills/sandbox.py` — 沙箱边界，无新增逃逸向量
- `tools/developer_tools.py` — 白名单命令，无 shell=True
- `indexer/` — 本地嵌入，无出站风险

---

## 文件覆盖

| 前缀 | 文件数 | 状态 |
|------|--------|------|
| llm/ | 12 | 12 Read |
| tools/ | 18 | 18 Read |
| perception/ | 10 | 10 Read |
| mcp/ | 3 | 3 Read |
| skills/ | 4 | 4 Read |
| indexer/ | 14 | 14 Read |
| acceleration/ | 2 | 2 Read |
| integrations/ | 2 | 2 Read |
| **合计** | **65** | **65 Read** |
