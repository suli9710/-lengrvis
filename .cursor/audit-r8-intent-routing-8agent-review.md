# 中文意图路由改动 — 8-Agent 交叉审查

**日期：** 2026-06-12  
**范围：** 中文引擎路由 + `rule` 结构化字段（7 个文件）

## 改动文件

| 文件 | Shard |
|------|-------|
| `backend/app/agents/delegation_rules.py` | S-Orc |
| `backend/app/orchestration/engine_router.py` | S-Orc |
| `backend/app/orchestration/execution_models.py` | S-Orc |
| `backend/app/agents/delegation_metadata.py` | S-Orc |
| `backend/app/services/task_service.py` | S-Api |
| `backend/tests/test_delegation_rules.py` | S-Client |
| `backend/tests/test_execution_engines.py` | S-Client |

---

## 8-Agent 审查矩阵

| 文件/域 | A1 Sec | A2 Rel | A3 Arch | A4 Logic | 综合 |
|---------|--------|--------|---------|----------|------|
| `delegation_rules.py` 引擎词表 | PASS | PASS | **PASS** | WARN→FIX | PASS |
| `engine_router.py` 重构 | PASS | PASS | **PASS** | PASS | PASS |
| `execution_models.py` +rule | PASS | PASS | **PASS** | PASS | PASS |
| `delegation_metadata` + `task_service` | PASS | PASS | **PASS** | PASS | PASS |
| 测试覆盖 | PASS | PASS | PASS | WARN | PASS |

**Gate：PASS**（审查中发现 1 项 Logic WARN，已当场修复）

---

## 各 Agent 发现摘要

### A1 Security — PASS
- 路由词表变更不涉及权限边界、SSRF、密钥或路径沙盒。
- `rule` 字段为内部结构化判据，不暴露给移动端脱敏视图。

### A2 Reliability — PASS
- `goal_is_system_diagnostics` regex 自 engine_router 原样迁入，行为不变。
- `route_engine` 判定顺序未变：explicit → write-intent → diagnostics → dev-read → os → fallback。

### A3 Architecture — PASS（本次最大收益）
- 引擎路由词汇并入 `delegation_rules.py`，消除 engine_router 内第 5 份独立 regex 表（推进 R7-A4）。
- `EngineRouteDecision.rule` 替代 `"system diagnostics" in route.reason` 字符串契约（推进 R7-A3）。
- agents→orchestration 依赖方向未恶化：`delegation_rules` 不反向 import engine_router。

### A4 Logic — WARN → FIXED
- **L1（已修）：** 初版 `DEVELOPER_WRITE_INTENT_TERMS` 含单字「改/写/补」，中文 substring 匹配导致「改革开放」等误触 write-intent。已删除单字触发词，保留「修改/编写/改进」等多字词。
- **L2（已修）：** 单字「类」误匹配风险，已移除；补「分析/解释/总结/审查」到 developer read。
- **L3（接受）：** 引擎层 `os_goal` 与 agent_hint 层 `FileAgent` 可对同一中文句给出不同粒度路由（如「打开下载文件夹」→ OS 引擎 + FileAgent hint），by design。
- **L4（信息）：** `OS_GOAL_TERMS` 含「文档」可能与 DocumentAgent hint 竞争；引擎走 OS 正确，Planner hint 仍由 infer 决定。

### A5 S-Orc — PASS
- `infer_supervisor_agent_hint` 系统诊断改查 `route.rule == "system_diagnostics"`，与 task_service 捷径一致。

### A6 S-Api — PASS
- `handle_chat` 系统诊断 fast-path 行为不变，仅判据从 reason 字符串改为 rule 枚举。

### A7 S-Infra — N/A
- 本次未触及 llm/tools/perception。

### A8 S-Client — PASS（建议跑测试）
- 新增 `test_engine_route_chinese_os_and_developer_goals` + 误触回归断言。
- 建议本地：`pytest tests/test_delegation_rules.py tests/test_delegation_metadata.py -q`

---

## 审查后硬化（已落地）

1. 删除单字 write 触发词：`改`、`写`、`补`
2. 删除单字 read 触发词：`类`
3. 补充 developer read 中文词：`分析`、`解释`、`总结`、`审查`
4. 测试：`改革开放的历史` 不得命中 `developer_write_os`

---

## 残余风险（非阻断）

| ID | 严重度 | 说明 |
|----|--------|------|
| R8-IR-L1 | 低 | 中文 substring 匹配仍可能在极口语句上误触（需 golden 持续扩充） |
| R8-IR-L2 | 低 | `EngineRouteDecision.rule` 尚未序列化到 API 响应（仅内部用） |
| R8-IR-L3 | 信息 | A1/A2 架构债（agents↔orchestration 双向依赖）未在本 sprint 处理 |

---

*8-Agent 交叉审查 | 中文意图路由 sprint | 2026-06-12*
