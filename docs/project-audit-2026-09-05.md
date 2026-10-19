**项目真实问题审查 · 2026-09-05**

审查基线：`00500c40`，Windows / Python 3.12.10 / Node 24.11.1。范围覆盖 backend、desktop、mobile、依赖与 CI 脚本。本次确认 10 项问题：5 项 P1、5 项 P2；未确认 P0。P1 表示应优先修复的隐私、授权或数据删除问题，P2 表示影响正常功能或交付的缺陷。

结论：现有自动化测试大部分通过，但没有覆盖几条关键的跨模块约束：任务隐私模式是否传到工具和模型、撤销授权是否停止后台读取、数据删除是否覆盖内存及磁盘副本。建议在处理真实敏感文件或对外发布前先修复 F1–F5。

下列问题均有源代码调用链及可执行复现或命令输出。网络相关复现使用合成文本，在云端 HTTP 发送函数处截获请求，未向外部发送测试文件，也没有据此断言实际用户数据已经泄露。产品源码和原有审查文档未修改。

| 编号 | 优先级 | 确认的问题 | 用户可见影响 |
| --- | --- | --- | --- |
| F1 | P1 | 索引向量化绕过文件上传开关 | 关闭上传后，文件文本仍进入云端请求 |
| F2 | P1 | 单任务隐私模式在工具及模型解析处丢失 | 隐私任务可能使用全局云端模型或网络权限 |
| F3 | P1 | 撤销目录授权未更新后台监听 | 撤销后新建文件仍被读取和索引 |
| F4 | P1 | 删除数据未清空会话内存 | 已删除内容仍可进入规划，关闭程序时写回数据库 |
| F5 | P1 | 删除数据遗漏工具结果文件和备份 | 本地明文内容副本仍存在 |
| F6 | P2 | 定时任务忽略真实执行状态 | 失败、拒绝和等待审批都可能显示“完成” |
| F7 | P2 | 定时任务删除接口实际只暂停 | 删除后列表仍保留，且可重新启用 |
| F8 | P2 | Cron 隐式使用 UTC，界面未说明 | 台北“每天 9 点”实际在 17 点执行 |
| F9 | P2 | Git 改动范围越出授权子目录 | 无写入任务被判为有改动，并带出兄弟文件名 |
| F10 | P2 | 锁定的 Browserslist 命中 high 审计项 | 当前 CI 的依赖安全门禁无法通过 |

**F1 · P1：索引向量化没有遵守文件内容上传开关**

位置：[embedding_service.py:38](D:/Desktop/mavris/backend/app/indexer/embedding_service.py:38)、[fts_index.py:223](D:/Desktop/mavris/backend/app/indexer/fts_index.py:223)、[fts_index.py:428](D:/Desktop/mavris/backend/app/indexer/fts_index.py:428)。

当全局为 efficiency、配置了云端模型且本地 ONNX embedding 不可用时，索引把完整文本分块传给 `get_provider(settings, task="embed").embed(...)`。这里没有检查 `can_upload_file_content(settings)`；即使 `allow_file_content_upload=false`、`allow_cloud_context=false`，仍会选中云端 embedding。配置的本地 embedding 失败后的回退路径也经过这里。

复现通过真实 `FTSIndex.rebuild()` 索引一个合成文本文件，在 `OpenAICompatibleProvider._post_json` 边界观察到 1 次请求，`input` 含原文件标记；此时上传授权判断为 false。与产品 [privacy.py:14](D:/Desktop/mavris/backend/app/policy/privacy.py:14) 的规则不符。

建议在向量化入口保留内容来源并实施上传策略；不允许上传时使用本地 embedding 或已有 hashing 回退。验收应同时覆盖全量索引、增量索引、本地 embedding 失败，以及搜索查询/记忆文本的来源约束，断言云端发送函数未收到受限制内容。

证据：`index_ignores_file_upload_consent`。

**F2 · P1：单任务隐私模式没有贯穿执行链**

位置：[step_execution_handler.py:68](D:/Desktop/mavris/backend/app/orchestration/handlers/step_execution_handler.py:68)、[orchestrator_agent.py:213](D:/Desktop/mavris/backend/app/agents/orchestrator_agent.py:213)、[document_tools.py:67](D:/Desktop/mavris/backend/app/tools/document_tools.py:67)、[document_service.py:59](D:/Desktop/mavris/backend/app/services/document_service.py:59)。

界面允许单独为定时任务选择隐私模式。规划阶段会使用任务模式，但 `_runtime_context()` 把全局设置原样交给 `TaskRuntimeContext.from_task()`，没有应用 `task.mode`。全局效率模式并允许联网时，一个 `Task(mode="privacy")` 的工具上下文仍是 efficiency，浏览器及文件上传策略判断均为允许。

文档工具还有第二处设置丢失：即便调用者正确传入 privacy 设置，`_document_provider()` 因“模式为本地”直接放行 `_provider`；后者却调用不带设置参数的 `get_provider()`，重新选择全局模型。因此只修运行上下文还不足以修复文档工具。

已做两次独立验证：一是任务上下文的实际策略判断；二是直接传入 privacy 且禁止上传的上下文，调用真实文档摘要工具。在全局 efficiency 下，合成文档正文仍进入 1 次云端 chat 请求。后一验证仅替换网络发送函数，文档读取、模型选择和摘要服务均执行产品代码。

建议构造任务专属设置副本，把模式与授权一致传给策略、所有工具及 provider resolver。文档 resolver 必须使用调用上下文中的设置选择模型，并验证实际 provider 的云端能力。验收覆盖“全局效率＋任务隐私”以及“正确 privacy 上下文＋全局云端配置”两条路径。

证据：`privacy_task_uses_global_tool_mode`、`document_privacy_context_resolves_global_cloud`。

**F3 · P1：撤销授权目录后监听仍继续读取**

位置：[file_watcher.py:159](D:/Desktop/mavris/backend/app/indexer/file_watcher.py:159)、[file_watcher.py:225](D:/Desktop/mavris/backend/app/indexer/file_watcher.py:225)、[settings_service.py:69](D:/Desktop/mavris/backend/app/services/settings_service.py:69)。

监听器启动时保存一份 `allowed_directories`，事件处理和实际索引都继续使用该副本。设置更新只修改数据库，没有重新配置监听器；消费队列时也不重新读取当前授权。

复现使用真实 Watchdog 监听合成目录，通过真实设置确认 nonce 将目录列表改为空，再在已撤销目录内新建文件。返回设置已是 `[]`，但新文件仍进入 `indexed_files`。在 F1 的云端配置条件下，该文件文本还进入 embedding 请求。未授权读取本身独立于 F1 存在。

建议设置变更后同步重建监听范围，并在实际读文件前重新验证最新授权、丢弃撤销范围中的排队事件。验收必须在服务不重启的情况下验证撤销后新建/修改文件均不再被读取，并验证新增授权目录开始监听。

证据：`revoked_directory_still_indexed`。

**F4 · P1：删除本机数据后会话内容仍在内存，并可重新落盘**

位置：[routes_system.py:102](D:/Desktop/mavris/backend/app/api/routes_system.py:102)、[session_context.py:241](D:/Desktop/mavris/backend/app/core/session_context.py:241)、[lifespan.py:46](D:/Desktop/mavris/backend/app/lifespan.py:46)。

删除接口清空数据库，却没有清空全局 `SessionContextStore.current`。规划继续可以读取其中的 notes 等内容；正常关闭时 lifespan 还会调用之前登记的 `session_store.save`。`save()` 发现对应记录不存在时允许重新插入，所以数据库删除结果可以被旧内存覆盖。

复现使用产品的全局会话 store 保存合成私密标记，再调用真实删除接口（仅模拟原生确认已通过）。接口返回 `ok=true`，数据库会话行数为 0，但 `planning_context()` 仍含标记。随后调用正常关闭时使用的同一 `save()`，标记重新出现在数据库中。

建议删除操作先阻止相关运行继续写入，再就地清空存活 store 或使旧 store 失效，避免只重置单例变量却留下已注册的保存回调。增加数据代次/删除标记以拒绝旧任务写回。验收覆盖删除后继续对话、保存回调和重启后的内容。

证据：`erase_keeps_runtime_and_disk_data` 中 `erased_note_available_to_planner=true`、`erased_note_restored_on_shutdown=true`。这里模拟了关闭保存回调，没有启动并退出完整 Electron 应用。

**F5 · P1：删除本机数据遗漏磁盘上的工具结果和文件备份**

位置：[routes_system.py:91](D:/Desktop/mavris/backend/app/api/routes_system.py:91)、[result_budget.py:78](D:/Desktop/mavris/backend/app/orchestration/result_budget.py:78)、[managed_backups.py:34](D:/Desktop/mavris/backend/app/tools/managed_backups.py:34)。

删除接口只清理 SQLite 内容和诊断包 JSON。超长工具结果由 `persist_large_result()` 写入应用数据目录的 `tasks/<task-id>/tool-results/`，文件工具回滚副本由 `create_managed_backup()` 写入 `file-tool-backups/`。这两类由程序产生的明文内容副本都不在删除范围内，也没有被响应列为保留项或待手动清理项。

复现通过这两个真实 helper 创建合成内容，删除接口成功后，两份文件仍能读到原文。与 F4 不同，这些内容在删除返回时就已经残留于磁盘，不依赖后续保存或退出。

建议建立应用生成的个人数据文件清单，在同一删除流程安全清理并逐项验证。只处理应用管理的副本；清理失败时应返回残留信息。验收覆盖工具大结果、备份及删除失败，确保不会误删用户原文件。现有代码已将任务录屏迁入加密数据库，本次没有把录屏错误地归入磁盘残留。

证据：`erase_keeps_runtime_and_disk_data` 中 `tool_artifact_readable=true`、`managed_backup_readable=true`。

**F6 · P2：定时任务把失败、拒绝、待审批都标记为完成**

位置：[scheduler_service.py:198](D:/Desktop/mavris/backend/app/services/scheduler_service.py:198)、[scheduler_service.py:238](D:/Desktop/mavris/backend/app/services/scheduler_service.py:238)。

`_execute()` 默认 `last_status="completed"`；从 `handle_user_goal()` 返回后只读取 `task.id`，忽略真实任务阶段和执行阶段。编排器通过返回 Task 表示失败、拒绝或待审批，这些情况不一定抛异常，所以最终被持久化为 completed，并发出“定时任务完成”通知。

复现分别让编排入口返回 FAILED、DENIED、WAITING_USER_APPROVAL 的真实 Task 对象，运行实际调度器。三种情况全部记录 completed，并尝试发送完成通知。只替换编排返回值和通知发送，未向用户设备发送测试通知。

建议保存并传播任务状态；等待审批应保持待处理，只有终态成功才发完成通知，后续审批和执行结果应回写关联定时运行记录。验收覆盖成功、业务失败、拒绝、待审批和执行异常。

证据：三条 `scheduler_false_completed`。

**F7 · P2：点击“删除定时任务”实际只是暂停**

位置：[routes_schedules.py:52](D:/Desktop/mavris/backend/app/api/routes_schedules.py:52)、[scheduler_service.py:130](D:/Desktop/mavris/backend/app/services/scheduler_service.py:130)、[SchedulePanel.tsx:52](D:/Desktop/mavris/desktop/src/renderer/components/SchedulePanel.tsx:52)。

DELETE 接口调用 `cancel()`，其实现仅将 `enabled` 设为 false。`list()` 不排除这些条目，UI 删除成功后重新读取列表，所以同一任务还在，并能再次启用。界面已经另设“暂停”按钮，这使“删除”功能没有实现所标示的行为。

复现创建定时任务，调用实际删除路由后返回 `ok=true`；列表仍包含该 id，`enable(id, true)` 仍成功。

建议明确删除与暂停的存储语义，可物理删除或使用独立删除标记；列表与启用接口必须遵守删除状态，必要审计记录单独保留。验收覆盖删除后刷新列表、再次启用及执行中的删除。

证据：`schedule_delete_retains_item`。

**F8 · P2：定时周期采用 UTC，但界面没有时区约定**

位置：[scheduler_service.py:37](D:/Desktop/mavris/backend/app/services/scheduler_service.py:37)、[schemas.py:674](D:/Desktop/mavris/backend/app/core/schemas.py:674)、[SchedulePanel.tsx:76](D:/Desktop/mavris/desktop/src/renderer/components/SchedulePanel.tsx:76)。

Cron 以 `_utc_now()` 为基准计算，模型没有保存时区；界面只解释 Cron 表达式，还给出“每天 9 点”的目标示例，却未说明使用 UTC。结果显示又使用本地 `toLocaleString()`。

固定时间复现：台北时间 2026-09-05 08:30 创建 `0 9 * * *`，计算出的下次执行是当天 17:00，而非本地 09:00。UTC 存储本身没有问题，缺陷在于输入时区未定义且不可选择。

建议保存 IANA 时区并在该时区计算 Cron，再转 UTC 存储；若产品明确只支持 UTC，则在输入处直接标明并展示本地换算。验收覆盖 UTC+8 与夏令时地区的跨日调度。

证据：`schedule_timezone`。

**F9 · P2：Git 验证把授权子目录外的改动算进当前任务**

位置：[developer_write_guard.py:78](D:/Desktop/mavris/backend/app/orchestration/developer_write_guard.py:78)、[developer_write_guard.py:107](D:/Desktop/mavris/backend/app/orchestration/developer_write_guard.py:107)、[developer_write_guard.py:194](D:/Desktop/mavris/backend/app/orchestration/developer_write_guard.py:194)。

从授权子目录执行无 pathspec 的 `git status --porcelain=v1`，Git 会查找父级仓库并返回全仓库改动；porcelain 路径相对仓库根目录。代码却用 `授权子目录 / 返回路径` 做验证，并直接把这些条目用于 `writes_detected` 与后续测试命令推断。

复现建立独立临时 Git 仓库，只授权一个空子目录，在父级创建未跟踪兄弟文件。子目录没有文件且只有 Read 工具事件，结果仍 `writes_detected=true`，summary 包含外部兄弟文件名，而且该条目没有授权错误标记。没有观察到此路径读取兄弟文件正文，因此不将它描述为任意文件内容泄露。

建议先解析并验证 Git 顶层目录，明确限制到授权 pathspec，并正确转换 porcelain 路径；改动判断还应区分任务开始前已有改动和本次任务写入。验收覆盖授权仓库根、授权仓库子目录、父仓库中的非 Git 子目录及原有未提交改动。

证据：`git_subdirectory_includes_unauthorized_siblings`。这一问题也解释了全量测试使用仓库内临时目录时的部分失败；仓库外复跑通过不代表该子目录产品场景已修复。

**F10 · P2：桌面依赖锁使 high 级审计门禁失败**

位置：[desktop/package-lock.json:2545](D:/Desktop/mavris/desktop/package-lock.json:2545)、[ci.yml:180](D:/Desktop/mavris/.github/workflows/ci.yml:180)。

锁文件和实际安装树均为 Browserslist 4.28.2，来自 Vite/Babel 开发依赖链。`npm --prefix desktop audit --json` 当前返回 1 个 high、1 个 moderate 受影响包；high 为 Browserslist。CI 明确执行 `npm --prefix desktop audit --audit-level=high`，因此按当前公告会阻止该门禁通过。

维护者公告确认受影响版本为 <=4.28.6，修复版为 4.28.7，涉及自定义统计数据处理及缓存增长问题。npm 对缓存问题标 high，维护者页面标 moderate；另一个统计处理公告自身标 high，因此不影响门禁判断。依据：[统计处理公告](https://github.com/browserslist/browserslist/security/advisories/GHSA-73wf-gq98-2v4g)、[缓存公告](https://github.com/browserslist/browserslist/security/advisories/GHSA-c83g-rgw3-j3cx)。

建议将兼容的传递依赖更新到包含修复的版本并更新锁文件，再执行审计和桌面构建。本项确认的是依赖与交付问题；该包为 dev dependency，没有把公告分数直接当作 Electron 用户端可远程利用漏洞。

证据：[desktop-audit.json](D:/Desktop/mavris/.tmp/audit-20260905/desktop-audit.json)。

**执行过的验证与结果**

| 验证 | 结果及范围 |
| --- | --- |
| 后端全量 pytest | 4,246 passed / 9 failed / 17 skipped，633.45 秒，共 4,272 项 |
| 初次失败逐项复跑 | 使用仓库外的独立临时目录，9 passed，12.72 秒；没有修改产品源码 |
| 桌面 TypeScript | 通过 |
| 桌面 Vitest | 98 个测试文件通过，432 passed / 1 skipped |
| 桌面构建 | renderer、Electron 构建及法务资源验证通过，随 smoke 执行 |
| 桌面行为 smoke | 18 个顶层子项均获得通过结果；其中 browser-activity 首次卡住后，在临时诊断副本中加 10 秒操作超时后完整通过 |
| 移动端 TypeScript | 通过 |
| 移动端行为 smoke | 13 项通过，涵盖 token、consent、会话、推送、审批标签、任务伴随、远程输入授权、wakeup、Android 返回及加固、EAS CLI |
| 后端定向 Ruff | F821/F822/F823/B023/B006/B019/S307/S506/S608 检查通过；不代表全规则 lint 已通过 |
| 后端依赖审计 | 对锁定清单执行 pip-audit，未发现已知漏洞 |
| 桌面依赖审计 | 2 个受影响包：Browserslist high、@xmldom/xmldom moderate |
| 移动端依赖审计 | 3 个 moderate 包节点，来自同一 decode-uri-component 传递依赖问题 |
| 根目录依赖审计 | qs 命中 moderate；属于协议测试工具的开发依赖 |
| MCP 官方 conformance | initialize、tools_call、sse-retry 三场景通过，共 5 项检查 |
| 依赖锁校验 / 仓库卫生 / 威胁模型检查器 | 均通过；检查器通过不等同于运行时安全保证 |
| Gitleaks 8.30.1 | 使用严格配置、空忽略列表，对当前 1,608 个 tracked 文件快照扫描，未发现命中；未扫描 Git 历史 |
| 独立缺陷复现 | 11 条结果记录，覆盖 F1–F9；F2、F6 各包含多种条件，F4/F5 共用一次删除流程 |

初次后端失败的 7 项涉及发布证据辅助脚本：测试在仓库内创建临时文件时，输出采用仓库相对路径，而断言假定只返回文件名。另 2 项涉及 Git 对父仓库的发现，已用独立临时仓库验证其中的真实边界缺陷并列为 F9。这里没有声称同一次全量运行全部通过，也没有将所有首次失败直接算成 9 个产品缺陷。

桌面完整 `npm run smoke` 的首次运行被手动停止在 browser-activity；后续通过来自等效临时诊断副本与剩余两个独立 smoke。卡住没有稳定复现，未单列为确定缺陷。签名 smoke 验证的是脚本行为，并非本次实际发行包已经签名。

补充依赖信号：mobile 的 expo-router → query-string → decode-uri-component 命中畸形百分号编码输入的耗时问题，3 个节点不代表 3 个独立漏洞；尚未在 Android 深链入口复现实机影响，未把它计入 10 项已验证产品问题。修复时应选择与当前 Expo 版本兼容的更新，不能直接照抄 npm 给出的 expo-router 大版本回退建议。依据：[维护者公告](https://github.com/SamVerschueren/decode-uri-component/security/advisories/GHSA-vcc3-ghjq-m6fr)。

**复现材料与验证边界**

统一复现脚本：[reproduce_findings.py](D:/Desktop/mavris/.tmp/audit-20260905/reproduce_findings.py)。运行命令：

```powershell
& 'D:/Desktop/mavris/.venv/Scripts/python.exe' 'D:/Desktop/mavris/.tmp/audit-20260905/reproduce_findings.py'
```

脚本在子进程内使用独立环境和临时数据目录、合成文件、测试用密钥及网络拦截；输出观察结果，不会修复源码。结果：[probe-results.json](D:/Desktop/mavris/.tmp/audit-20260905/probe-results.json)。

主要日志：[后端全量](D:/Desktop/mavris/.tmp/audit-20260905/backend-tests.log)、[失败复跑](D:/Desktop/mavris/.tmp/audit-20260905/backend-retry.log)、[桌面单测](D:/Desktop/mavris/.tmp/audit-20260905/desktop-tests.log)、[桌面初次 smoke](D:/Desktop/mavris/.tmp/audit-20260905/desktop-smoke.log)、[浏览器诊断复跑](D:/Desktop/mavris/.tmp/audit-20260905/bounded-browser-smoke.log)、[移动 smoke](D:/Desktop/mavris/.tmp/audit-20260905/mobile-smoke.log)、[MCP](D:/Desktop/mavris/.tmp/audit-20260905/mcp-conformance.log)、[密钥扫描](D:/Desktop/mavris/.tmp/audit-20260905/secret-scan.log)。

本次没有完成真实外部 LLM 任务质量评测、Android 实机/远程输入联调、Windows 干净机器安装、实际发布包签名和发行验证；没有执行 Linux/macOS 全套运行验证。17 个后端跳过项包含当前 Windows 会话无法创建的符号链接和 POSIX 专属场景。源码审查与合成复现不能替代这些环节，也不构成“所有文件每一行均无问题”的保证。

建议修复顺序：先处理 F1/F2 的隐私边界，再处理 F3–F5 的授权及删除生命周期；随后修复 F6–F9 的功能行为，并更新 F10 所述锁文件。回归检查应验证跨模块的最终行为：不能只断言设置开关已保存、数据库行数已清零，或调度函数没有抛异常。
