**审查问题修复结果 · 2026-09-07（2026-09-09 更新）**

对应 [原始审查](D:/Desktop/mavris/docs/project-audit-2026-09-05.md) 的 F1–F10，按 [六步修复计划](D:/Desktop/mavris/docs/project-audit-fix-plan-2026-09-05.md) 实施。基线 `00500c40`，分支 `codex/audit-fixes-20260905`。十项修复及最终源码复核的补充问题均已完成，最终回归通过。

| 问题 | 修复后的行为 | 回归证据 |
| --- | --- | --- |
| F1 · 上传控制 | 禁止文件上传时，索引、记忆和查询向量化不会请求云端；本地模型失败后使用本地散列向量。允许上传时保留云端能力。 | [隐私测试](D:/Desktop/mavris/backend/tests/test_audit_privacy_regressions.py:63) |
| F2 · 任务隐私 | 任务模式传入工具运行、步骤调度、OS 引擎、记忆与语义查询；文档模型按照该任务的设置解析，并检查实际 provider 的云端能力。 | [任务与文档测试](D:/Desktop/mavris/backend/tests/test_audit_privacy_regressions.py:90) |
| F3 · 授权撤销 | 设置更新会重新配置监听目录；排队事件在消费和实际读取时重新校验权限。新增授权也能立即监听。 | [文件监听测试](D:/Desktop/mavris/backend/tests/test_file_watcher.py) |
| F4 · 会话删除 | 清空所有存活会话对象，旧快照拒绝写回；消息队列及失败重试绑定删除代次，过期内容无法在读取或关闭应用时恢复。不同数据库相互隔离。 | [旧会话测试](D:/Desktop/mavris/backend/tests/test_privacy_erase.py:36)、[消息队列测试](D:/Desktop/mavris/backend/tests/test_agent_bus_erase.py) |
| F5 · 磁盘副本删除 | 清理诊断包、工具结果和应用备份。写请求、运行查询中的状态回写、任务、索引及消息落盘与删除互斥；清理不完整时明确返回失败并留下审计记录。 | [文件、占用、查询与链接测试](D:/Desktop/mavris/backend/tests/test_privacy_erase.py:58) |
| F6 · 调度结果 | 根据 Task 的真实阶段记录结果；后续审批完成由定时扫描更新。暂停再启用不会丢失原执行结果，旧执行也不能覆盖新执行；完成通知只对应成功结果。 | [调度状态测试](D:/Desktop/mavris/backend/tests/test_audit_scheduler_regressions.py) |
| F7 · 调度删除 | DELETE 删除持久化记录；暂停仍独立使用 enable 接口。执行中的任务结束也不能恢复已删除的调度记录。 | [删除 API 测试](D:/Desktop/mavris/backend/tests/test_audit_scheduler_regressions.py:84) |
| F8 · 时区 | 桌面新建调度保存 IANA 时区，在确认窗口和列表中展示，并按该时区计算 Cron。旧任务继续按 UTC 执行。 | [API、历史数据与夏令时测试](D:/Desktop/mavris/backend/tests/test_audit_scheduler_regressions.py)、[桌面组件测试](D:/Desktop/mavris/desktop/src/renderer/components/SchedulePanel.test.ts) |
| F9 · Git 范围 | Git 状态和默认 diff 限定到工作目录；NUL 路径正确处理中文、空格与重命名。拒绝特殊 pathspec，历史与对象查询校验整个仓库的授权，避免读取未授权兄弟目录内容。 | [状态与路径解析测试](D:/Desktop/mavris/backend/tests/test_developer_write_guard.py:108)、[Git 越界测试](D:/Desktop/mavris/backend/tests/test_developer_git_scope.py) |
| F10 · 依赖门禁 | 定点升级 Browserslist、@xmldom/xmldom、js-yaml、Joi、Vitest 及必要依赖；2026-09-09 安装树和锁文件审计均为零漏洞。 | [锁文件](D:/Desktop/mavris/desktop/package-lock.json)、[最新安装树审计](D:/Desktop/mavris/.tmp/audit-fixes/desktop-audit-reviewed-after.json)、[锁文件审计](D:/Desktop/mavris/.tmp/audit-fixes/desktop-audit-reviewed-lock.json) |

删除本地数据时，如任务或索引仍在运行，接口返回 `409 local_data_busy`，需要先停止相关工作；文件清理不完整返回 `500 local_data_cleanup_incomplete`，可在关闭占用程序、检查目录后重试。原文件、审计链以及默认保留的设置继续按既有删除范围处理。

只授权 Git 子目录时，仍可查看该目录的工作区状态及 `git diff -- path`。`git log`、`git show` 和包含对象/版本参数的 diff 需要授权仓库根目录，因为 Git 对象读取不受普通文件路径限制。特殊 pathspec 会被明确拒绝。

| 验证 | 结果 |
| --- | --- |
| 后端最终全量 pytest | 4327 项通过、17 项环境跳过，0 失败、0 错误；659.64 秒，共 4344 项 |
| 删除功能补充边界测试 | 18 项通过，包括运行查询竞态与 Windows 目录联接保护 |
| 消息队列删除与隔离 | 5 项通过，涵盖排队、在途写入、失败重试、队列溢出和跨数据库隔离 |
| 调度补充回归 | 31 项通过，包括暂停再启用和旧执行覆盖保护 |
| Git 补充回归 | 120 项通过、1 项环境跳过，包括真实 Git 特殊 pathspec 与对象读取 |
| 桌面 Vitest | 98 个文件通过，433 项通过、1 项跳过 |
| 桌面完整 build | TypeScript、renderer、Electron、preload 与法务资源验证通过 |
| 桌面定向 smoke | 诊断页面、preload 参数过滤、IPC 安全、desktop token 生命周期、source-map 策略共 5 项通过 |
| 改动 Python Ruff / diff 空白检查 | 通过 |
| 依赖锁 / 仓库卫生 | `npm run deps:verify` 通过；桌面 `npm ci --dry-run --ignore-scripts --engine-strict` 通过 |
| 桌面 npm audit | 安装树及锁文件审计均为零漏洞，high 门禁通过 |

各缺陷先用测试重现，再修改实现。上一轮回归中发现四个并发测试替身未提供真实 `_tool_context()` 已有的 `settings` 字段；补全该契约后，两个测试文件共 20 项通过，原有隔离、审批和取消断言保留。此前的文档 provider、路由与向量化替身也补全了参数及本地 provider 身份。最终源码复核新增 38 项回归，其中运行查询、消息队列、调度与 Git 的原实现分别出现 4、4、3、23 项失败，补充修复后定向测试全部通过。

依赖声明范围保持兼容，最终变更 18 个锁文件节点。主要版本为 Browserslist 4.28.9、@xmldom/xmldom 0.8.15、js-yaml 4.3.2、Joi 18.2.8、Vitest/@vitest 4.1.11。Browserslist 修复依据维护者的 [安全公告](https://github.com/browserslist/browserslist/security/advisories/GHSA-73wf-gq98-2v4g) 与 [发布记录](https://github.com/browserslist/browserslist/releases/tag/4.28.9)。

2026-09-09 重新获取审计数据时，上一轮零漏洞的桌面依赖出现 1 项高危、2 项中危和 1 项低危报告（[升级前快照](D:/Desktop/mavris/.tmp/audit-fixes/desktop-audit-reviewed.json)）。按维护者的 [js-yaml 公告](https://github.com/nodeca/js-yaml/security/advisories/GHSA-2883-xcg3-v3hh)、[Vitest 公告](https://github.com/vitest-dev/vitest/security/advisories/GHSA-82fw-gwwq-j7x9)、[Joi 公告](https://github.com/hapijs/joi/security/advisories/GHSA-6w3j-5fw6-r9vr) 补充升级后，重新运行桌面 433 项测试、完整构建和依赖锁校验均通过，最新审计恢复零漏洞。

零漏洞结论只对应本次桌面依赖审计；原审查中 mobile 的 decode-uri-component 和根目录 qs 中等级别信号尚未在本轮升级，需要分别处理 Expo 兼容性和开发工具依赖。

验证使用仓库外临时数据库、合成文件与测试密钥；云端请求在 HTTP 边界拦截。真实 LLM 任务质量、Android 真机、干净机器安装及实际发行包签名仍需专门验证。代码留在本地修复分支，尚未提交或发布。

主要日志：[后端最终全量](D:/Desktop/mavris/.tmp/audit-fixes/backend-reviewed-full.log)、[JUnit 结果](D:/Desktop/mavris/.tmp/audit-fixes/backend-reviewed-full.xml)、[后端首次回归](D:/Desktop/mavris/.tmp/audit-fixes/backend-full.log)、[并发测试复跑](D:/Desktop/mavris/.tmp/audit-fixes/parallel-green.log)、[删除查询边界](D:/Desktop/mavris/.tmp/audit-fixes/erase-query-green.log)、[消息队列隔离](D:/Desktop/mavris/.tmp/audit-fixes/erase-agent-bus-isolation.log)、[调度结果](D:/Desktop/mavris/.tmp/audit-fixes/schedule-reenable-green.log)、[Git 授权](D:/Desktop/mavris/.tmp/audit-fixes/git-pathspec-green.log)、[最新桌面测试](D:/Desktop/mavris/.tmp/audit-fixes/desktop-reviewed-tests.log)、[最新完整构建](D:/Desktop/mavris/.tmp/audit-fixes/desktop-reviewed-build.log)、[最新冒烟检查](D:/Desktop/mavris/.tmp/audit-fixes/desktop-reviewed-smokes.log)、[依赖锁验证](D:/Desktop/mavris/.tmp/audit-fixes/dependency-reviewed-locks.log)。
