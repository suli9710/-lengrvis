# Lengrvis 审查修复记录 · 2026-10-11

本轮在当前工作树上修复审查报告的 F01、F02、F03。原有审查报告与复现材料保留，发布验收结论仍以正式证据和签署为准。

## 修复内容

### F01：隐私上下文跨事项、跨模式隔离

- 记忆持久化 `source_mode`、`allow_cloud_context` 与绑定记忆 ID、内容摘要和策略的 HMAC。
- efficiency / hybrid 的规划召回只接受来源允许出网且策略完整性有效的记忆。privacy 记忆与缺少来源策略的旧记忆仅用于本地上下文；普通的确认或 promotion 不会解除出网限制。
- 管家规划构造跨事项上下文时过滤 privacy 事项的目标；未标注来源策略的会话推断偏好也不会进入云端规划。
- 显式偏好和自动保存的事实均绑定事项模式。修订记忆继承父记录的限制，修订时准备 embedding 也走本地路由，避免在持久化过滤之前出网。

主要文件：`backend/app/core/context_egress.py`、`backend/app/agents/memory_agent.py`、`backend/app/core/db_memory.py`、`backend/app/services/butler_service.py`、`backend/app/services/butler_conversation.py`、`backend/app/services/butler_memory.py`。

### F02：脱敏日志下的业务提交去重

- 在 proposal 脱敏之前生成业务提交 HMAC 摘要并持久化。`submit_form` 与 `act(kind=submit)` 使用同一规范化规则，URL fragment 不影响业务身份。
- 业务摘要不包含 task / step / approval ID；另存 HMAC 将摘要绑定到具体工具执行记录，防止篡改或复制摘要改变历史匹配结果。
- 历史 committed、outcome_unknown、executing 记录都参与保护；只有本次执行的当前 executing claim 可以排除。其他步骤、其他任务以及已 committed 的同一记录不能借此跳过检查。
- 日志继续脱敏 selector 与 fields。缺少摘要的旧脱敏提交记录在相同端点保守阻断；真实执行产生并保存的 `not_submitted` 回执允许安全重试。

主要文件：`backend/app/services/butler_effects.py`、`backend/app/orchestration/tool_runtime_lifecycle.py`、`backend/app/orchestration/tool_runtime_execution.py`、`backend/app/orchestration/butler_runtime.py`、`backend/app/core/schemas.py`。

### F03：长文档分块召回

- FTS 保存真实 `chunk_id`；旧 FTS 表在初始化时重建映射，保留文档块与已有 embedding，无需重新调用模型。
- 按命中 chunk 与 BM25 rank 获取候选；权限过滤和相关性排序在 LIMIT 之前完成。
- 带 embedding 与无 embedding 的命中共享同一个候选预算。词法回退和 LIKE 回退也保留实际命中块，避免再次退回文首片段。

主要文件：`backend/app/indexer/vector_index.py`、`backend/app/indexer/fts_index.py`、`backend/app/core/db_schema.py`。

## 验证

新增 50 项回归，覆盖真实 provider 发送入口、协调开关、efficiency / hybrid、已完成偏好、暂停事项、旧记忆与策略篡改、本地修订 embedding、生产 proposal / journal、两种提交入口交叉、执行 claim、提交状态、安全重试、末尾关键词、候选竞争、权限过滤、无 embedding / LIKE 回退与 FTS 迁移。

| 检查 | 结果 |
| --- | --- |
| 新增回归 + 既有语义搜索测试 | 60 passed；[日志](../.tmp/audit-fixes-20261011/regressions.log) / [JUnit](../.tmp/audit-fixes-20261011/regressions.xml) |
| 后端全量 pytest | 4825 passed、22 skipped、3 warnings；960.59 秒；[日志](../.tmp/audit-fixes-20261011/backend-pytest.log) / [JUnit](../.tmp/audit-fixes-20261011/backend-pytest.xml) |
| 后端 Ruff | 全树 lint 通过；本次 17 个 Python 文件格式检查通过 |
| 源码规模门禁 | 通过，P95 = 789，阈值 800；[日志](../.tmp/audit-fixes-20261011/source-size.log) |
| npm audit：根目录 / desktop / mobile | 均因 advisory endpoint `ETIMEDOUT` 未完成，JS 依赖漏洞状态仍未知 |
| 严格发布门禁 | 未通过，仍有 7 项 stop-ship 验收证据待完成，候选绑定与负责人签署缺失；[日志](../.tmp/audit-fixes-20261011/release-readiness.log) |

新增回归使用隔离的临时数据库、虚构内容和外部发送 / 执行替身；没有实际发送隐私内容或提交真实表单。代码修复和本地测试不能代替 Windows 干净机器、真实模型、Android 链路、签名安装包和人工签署验收。

全量测试退出码为 0，JUnit 记录 4847 项、0 failures、0 errors。22 项跳过涉及原生应用验收开关、符号链接权限或平台差异；3 项警告来自 developer_tools 测试的子进程输出 UTF-8 解码异常，具体堆栈保存在日志中。测试结束后核对了本轮 17 个 Python 文件的 SHA-256，均与开始测试时一致。

原始隐私和搜索脚本在最终代码上再次运行：云端请求不含 privacy 目标或偏好；搜索候选和结果均包含 chunk 99。输出：[隐私复现](../.tmp/audit-fixes-20261011/privacy-after.json) / [检索复现](../.tmp/audit-fixes-20261011/search-after.json)。F02 由生产 journal 回归覆盖；原始缺陷脚本在后续任务尝试重复提交时已触发预期的 `ButlerExecutionDeferred`。

## 兼容性

- 旧记忆仍可管理，并用于 privacy 本地上下文；缺少来源证明时不会自动迁移为云端可用。
- 旧脱敏提交日志可能需要先核对回执，保护逻辑不会自动重放。
- FTS 格式增加未索引的 chunk ID 列；初始化迁移只重建 FTS 派生数据。
- 本轮未提交 Git commit，也未改写发布看板的验收状态。
