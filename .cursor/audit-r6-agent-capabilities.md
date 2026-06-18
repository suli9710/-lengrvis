# Round 6 附件 — Agent 能力详表

> **正式审计终报见：** [`.cursor/audit-r6-final-report.md`](./audit-r6-final-report.md)

本文件为 Round 6 能力审计的 **详表附件**，供实现与 golden 补全时查阅。发现项编号、严重度、修复优先级以终报为准。

---

## Worker 工具全表（126 内置）

### FileAgent（26）
`file.*`（cleanup/copy/move/trash/search/semantic_search 等）、`image.cluster*`

### ComputerAgent（42）
- `system.*`（12）— diagnostics, get_info, processes, cleanup_suggestions 等  
- `dev.*`（9）— grep, glob, git_status, pytest_inventory, shell_readonly 等  
- `remote.*`（4）— click, key_press, type_text, view_screen  
- `ui_automation.*`（20）— click, type_text, screenshot, list_windows 等  
- `workflow.run`（1）

### BrowserAgent（21）
`browser.navigate`, `read_page`, `act`, `cua`, `fill_form`, `session_*` 等

### DocumentAgent（18）
`document.*`（13）、`vision.*`（5）

### AppAgent（12）
`app.launch_*`, `uninstall_app`, `excel.*`, `cluster_installed` 等

### SearchAgent（4）
`search.query`, `search.fetch_result`, `search.summarize_results`, `tool.search`

### ExternalServices（3）
`external.email.send`, `external.calendar.create_event`, `external.webhook.post`

---

## Planner 确定性短路顺序

1. `_deterministic_cleanup_plan` → `file.cleanup_plan`  
2. `_deterministic_file_plan` → `file.trash`（dry_run）  
3. `_deterministic_uninstall_plan` → `app.uninstall_app`  
4. `_deterministic_system_check_plan` → `system.diagnostics`  
5. `_deterministic_open_app_plan` → `app.launch_*`  
6. `_deterministic_search_plan` → `file.search_by_name`  

源码：`backend/app/agents/planner_agent.py:123-134`

---

## Worker 执行契约摘要

源码：`backend/app/agents/base.py:99-159`

1. 有失败 observation → LLM 恢复  
2. 无 tool_name → request_revision  
3. tool owner ≠ self.name → request_revision（**不越权**）  
4. defer_loading → LLM  
5. required args 缺失 → request_revision  
6. 否则 → 确定性 `propose_tool`（无 LLM hop）

---

## Golden task 分类（34 条）

| category | 数量 |
|----------|------|
| system | 5 |
| safety | 7 |
| approval | 5 |
| file | 5 |
| cleanup | 2 |
| document | 3 |
| chat | 3 |
| files_api | 3 |
| app | 1 |

**未覆盖：** browser, search, remote, ui_automation, dev, developer-engine, workflow, external, skill, mcp

---

*附件 | Round 6 | 2026-06-12*
