# Lengrvis 全项目审查 · 2026-10-18

> **2026-10-19 修复记录：** 本报告正文保留审查时点的结论。F01–F14、S01–S11 已实施代码修复，验证范围及剩余发布验收见文末；修复提交见 Git 历史，不代表完成发布签署。

> 哥哥，本轮确认 **4 类 P1、10 类 P2、11 类 P3**。上一轮 F01 的记忆/事项隔离已生效，但会话摘要仍是一条未受来源策略约束的出网通道；隐私删除和保留清理各有一个可复现的自锁缺陷。自动化测试大部分通过，但后端全量在当前 DNS 环境下有 10 项环境性失败；严格发布门禁仍未通过。

## 审查对象与结论边界

- 工作区：`D:/Desktop/mavris`；分支 `main`；HEAD `00500c4094608ac636ce9e55edae74ae9d7606bc`。
- 覆盖当前工作树：176 个已修改跟踪文件 + 151 个未跟踪文件，包括 Butler、浏览器、Office、隐私删除、移动会话相关代码。结论不是只针对 HEAD。
- 源码规模门禁口径：1,132 个源文件 / 269,887 行；后端 450 个文件，桌面 401 个，移动 55 个。
- 方法：14 个高风险域并行只读审查（每个域最多 6 条候选），主会话对高价值候选逐条读代码、做临时数据目录复现，再与上一轮 F01–F03 修复去重。报告只把已复现或代码路径完整的条目列为“确认”；代理给出但未亲验的条目单列，不计入确认数。
- 未声称逐行人工审阅全部源码，也不把本轮结果当作发布签署。原有源码文件内容未改动；本轮仅新增报告与复现材料。

## 结论摘要

| 级别 | 数量 | 代表问题 |
|---|---:|---|
| P1 | 4 | 隐私任务摘要进入云端规划；OCR/视觉上传绕过 `allow_file_content_upload`；含设置删除后权限策略永久失效；observe-only 授权在 trusted_edits 下自动写文件 |
| P2 | 10 | 保留清理触发敏感完整性自锁；`public_matter` 脱敏破坏结构化答复；过期审批让运行永久等待；失败工具让事项不可恢复；提交去重可被大小写绕过；读取敏感字段；FTS 残留；DOCX 页眉页脚漏脱敏；xlsm 宏丢失；移动端取消生物识别即解配 |
| P3 | 11 | 定时任务时区、截图产物、终态事项可取消、点击提交绕过账本、FTS 权限过滤顺序、UIA API 参数、IPC 冒烟缺口、提交 URL 规范化、下载监听泄漏、运行失败后任务非终态、发布签名门禁只读文本 |

## 确认的问题

### P1

#### F01 · P1 · 隐私任务的会话摘要会进入后续 efficiency/hybrid 云端请求

**影响：** privacy 模式下产生的目标、工具输出和观察文本会被写入全局 `session_contexts`，之后任意 efficiency/hybrid 任务的 planner 提示与 provider 请求都会带上这段摘要。摘要持久化在 SQLite 中，跨重启保留，直到被下一次摘要覆盖；它绕过了上一轮 F01 对 memory/matter 的出网限制。

**触发条件：** 任一 privacy 任务进入终态（或对 privacy 任务执行摘要/压缩），随后启动 efficiency 或 hybrid 任务。后者本应只携带自己的上下文。

**原因：** [`completion_handler.py:126-173`](../backend/app/orchestration/handlers/completion_handler.py#L126-L173) 在任务完成时调用 `summarize_messages()` 并把结果写入 `store.remember_summary()`，没有传入任务 mode，也没有来源策略；[`session_context.py:46-64`](../backend/app/core/session_context.py#L46-L64) 的 `context_for_planning()` 把 `conversation_summary` 作为全局上下文返回；[`planning_handler.py:374-380`](../backend/app/orchestration/handlers/planning_handler.py#L374-L380) 和 [`management.py:1305-1309`](../backend/app/context/management.py#L1305-L1309) 不做目标 mode 过滤；[`planner_agent.py:295-297`](../backend/app/agents/planner_agent.py#L295-L297) 将其拼进 planner prompt。

**证据：** 临时数据目录中写入一条包含 `PRIVATE_GOAL_MARKER_8842` 的 privacy 任务消息，调用 `_update_session_summary()` 后，`planning_context()["conversation_summary"]` 含该标记；再用 efficiency 设置调用 `project_messages_for_llm()`，投影消息同样含该标记。[复现脚本](../docs/audit-evidence-2026-10-18/reproduce_privacy_summary.py) · [输出](../docs/audit-evidence-2026-10-18/reproduce-privacy-summary.log)。

**修复方向：** 为摘要持久化来源 mode 与出网策略（HMAC 绑定，类似 `context_policy_hmac`）；在 `planning_context()`、provider `prepare()` 和 planner 格式化边界按目标 mode 过滤；或按 mode/会话隔离摘要。补一条“privacy 任务完成后 efficiency 请求正文不含其目标”的回归。

#### F02 · P1 · OCR/视觉云回退忽略 `allow_file_content_upload`

**影响：** 默认配置（`allow_file_content_upload=false`，见 [config.py:194](../backend/app/config.py#L194) 与 `.env:23`）下，efficiency 模式仍会把本地图片、扫描页、PDF 页面和收据图片送到云端 provider；图片以 base64 data URL 发送。设置页里的“允许上传文件内容”开关对这些路径不起作用。

**触发条件：** efficiency 模式，本地 OCR/视觉不可用或返回空文本，调用 `document`/`vision` 相关 OCR、`describe_image`、图片 embedding 回退；或索引器对扫描 PDF 做 OCR 回退。

**原因：** [`ocr_service.py:88-100`](../backend/app/indexer/ocr_service.py#L88-L100) 只用 `can_use_cloud_model(..., task="ocr").allowed`；[`privacy.py:30-35`](../backend/app/policy/privacy.py#L30-L35) 按 mode 返回允许，完全不看 `allow_file_content_upload`；[`vision_tools.py:186-196`](../backend/app/tools/vision_tools.py#L186-L196) 的 `_run_vision` 直接取 provider；[`openai_compatible.py:859-866`](../backend/app/llm/openai_compatible.py#L859-L866) 读取文件并编码上传。已有的 `can_upload_file_content()` 只在文档解析和文本 embedding 路径使用。

**证据：** 用 fake cloud provider 记录调用：`allow_file_content_upload=False` 时 `describe_image()` 仍调用 `provider.vision(path)`；`ocr_image_result()` 在本地 OCR 空结果后仍调用 `provider.ocr(path)`。两次都没有真实网络请求。[视觉复现](../docs/audit-evidence-2026-10-18/reproduce_vision_upload.py) · [OCR 复现](../docs/audit-evidence-2026-10-18/reproduce_ocr_upload.py) · [视觉输出](../docs/audit-evidence-2026-10-18/reproduce-vision-upload.log) · [OCR 输出](../docs/audit-evidence-2026-10-18/reproduce-ocr-upload.log)。

**修复方向：** 把“文件内容出网”统一到一个决策函数，要求 mode 允许、`allow_file_content_upload` 为真、并且工具/服务边界显式放行；OCR、vision、image embedding、PDF OCR 全部调用它。补正向和反向回归，断言 cloud provider 未被调用。

#### F03 · P1 · 含设置的本机数据删除会让权限策略永久失效

**影响：** 用户在隐私面板勾选“同时删除设置”并完成删除后，`permission_policies` 的默认策略行被删，但完整性 bootstrap anchor 仍为 `complete`，重启也不会补建。之后所有权限策略读取抛出 `SensitiveRecordIntegrityError`，工具执行权限检查、`GET/PUT /api/settings/permission-policy` 都失败；删除接口本身仍返回成功。

**触发条件：** 桌面隐私面板勾选 `includeSettings`（[PrivacyDataPanel.tsx:16,28,64](../desktop/src/renderer/components/settings/PrivacyDataPanel.tsx#L16)），完成 prepare/erase/complete 流程，然后执行任意需要权限策略的操作或重启。

**原因：** [`db_maintenance.py:55`](../backend/app/core/db_maintenance.py#L55) 把 `permission_policies` 放进 `SETTINGS_TABLES`，[`db_maintenance.py:71-105`](../backend/app/core/db_maintenance.py#L71-L105) 删除策略及其 proof/presence；[`db_sensitive_integrity.py:267-269`](../backend/app/core/db_sensitive_integrity.py#L267-L269) 只在 `anchor_state == "pending"` 时补建默认策略；[`permissions.py:203-207`](../backend/app/policy/permissions.py#L203-L207) 在 anchor complete 且默认行为空时抛错。

**证据：** 临时 DB 中 `get_policy()` 正常 → `erase_local_user_data(include_settings=True)` → `get_policy()` 抛 `SensitiveRecordIntegrityError: Sensitive permission policy record is missing for default`；再次 `bootstrap_sensitive_record_integrity()` 后仍抛错。[复现脚本](../docs/audit-evidence-2026-10-18/reproduce_erase_policy_lockout.py) · [输出](../docs/audit-evidence-2026-10-18/reproduce-erase-policy.log)。

**修复方向：** anchor complete 时也检测默认策略行/proof/presence 缺失并补建；或在含设置删除时重置 anchor 与 bootstrap proof，让下次启动重新播种。补“含设置删除后 `get_policy()` 仍可用”的回归。

#### F04 · P1 · observe-only 授权在 trusted_edits/auto_review 下可自动批准主动事项的文件写入

**影响：** 用户给管家授予“只读观察”目录范围，并启用 `trusted_edits`/`auto_review` 后，文件监听产生的主动事项可以让 `file.write_text`、`file.edit_text` 等 R2 工具直接执行，不创建审批；`file_modify` 授权范围被绕过。

**触发条件：** 主动事项（`source != "user"`）绑定 observe-only scope，模型规划文件写入工具；全局 permission mode 为 `trusted_edits` 或 `auto_review`。

**原因：** [`butler_runtime.py:67`](../backend/app/orchestration/butler_runtime.py#L67) 调用 `action_scope(tool.name, args, scopes)` 未传 action，默认 `observe`；[`butler_scopes.py:113-130`](../backend/app/services/butler_scopes.py#L113-L130) 对 `file.`/`document.` 只检查目录，不要求 `file_modify`；[`tool_runtime_approval.py:27-29`](../backend/app/orchestration/tool_runtime_approval.py#L27-L29) 在 trusted/auto 且 reversible edit 时先返回 `False`，不会进入 `reversible_scope` 的 `file_modify` 校验。

**证据：** 临时 DB 中创建 `allowed_actions=["observe"]` 的目录 scope 和 `source="event"` 的 matter/task；`validate_task_action(file.write_text)` 返回 PASS；`trusted_reversible_edit_allowed=True`，`requires_runtime_approval=False`。[复现脚本](../docs/audit-evidence-2026-10-18/reproduce_scope_write_bypass.py) · [输出](../docs/audit-evidence-2026-10-18/reproduce-scope-write.log)。

**修复方向：** 按工具族映射所需 scope action（`file.`/`document.` → `file_modify`，UI 编辑 → `ui_edit`，浏览器写 → `browser_prepare`），在 trusted/auto 短路之前校验；补 observe-only + trusted_edits 的拒绝回归。

### P2

#### F05 · P2 · 保留清理删除审批 proof 却留下 presence 行，触发敏感完整性自锁

**影响：** 后台启动时执行保留清理，删除过期任务的审批及其 `sensitive_record_integrity` proof，但 `sensitive_record_presence` 仍保留对应行。`sensitive_integrity_check()` 把“presence 有、记录没有”判为失败，`require_sensitive_integrity_ok()` 抛错；审批挑战、回滚、隐私删除 prepare 都会被拒绝，且重启后仍失败（bootstrap 只补不删）。

**触发条件：** 存在超过保留窗口（默认 30 天）的终态任务审批或孤儿审批，重启后端 / 执行保留清理后访问审批或隐私删除接口。

**原因：** [`local_retention_service.py:462-469`](../backend/app/services/local_retention_service.py#L462-L469) 只删 `sensitive_record_integrity`；[`db_sensitive_integrity.py:172-183`](../backend/app/core/db_sensitive_integrity.py#L172-L183) 对缺失记录报失败；[`lifespan.py:56-60`](../backend/app/lifespan.py#L56-L60) 启动即执行清理。

**证据：** 临时 DB 插入旧任务与审批，`cleanup_expired_task_details()` 后 `sensitive_integrity_check()["ok"]` 为 False，失败项为 `approvals:<id> Sensitive local record is missing for a presence ledger entry`；`require_sensitive_integrity_ok()` 抛错。[复现脚本](../docs/audit-evidence-2026-10-18/reproduce_retention_presence.py) · [输出](../docs/audit-evidence-2026-10-18/reproduce-retention-presence.log)。

**修复方向：** 在 `_delete_sensitive_integrity_rows()` 中同步删除对应 `sensitive_record_presence`；或让完整性检查区分“有意删除”的 proof。补清理后 `ok is True` 且审批/删除接口可用的回归。

#### F06 · P2 · `public_matter` 脱敏破坏结构化交互字段和关联事项 ID

**影响：** 需要补充信息的事项在桌面上显示为多个同名 `***` 输入框；提交的 answers 键与后端要求的真实字段名不一致，返回 400“回答包含未请求的信息项”。需要选择前置/后续事项的事项，下拉选项变成 `[REDACTED_TOKEN]`，提交必然 400；占位事项永久停在 `waiting_input`，前置关系无法落库。

**触发条件：** 模型决策 `action="ask"` 且带 `missing_fields`；或一句话匹配多个既有事项，创建 `choose_dependency` 占位事项。

**原因：** [`butler_state.py:31-62`](../backend/app/services/butler_state.py#L31-L62) 对整个 payload 调 `redact_value()`，仅回填少量 ID；[`redaction.py:40`](../backend/app/policy/redaction.py#L40) 把 `fields` 视为表单容器，[`redaction.py:277-299`](../backend/app/policy/redaction.py#L277-L299) 把其中每个字符串替换成 `***`；[`redaction.py:74`](../backend/app/policy/redaction.py#L74) 的 24+ token 规则把 `matter_<32hex>` 替换成 `[REDACTED_TOKEN]`。桌面端用 `interaction.fields` 生成输入框（[ButlerView.tsx:223,236](../desktop/src/renderer/features/butler/ButlerView.tsx#L223)），用 `coordination_request.parents/children` 作为选项（[ButlerView.tsx:211](../desktop/src/renderer/features/butler/ButlerView.tsx#L211)）；后端却用真实值校验（[butler_conversation.py:371-378](../backend/app/services/butler_conversation.py#L371-L378)、[407-411](../backend/app/services/butler_conversation.py#L407-L411)）。

**证据：** `public_matter()` 输出 `interaction.fields == ["***","***"]`；`coordination_request.parents/children == ["[REDACTED_TOKEN]"]`，而 `versions` 的键仍是真实 matter id。[复现脚本](../docs/audit-evidence-2026-10-18/reproduce_public_matter.py) · [输出](../docs/audit-evidence-2026-10-18/reproduce-public-matter.log)。

**修复方向：** 在受控 ID 回填中加入 `interaction.fields` 与 `coordination_request.parents/children`，或对该结构使用 key-aware 脱敏而不是整体 `redact_value`。补 API 契约测试：用 `public_matter` 返回值构造 `MatterResponse` 能成功 `respond`。

#### F07 · P2 · 审批过期后步骤仍 WAITING、运行永久 AWAITING_APPROVAL

**影响：** 用户离开超过审批 TTL（R3 约 5 分钟、R2 约 10 分钟）后，审批列表接口会把待审批记录标记为 expired；步骤仍停在 WAITING，Run 仍为 AWAITING_APPROVAL。`resume_run()` 对 AWAITING_APPROVAL 直接返回，不会重新规划或拒绝；UI 没有可处理的待审批项，运行看起来永远转圈，只能取消任务。

**触发条件：** 创建审批后等待超过 TTL，再打开审批列表或移动端轮询。

**原因：** [`mobile_pairing_service.py:825-832`](../backend/app/services/mobile_pairing_service.py#L825-L832) 仅发布 `approval.decided` 事件；[`db_approvals.py:390-406`](../backend/app/core/db_approvals.py#L390-L406) 只改审批状态；[`run_service.py:483-486`](../backend/app/services/run_service.py#L483-L486) 对 AWAITING_APPROVAL 直接 `return run`。`_deny_waiting_steps_for_expired_approvals` 只从暂停/取消路径调用，过期扫描没有调用。

**证据：** 代码路径完整；本轮未构造真实等待 5 分钟的端到端运行（避免占用真实审批）。[审批过期入口](../backend/app/services/mobile_pairing_service.py#L825-L832) · [resume 短路](../backend/app/services/run_service.py#L483-L486)。

**修复方向：** 过期扫描后把对应 step 置为 denied/failed，并让 run 进入可恢复终态；或在 `resume_run()` 发现“无 pending approval 且存在 WAITING step”时重新规划。补 TTL 过期回归。

#### F08 · P2 · 普通失败的非 R0 工具结果被当作“结果不明”，事项无法再继续

**影响：** 任意一次明确失败的非 R0 工具调用（例如 `file.write_text` 返回“Path is not a file.”）会被记为 `committed`，但 `ok=False`，因此永远不进入 `reviewed_results`；`uncertain_execution()` 把它视为结果不明。之后 dispatch、`verify_conditions`、`resume_readiness` 都拒绝继续；用户提交 reconcile 回答只清 interaction，不清 ToolCall，下一次 dispatch 仍被同一个调用拦住，只能取消事项。

**触发条件：** 非 R0 工具明确失败一次，或写工具在 `tool.execute` 前被暂停/撤权导致异常，随后尝试继续事项。

**原因：** [`tool_runtime.py:562-565`](../backend/app/orchestration/tool_runtime.py#L562-L565) 对非 `outcome_unknown` 结果一律 `mark_tool_call_committed`；[`butler_evidence.py:167-179`](../backend/app/services/butler_evidence.py#L167-L179) 只收 `result.ok and review allows reuse`；[`butler_evidence.py:48-63`](../backend/app/services/butler_evidence.py#L48-L63) 把 committed 且不在 reviewed 的调用判为 uncertain；[`butler_service.py:606-614`](../backend/app/services/butler_service.py#L606-L614) 只设置 reconcile interaction。

**证据：** 临时 DB 插入 committed + ok=False 的 `file.write_text` 结果；`reviewed_results=0`、`uncertain_execution=True`、`verify_conditions` 返回“已有操作结果尚不明确…”。[复现脚本](../docs/audit-evidence-2026-10-18/reproduce_uncertain_failure.py) · [输出](../docs/audit-evidence-2026-10-18/reproduce-uncertain-failure.log)。

**修复方向：** 区分“已确认无副作用的失败”和“未知结果”：为可安全重试的失败写 `no_side_effect`/`failed` 状态，并在 reconcile 确认后解除该调用的阻塞；或为失败结果生成可审查的 `reviewed_results` 条目。

#### F09 · P2 · `browser.act` 的提交识别未规范化 kind，重复提交保护可被大小写绕过

**影响：** 实际执行路径会把 `kind="Submit"`、`"submit "`、`"submit-form"` 规范化为 `submit` 并执行提交，但 `is_submission()` 只认精确的 `"submit"`；这类调用不会写入 `submission_digest`/`submission_binding_hmac`，后续 `repeated_submission()` 直接放行，F02 的跨任务去重失效。

**触发条件：** 模型（或被网页注入影响的模型）产出 `browser.act`，`action.kind` 不是小写精确值；执行后同一事项的后续任务再次提交同一表单。

**原因：** [`butler_evidence.py:66-71`](../backend/app/services/butler_evidence.py#L66-L71) 精确比较 `action.get("kind") == "submit"`；[`browser_activity_runtime.py:1216-1224`](../backend/app/services/browser_activity_runtime.py#L1216-L1224) 通过 `_canonical_action_kind()` 做 casefold/strip/别名映射；[`browser_session_scope.py:159-171`](../backend/app/services/browser_session_scope.py#L159-L171) 的映射包含 `submit-form`。`browser.act` 注册时没有 `validate_input`。

**证据：** 插入 committed 的 `kind="Submit"` 调用后，`is_submission(prior)=False`，同一表单的 `kind="submit"` 调用 `repeated_submission()=False`；`_normalize_action()` 返回 `submit`。[复现脚本](../docs/audit-evidence-2026-10-18/reproduce_submission_kind.py) · [输出](../docs/audit-evidence-2026-10-18/reproduce-submission-kind.log)。

**修复方向：** 把提交判定统一到执行侧规范化函数：先合并顶层/嵌套 action，再 `_canonical_action_kind()`，再判断 submit；并为 `browser.act` 增加运行时 schema 校验或拒绝非规范 kind。

#### F10 · P2 · 读取路径的敏感字段守卫缺少 ssn/pin/totp/cc-number 等

**影响：** 托管浏览器可以用 `browser.read_page`/`browser.observe` 指定 `#ssn`、`#pin`、`#totp`、`autocomplete=cc-number` 等字段，字段值会作为工具结果返回给模型，并在 efficiency/hybrid 模式进入云端请求。后端 `read_page` 不调用 `_sensitive_selector()`，唯一拦截是 Electron 的正则，而该正则不覆盖这些名称。

**触发条件：** 已登录页面包含 SSN/PIN/TOTP/信用卡号字段；模型或被注入的页面内容要求读取该字段。

**原因：** [`browserObservation.ts:4-8`](../desktop/src/main/browserObservation.ts#L4-L8) 的正则只有 password/pwd/token/secret/credit/card number/cvv/cvc/otp/one-time/verification code；[`browser_tools.py:188-204`](../backend/app/tools/browser_tools.py#L188-L204) 的 `read_page` 把 selector 原样传给 runtime，不调用 [`_sensitive_selector()`](../backend/app/tools/browser_tools.py#L294-L296)；[`browser_activity_safety.py:139-144`](../backend/app/services/browser_activity_safety.py#L139-L144) 对 `text` 值不做脱敏。

**证据：** 正则对 `ssn/pin/totp/mfa_code/authCode/securityCode/cc-number/pan` 均不匹配；`browser.read_page` 路径无后端敏感 selector 检查。[正则探针](../docs/audit-evidence-2026-10-18/reproduce-sensitive-selector.log)（同一台机器上的 Node 探针；截图条目另见 S02）。

**修复方向：** 让 Electron 与后端共用敏感 autocomplete/name 词表（ssn、pin、pan、totp、mfa、security code、auth code 等），后端在 read/observe 的 selector 与 selectors 上也拒绝敏感目标；补 `#ssn`、`#pin`、`autocomplete=cc-number` 的拒绝测试。

#### F11 · P2 · 本机数据删除漏掉 `document_chunks_fts`，索引正文残留

**影响：** 删除本机数据后，`document_chunks`/`indexed_files` 已清空，但 FTS5 影子表 `document_chunks_fts` 仍保存每个分块的全文和路径；`FTSIndex.search` 仍能直接查询到已删除文档的文本。删除后的 VACUUM 不会清理这些仍然存活的 FTS 行；只有之后某次 `db.init_db()` 的计数核对/重建才会顺带删除。

**触发条件：** 索引过任意本地文档，执行隐私删除，然后立即查询 FTS 或检查数据库文件。

**原因：** [`db_maintenance.py:9-54`](../backend/app/core/db_maintenance.py#L9-L54) 的 `PERSONAL_DATA_TABLES` 不含 `document_chunks_fts`，[`db_maintenance.py:71-105`](../backend/app/core/db_maintenance.py#L71-L105) 的残留校验也只统计列表内表；[`db_schema.py:511-521`](../backend/app/core/db_schema.py#L511-L521) 的 FTS 表是内容型 `fts5(file_id, path, text, chunk_id)`；[`fts_index.py:488-498`](../backend/app/indexer/fts_index.py#L488-L498) 直接 `FROM document_chunks_fts`。

**证据：** 临时 DB 插入含 `ERASED_DOCUMENT_MARKER_7731` 的 FTS 行，`erase_local_user_data()` 后 `document_chunks_fts` 仍有 1 行且全文可读；DB 文件中仍能搜到标记字节。[复现脚本](../docs/audit-evidence-2026-10-18/reproduce_fts_erase.py) · [输出](../docs/audit-evidence-2026-10-18/reproduce-fts-erase.log)。

**修复方向：** 在删除事务中显式 `DELETE FROM document_chunks_fts`（并保持 `PRAGMA secure_delete=ON`），把 FTS 行数纳入残留校验；补“删除后 FTS 计数为 0、文件中无标记”的回归。

#### F12 · P2 · DOCX 脱敏只处理正文和表格，页眉/页脚/文本框中的 PII 原样保留

**影响：** `document.apply_redaction` 对 .docx 返回成功，但页眉、页脚（以及文本框中的内容）没有脱敏；用户以为文件已清理，实际 PII 仍随文件外发。

**触发条件：** DOCX 的页眉/页脚/文本框包含邮箱、电话、证件号等；执行脱敏。

**原因：** [`document_intelligence_service.py:1261-1295`](../backend/app/services/document_intelligence_service.py#L1261-L1295) 的 `_apply_redaction_docx()` 只遍历 `doc.paragraphs` 和 `doc.tables`，没有遍历 `section.header/footer`、`doc.sections` 中的文本框或其他 part。

**证据：** 临时 DOCX 正文写 `alice@example.com`、页眉和页脚也写同一邮箱；脱敏后正文变为 `[REDACTED:EMAIL]`，页眉/页脚仍是 `alice@example.com`。[复现脚本](../docs/audit-evidence-2026-10-18/reproduce_redaction_header.py) · [输出](../docs/audit-evidence-2026-10-18/reproduce-redaction-header.log)。

**修复方向：** 遍历所有 sections 的 header/footer、嵌套表格、文本框/绘图中的段落；或明确拒绝含未支持 part 的文档，而不是报告成功。补页眉/页脚/文本框回归。

#### F13 · P2 · `document.edit_xlsx` 对 `.xlsm` 直接 load+save，静默删除 VBA 宏

**影响：** 通过 `document.edit_xlsx` 修改一个启用宏的工作簿（.xlsm）时，openpyxl 默认不保留 `vbaProject.bin`；保存后文件不再含宏，业务自动化/签章逻辑丢失。工具返回成功，用户很难发现。

**触发条件：** `path` 指向 `.xlsm`，`dry_run=false`，执行 `edit_xlsx`。

**原因：** [`document_intelligence_service.py:1205-1249`](../backend/app/services/document_intelligence_service.py#L1205-L1249) 用 `load_workbook(path_obj)`，没有 `keep_vba=True`，也没有拒绝 `.xlsm`；[`document_tools.py:577-585`](../backend/app/tools/document_tools.py#L577-L585) 的 schema 未限制扩展名。

**证据：** 代码路径确认；本轮没有可用的真实宏工作簿，未做二进制级复现。openpyxl 的 `keep_vba` 语义是公开契约；建议修复前先用真实 .xlsm 验证。[编辑入口](../backend/app/services/document_intelligence_service.py#L1222) · [schema](../backend/app/tools/document_tools.py#L577-L585)。

**修复方向：** 对 `.xlsm` 使用 `keep_vba=True` 并验证 VBA part 仍在；或拒绝 `.xlsm/.xltm` 编辑，要求用户另存为 .xlsx。补宏工作簿回归。

#### F14 · P2 · 移动端取消一次生物识别解锁会删除配对令牌与 LAN 证书 pin

**影响：** 用户在手势/面容识别弹窗点取消，`loadSession()` 的外层 catch 会调用 `clearStoredSessionStrict()`，删除 secure store 中的 token/refresh token，并清理 TLS 信任；手机被静默解配，需要重新扫码。用户没有选择“退出登录”。

**触发条件：** 已配对手机启动/解锁时，`SecureStore.getItem(..., requireAuthentication: true)` 因用户取消而 reject。

**原因：** [`mobile/src/store/auth.ts:72-85`](../mobile/src/store/auth.ts#L72-L85) 读取带生物识别的 secure store；[`auth.ts:114-119`](../mobile/src/store/auth.ts#L114-L119) 的 `loadSession` 外层 catch 对所有非 `SessionRecoveryError` 都清空；取消错误不是 `AuthExpiredError`/`InsecureLanBaseUrlError`，因此走到清空分支。

**证据：** 代码路径完整；本轮未在真机上取消生物识别弹窗。[读取与清理](../mobile/src/store/auth.ts#L72-L119)。

**修复方向：** 区分“用户取消/系统中断”和“凭据损坏/过期”：取消时保留会话并返回可重试状态，只有明确无效或过期才清理；补取消路径单测。

### P3

| ID | 问题 | 位置 | 证据 / 状态 |
|---|---|---|---|
| S01 | Guardian 接管后定时任务忽略 `schedule.timezone`，非 UTC 任务按 UTC 触发；Guardian 创建接口也不接收 timezone | [routes_guardian.py:274-282](../backend/app/api/routes_guardian.py#L274-L282) · [guardian_scheduler.py:63](../backend/app/services/guardian_scheduler.py#L63) · [scheduler_service.py:34-52](../backend/app/services/scheduler_service.py#L34-L52) | 代码确认；`_next_run()` 调用未传 `timezone` |
| S02 | 截图目录命名改为 `<16 hex>`，后端校验仍要求 `<pid>-<uuid>`，所有桌面截图 artifact 被静默丢弃；adapter 也不回传 `screenshot_url` | [browser_host_bridge_service.py:25](../backend/app/services/browser_host_bridge_service.py#L25) · [:356-374](../backend/app/services/browser_host_bridge_service.py#L356-L374) · [browserScreenshotStore.ts:170-171](../desktop/src/main/browserScreenshotStore.ts#L170-L171) · [desktop_browser_adapter.py:44-66](../backend/app/services/desktop_browser_adapter.py#L44-L66) | 已复现正则不匹配：[输出](../docs/audit-evidence-2026-10-18/reproduce-screenshot-regex.log) |
| S03 | `control_matter()` 只对 resume 拦终态；已完成事项可被 pause/cancel 覆盖为 paused/cancelled，resume 不恢复，依赖方被改写为“前置失败/取消” | [butler_resident.py:277-326](../backend/app/services/butler_resident.py#L277-L326) · [ButlerChatContext.tsx:17](../desktop/src/renderer/features/butler/ButlerChatContext.tsx#L17) | 代码确认；与 `respond`/`update_arrangement` 的终态守卫不一致 |
| S04 | 点击提交按钮不会触发 `BrowserSubmissionGuard`，也不进入 `is_submission` 账本；第二次点击提交可绕过事项级去重 | [browserTargetSnapshot.ts:83,98-99](../desktop/src/main/browserTargetSnapshot.ts#L83) · [browserHost.ts:429-434](../desktop/src/main/browserHost.ts#L429-L434) · [butler_evidence.py:69-71](../backend/app/services/butler_evidence.py#L69-L71) | 代码确认；需真实页面做端到端复现 |
| S05 | `FTSIndex.search` 在 SQL `LIMIT` 之后才做目录权限过滤，且无 BM25 排序；索引残留行可挤掉授权结果 | [fts_index.py:481-498](../backend/app/indexer/fts_index.py#L481-L498) | 代码确认；与 F03 的 vector 路径修复不是同一路径 |
| S06 | `/api/ui-automation/action` 把用于选 action 的 `action`/`kind` 键原样传给严格 schema（`extra="forbid"`），所有动作在真实执行前被拒 | [routes_ui_automation.py:168-202,249-256](../backend/app/api/routes_ui_automation.py#L168-L202) · [ui_automation_contracts.py:215-224](../backend/app/tools/ui_automation_contracts.py#L215-L224) | 代码确认；路由层未剔除调度键 |
| S07 | 新增 Butler IPC 通道没有进入 `ipc-security-smoke.cjs`，也没有 handler 级测试；安全冒烟仍只覆盖旧通道 | [ipc.ts:121-130](../desktop/src/shared/ipc.ts#L121-L130) · [ipc-security-smoke.cjs](../desktop/scripts/ipc-security-smoke.cjs) | 代码/脚本确认；`butler*` 未出现在 smoke |
| S08 | `submission_identity()` 只去掉 URL fragment，不规范化 host 大小写、默认端口、`.`/`..` 段；等价 URL 产生不同摘要，事项级去重可被绕过 | [butler_effects.py:20-28](../backend/app/services/butler_effects.py#L20-L28) | 已复现：`same_url_blocked=True`、`equivalent_url_blocked=False`，[输出](../docs/audit-evidence-2026-10-18/reproduce-submission-url.log) |
| S09 | `hardenEmbeddedWebContents()` 每个 tab 都向共享 Session 注册 `will-download`，`destroyed` 只删 request route，不删下载监听；长期运行会泄漏已关闭事项并重复触发 blocked-download 事件 | [browserHostWebContentsHardening.ts:44-58](../desktop/src/main/browserHostWebContentsHardening.ts#L44-L58) | 已复现：两个 tab 销毁后监听数仍为 2，[输出](../docs/audit-evidence-2026-10-18/reproduce-download-listener.log) |
| S10 | 引擎循环异常/最大轮次只把 Run 置 FAILED，不更新 Task；Task 可能永久停在 planning/executing，无法 resume | [run_service.py:842-859](../backend/app/services/run_service.py#L842-L859) | 代码确认；未构造完整引擎异常 |
| S11 | 严格/RC 发布门禁只读取证据文本中的 `Owner signature verification: verified`，不独立验签；文本可被伪造 | [check_release_readiness_dashboard.py:346-383](../scripts/check_release_readiness_dashboard.py#L346-L383) | 代码确认；属于发布门禁完整性，不是运行时漏洞 |

## 上一轮 F01–F03 复核

- **F01（隐私记忆/事项出网）**：`context_egress.py` 的来源模式、HMAC 与 memory/matter 过滤已生效；`test_planning_context_privacy.py` 的 28 个用例在隔离 DNS 异常后全部通过。但 **会话摘要**没有来源策略，是同一威胁模型下的新旁路（F01），因此上一轮 F01 不能视为完整关闭。
- **F02（脱敏日志下的提交去重）**：业务摘要与 binding HMAC 正常，`test_butler_submission_journal.py` 覆盖的 fragment、跨任务、别名、committed/outcome_unknown 场景通过。残留问题为 kind 规范化（F09）与 URL 规范化（S08）。
- **F03（FTS 命中分块）**：`vector_index.py` 已保留 `chunk_id` 与 BM25 rank，`test_semantic_chunk_candidates.py` 通过；`FTSIndex.search` 的权限过滤顺序与排序仍是独立问题（S05），不影响 F03 的结论。
- 上述 50 项定向回归全部通过：[输出](../docs/audit-evidence-2026-10-18/f01-f03-regressions.log)。

## 其他候选（未完成亲验，不计入确认数）

以下来自 14 个域的代理报告，主会话只做了初步代码核对或未复现。它们可能真实，也可能被未读到的下游保护消解；修复前应逐条复核：

| 候选 | 域 | 位置 | 备注 |
|---|---|---|---|
| Developer 结果预算截断跳过 out-of-workspace 写校验并报告成功 | orchestration | [developer_engine.py](../backend/app/orchestration/developer_engine.py) | 需构造 >80KB developer payload |
| 被拒绝的 developer 写运行仍执行 workspace 测试代码 | policy | [developer_engine.py:304-327](../backend/app/orchestration/developer_engine.py#L304-L327) | 需真实 developer 子进程 |
| UI 自动化超时/取消只取消协程，COM 线程继续写 | perception | [ui_automation_tools.py](../backend/app/tools/ui_automation_tools.py) | 需 Windows UIA 环境 |
| 屏幕监控在视觉调用之后才采样前台窗口，敏感窗口抑制可能用错窗口 | perception | [screen_monitor.py](../backend/app/perception/screen_monitor.py) | 需真实窗口切换 |
| `type_text` 审批预览只显示字符数，不显示实际输入 | perception | [ui_automation_tools.py:167-188](../backend/app/tools/ui_automation_tools.py#L167-L188) | 设计取舍还是缺陷需产品确认 |
| Developer 写守卫记录被拒绝路径但仍返回成功 | policy | [developer_write_guard.py](../backend/app/orchestration/developer_write_guard.py) | 需真实策略拒绝 |
| Office 公式注入 / 非 UTF-8 文本被覆盖损坏 | office | [document_intelligence_service.py](../backend/app/services/document_intelligence_service.py) | 需真实样例 |
| Indexer 进程级 httpx client 跨事件循环复用 | indexer/llm | [openai_compatible.py](../backend/app/llm/openai_compatible.py) | 需并发运行时复现 |
| Renderer 可通过 `desktopWebSocketOpen` 夺取 BrowserHost 控制 WebSocket | desktop-host | [desktopWebSocket.ts](../desktop/src/main/desktopWebSocket.ts) | 需安全模型确认 |
| 审批预览的 formaction/formmethod 与实际提交目标不一致 | desktop-host | [browserTargetSnapshot.ts](../desktop/src/main/browserTargetSnapshot.ts) | 需真实表单页面 |

## 验证结果

| 检查 | 结果 | 范围 / 证据 |
|---|---|---|
| 后端全量 pytest | **10 failed / 4,815 passed / 22 skipped**，949.31 秒 | 10 项均为 `example.com` 被本机 fake-IP DNS 解析到 `198.18.0.116`，SSRF 守卫按私有/保留地址拒绝；并非产品回归。[日志](../docs/audit-evidence-2026-10-18/backend-pytest.log) |
| 同 10 项隔离 DNS 后重跑 | 28 passed | 仅禁用 DNS 解析拦截，其他逻辑不变。[日志](../docs/audit-evidence-2026-10-18/privacy-tests-dns-isolated.log) |
| F01–F03 定向回归 | 50 passed | 隐私上下文、提交账本、分块候选。[日志](../docs/audit-evidence-2026-10-18/f01-f03-regressions.log) |
| 后端 Ruff | 通过 | `ruff check backend` 全树通过；另有 3 个新增 `scripts/*.py` 未格式化（CI 只检查 `backend/**`）。[日志](../docs/audit-evidence-2026-10-18/gates-ruff.log) |
| 桌面 typecheck / Vitest | 通过 | 107 个测试文件，486 passed / 1 skipped。[日志](../docs/audit-evidence-2026-10-18/desktop-test.log) |
| 桌面 renderer build / 预算 | 通过 | entry 207.7 KB，largest chunk 129.8 KB，总 JS 775.8 KB。[日志](../docs/audit-evidence-2026-10-18/desktop-build.log) |
| 移动端 typecheck | 通过 | `tsc --noEmit`。[日志](../docs/audit-evidence-2026-10-18/mobile-typecheck.log) |
| 源码规模门禁 | 通过 | 1,132 文件 / 269,887 行，P95 789 ≤ 800。[日志](../docs/audit-evidence-2026-10-18/gates-size.log) |
| 秘密扫描 | 通过 | 17.82 MB，无命中。[日志](../docs/audit-evidence-2026-10-18/gates-secrets.log) |
| 仓库卫生 | 通过 | 无跟踪的运行时产物。[日志](../docs/audit-evidence-2026-10-18/gates-hygiene.log) |
| 威胁模型结构 | 通过 | `security:threat-model` ok。[日志](../docs/audit-evidence-2026-10-18/gates-release.log) |
| 严格发布就绪 | **未通过** | 7 个 RR-P0 仍 `in_progress`；候选提交 `307c968e` 与 HEAD 不一致；工作树 dirty；负责人签名 pending。[日志](../docs/audit-evidence-2026-10-18/gates-release.log) |
| 全审查 scorecard | **未通过** | 94/100；因工作树 dirty 失败；7 个 P0 未完成。 |
| npm 依赖审计 | **未完成** | 前两轮 advisory endpoint 超时；本轮未重新跑完，JS 依赖漏洞状态仍未知。 |
| 桌面 smoke / 移动端 smoke | 本轮未运行 | 只跑了 typecheck、Vitest、renderer build；smoke 证据沿用前轮，不代表本轮。 |
| 真实模型 / 干净机器 / Android 真机 | 本轮未运行 | 发布证据仍需单独验收。 |

**关于 10 项后端失败的说明：** `example.com` 在当前机器被 DNS 解析到 `198.18.0.116`（TUN fake-IP 常见网段），`validate_outbound_http_url()` 将其判定为保留地址并拒绝；`test_planning_context_privacy.py` 和 `test_request_mode_routing.py` 在构造 cloud provider 时没有注入 resolver。产品已有 `LENGRVIS_TRUSTED_FAKE_IP_TUN` 显式开关，但测试没有设置。建议测试注入 `resolver` 或设置该开关，否则这些隐私回归在 fake-IP 开发机上会误报。

## 发布阻断与依赖审计缺口

`npm run release:readiness:strict` 返回非零，7 个 stop-ship 条目仍为 `in_progress`：

1. Windows 干净机器安装、启动、任务、诊断与卸载验收。
2. 本地模型安装、启动、拉取及隐私任务验收。
3. Android 安装包与真机/模拟器 LAN HTTPS/WSS 链路验收。
4. 至少 30 个真实自然语言任务的结果质量评审。
5. 实际诊断包的人工外发审查及签署。
6. Public Beta/RC 候选版本、门禁和发布负责人签署。
7. 与候选版本绑定的威胁模型评审与剩余风险接受。

这些是缺失的发布证据，不是本轮新增的 7 个 P0 代码漏洞。看板候选仍绑定 `307c968e421131fa7ce62afdadef404ff02e94a6`，与当前 HEAD 不同；CI 状态、干净工作树、负责人签名也未满足。npm 审计未完成，JS 依赖漏洞状态仍未知；不要用本轮通过的门禁替代依赖审计。

## 模块判断

- **backend（核心资产）**：审批、文件沙箱、SSRF、隐私记忆的基础设计仍然扎实；本轮问题集中在“生命周期边界”（摘要、删除、保留清理、审批过期）和“跨模式/跨权限动作映射”，不是基础原语失效。
- **desktop（主 UI/浏览器宿主）**：类型、单测、构建通过；渲染包预算正常。但 BrowserHost 的终态清理、提交守卫覆盖和 IPC 冒烟需要补强。
- **mobile（可选 companion）**：typecheck 通过；配对存储的生物识别取消路径会造成静默解配，属于用户可感知的高影响问题。
- **tests/CI**：测试数量充足，但存在 DNS 外部依赖、IPC 新通道未进 smoke、发布签名门禁只读文本等“证据有效性”问题。测试通过不等于发布就绪。

## 材料

- 本报告：[project-full-review-2026-10-18.md](project-full-review-2026-10-18.md)
- 机器摘要：[verification-summary.json](audit-evidence-2026-10-18/verification-summary.json)
- 复现脚本与日志：[audit-evidence-2026-10-18/](audit-evidence-2026-10-18/)
- 复现均在临时 `LENGRVIS_DATA_DIR` 下执行；云端发送用 fake provider 截获，未真实上传隐私内容；浏览器外部执行未真实提交表单。

> 结论：本轮不是“发布已就绪”。建议先修复 F01–F04，再处理 F05–F14，最后补 S 系列与测试有效性缺口；完成后再跑一遍全量门禁和发布验收。

## 2026-10-19 修复与验证

本次处理以上 25 个确认条目；“其他候选”未作为已确认缺陷合并修复。

| 条目 | 已实施的修复 |
|---|---|
| F01 | 摘要增加绑定会话、内容和云端使用权限的 HMAC；隐私及旧版无来源摘要保持本地，合并后继承更严格的策略。规划、请求投影、摘要注入及持久化压缩边界过滤未获准摘要。原文“下一次摘要覆盖”应理解为持续合并，不能据此推断旧内容自然消失。 |
| F02 | OCR、视觉、图片比较、图片 embedding 及标签文本回退统一检查上传许可；显式 fallback 不能绕过开关；cloud provider 在编码发送前再次检查，本地 provider 保留本地路径。 |
| F03、F05、F11 | 含设置删除在同一事务中重建并签名安全默认权限策略；保留清理同步删除 presence；隐私删除清空 FTS 并校验。启动时仍拒绝未经授权的敏感记录缺失，不自动把完整性失败当作合法重置。 |
| F04 | 主动事项按工具风险及实际浏览器动作检查 file_modify、ui_edit、browser_prepare；执行和浏览器命令签发都复核权限，observe 不再放行写入。 |
| F06 | 结构化答复字段名、依赖候选 ID 和版本映射作为协议控制字段保留。 |
| F07、S10 | 过期审批结束等待中的步骤、运行和任务；引擎异常、轮次耗尽同步结束 Task；用户暂停、取消状态仍受到保护。过期操作须发起新任务取得新审批。 |
| F08 | 已经运行时审查允许的明确失败允许重新规划；失败不计入成功证据，outcome_unknown 和未完成审查仍阻止重放。 |
| F09、S08 | 提交识别统一 action kind 规范化；业务 URL 规范化主机、默认端口及点路径。提交摘要升级为 v2，已签名 v1 记录继续通过规范化和保守旧账本匹配防止重复提交。 |
| F10 | 后端读取及 Electron DOM 观察增加 SSN、PIN、TOTP、MFA、信用卡 autocomplete 等敏感字段识别，同时覆盖 selector、selectors 和控件列表。 |
| F12、F13 | DOCX 脱敏遍历正文、页眉、页脚和文本框 XML 段落，支持跨 run 匹配；xlsm/xltm 编辑启用 keep_vba。 |
| F14 | SecureStore 解锁错误向界面返回可重试错误，保留令牌和证书信任；本次验证为模拟取消认证，未声称真机生物识别验收。 |
| S01、S02、S03 | Guardian 调度及创建接口传递时区；后端接受当前截图目录格式，adapter 传递截图产物；终态事项的 pause/cancel 保持幂等。 |
| S04 | 托管浏览器拒绝用 click 执行原生表单提交，要求明确 submit；未经提交许可的写 HTTP 请求被拦截，用户手动接管仍可操作。 |
| S05、S06 | FTS 与 LIKE 回退在 SQL LIMIT 前执行权限过滤，FTS 使用 BM25 排序；UIA 路由剔除 action/kind/tool 调度键后再执行严格 schema 校验。 |
| S07、S09 | Butler 全部 IPC 通道加入安全冒烟及 handler 回归；共享 Session 只保留一个下载监听，最后一个标签关闭时移除监听及路由。 |
| S11 | 严格门禁读取签名产物并用配置的可信 Ed25519 公钥独立验签，核对候选提交、运行、版本、负责人及摘要；CI 配置公钥及已有锁文件中的验证依赖，文本 verified 声明不能代替验签。 |

验证使用临时数据目录、模拟 provider、合成 Office 文件及本地运行时，不操作用户真实数据，不上传文件或提交外部表单：

- 后端相关 27 个测试文件：**520 passed**，114.10 秒；随后提交摘要版本兼容和图片比较错误返回调整：**128 passed**，19.09 秒。
- provider 视觉边界：**11 passed / 4 skipped**；跳过项均为当前 Windows 会话没有创建符号链接权限。
- 隐私任务完成 → 持久化 → 重载 → efficiency 投影链路单独复测通过。
- 后端全树 Ruff、修改的发布脚本 Ruff、格式检查及 `git diff --check` 通过。
- 桌面全套：**109 个测试文件，494 passed / 1 skipped**；类型检查、Electron 构建、IPC 安全冒烟、bundled Ollama 环境冒烟、renderer 构建与包体预算通过。
- 移动端 typecheck、共享会话管理竞争测试及新增取消解锁测试通过。
- 发布工作流 YAML、内嵌 Python 语法与锁定依赖引用检查通过；未执行远端 CI。

未重跑整个后端、全部 smoke、真实模型任务、干净机器或 Android 真机验收。VBA 保留使用合成二进制 part 验证，尚无真实签章宏工作簿的 Office 验收；浏览器提交保护使用本地测试验证，尚无外部业务网站端到端验收。原有 7 个 RR-P0 发布证据缺口、真实负责人签署及依赖审计缺口仍需单独完成，不能由本次代码修复或通过的测试替代。
