# Lengrvis 全项目审查 · 2026-10-11

哥哥，本轮确认 **3 类可复现缺陷（1 个 P1、2 个 P2）**。现有自动化检查大部分通过，但隐私上下文隔离、事项级提交去重和长文档检索仍有实际缺口；严格发布就绪门禁未通过。建议先修复 F01，再处理 F02、F03，并完成候选版本对应的发布验收。

## 审查对象与结论边界

- 工作区：`D:/Desktop/mavris`；分支 `main`；HEAD `00500c4094608ac636ce9e55edae74ae9d7606bc`。
- 覆盖当前工作树，包括开始时已存在的 174 个已修改跟踪文件以及未跟踪的 Butler、浏览器、Office、移动会话相关代码；结论不是只针对 HEAD。
- 审查于 2026-10-10 开始，2026-10-11 继续并出具报告。后端全量测试记录来自 10 月 10 日；续查确认原有 1,743 个文件 SHA-256 全部一致，所以未重复运行已通过的全套测试。
- 完成全工作树文件分类、自动化验证和关键调用链人工审查；未声称逐行人工审阅全部源码。源码规模门禁自己的统计口径为 1,131 文件 / 269,788 行，与全文件资产清单口径不同。
- 原有文件内容未改动；本次仅新增报告与复现材料。复现使用临时数据目录及虚构内容，云端模型发送与浏览器外部 executor 已替换，不会真实发出云端请求或提交表单。

源码一致性证据：[哈希核验](D:/Desktop/mavris/docs/audit-evidence-2026-10-11/source-end-verify.json)。可检索资产报告：[交互式 HTML](D:/Desktop/mavris/docs/project-full-review-2026-10-11.html)。

## 确认的问题

### F01 · P1 · privacy 来源内容会进入后续云端事项的规划请求

**影响：** 正确选择当前事项的 provider 仍不足以隔离隐私数据：其他 privacy 事项的目标和 privacy 模式保存的偏好，能够成为 efficiency 云端规划请求的正文。

**触发条件：** 同一数据目录中存在 privacy 事项或确认过的隐私偏好，之后运行使用云端 provider 的 efficiency 事项。跨事项目标路径要求开启 coordination_enabled；偏好路径在关闭协调时也能触发。

**原因：** decide() 的 related_matters 遍历其他事项时未按来源 mode 过滤。MemoryAgent.remember() 只将 mode 用于 embedding 路由，记忆记录没有保留供后续召回使用的隐私模式或出网权限；preference_context() 将召回的已确认内容直接加入规划 payload。

**复现结果：** 真实 coordinator、provider 选择和数据库路径下，在 OpenAICompatibleProvider 的发送方法入口截获了包含虚构 privacy 目标的 efficiency 请求；关闭协调后，另一个 efficiency 请求包含 privacy 模式保存的偏好。allow_file_content_upload=false，实际网络请求为 0。

**定位：** [backend/app/services/butler_service.py](D:/Desktop/mavris/backend/app/services/butler_service.py:110)；[backend/app/services/butler_service.py](D:/Desktop/mavris/backend/app/services/butler_service.py:120)；[backend/app/services/butler_conversation.py](D:/Desktop/mavris/backend/app/services/butler_conversation.py:276)；[backend/app/agents/memory_agent.py](D:/Desktop/mavris/backend/app/agents/memory_agent.py:129)；[backend/app/agents/memory_agent.py](D:/Desktop/mavris/backend/app/agents/memory_agent.py:259)。

**修复方向：** 为持久化内容保留来源模式和可出网策略；在云端规划 payload 构造边界统一过滤或要求明确放行。隐私事项只能向其他任务暴露符合策略的元数据；旧记忆缺失来源时采用保守迁移策略。

**应补回归：** 覆盖 privacy → efficiency、privacy → hybrid 云端 planner，分别测试协调开关、已完成偏好事项与暂停事项；断言云端发送入口不含 privacy 来源正文。不要只测试当前任务的 provider.mode。

材料：[复现脚本](D:/Desktop/mavris/docs/audit-evidence-2026-10-11/reproduce_privacy_context.py) · [实际输出](D:/Desktop/mavris/docs/audit-evidence-2026-10-11/reproduce-privacy-context.log)。

### F02 · P2 · 表单重复提交检查比较原始参数与脱敏日志，漏判已完成提交

**影响：** 同一事项后续执行再次提出相同表单提交时，事项级保护不会要求先核对原网页回执。该缺陷会增加重复业务操作风险；其他审批与浏览器授权仍然生效。

**触发条件：** browser.submit_form 或 browser.act(kind=submit) 提交包含实际 selector/fields 的表单，记录为 committed 后，在同一事项的下一次执行中提出相同提交。

**原因：** 生产 proposal 流程先将 selector、fields 脱敏后写入 ToolCall.args；repeated_submission() 又从这些历史 args 重建业务标识，与当前未脱敏的参数比较。两个标识不同，因此 committed 记录也不匹配。已有单测直接写入原始 args，绕过了实际日志脱敏。

**复现结果：** 通过真实 _publish_tool_call_proposal()、执行 claim 与 commit 生成日志，两种入口均得到 guard_with_committed_prior=false；新 task 绑定同一事项后 validate_task_action(..., live=True) 仍允许相同提交。外部 executor 替换为计数器，没有真实提交。

**定位：** [backend/app/services/butler_effects.py](D:/Desktop/mavris/backend/app/services/butler_effects.py:43)；[backend/app/orchestration/tool_runtime_lifecycle.py](D:/Desktop/mavris/backend/app/orchestration/tool_runtime_lifecycle.py:70)；[backend/app/orchestration/tool_runtime_lifecycle.py](D:/Desktop/mavris/backend/app/orchestration/tool_runtime_lifecycle.py:351)；[backend/app/tools/browser_tools.py](D:/Desktop/mavris/backend/app/tools/browser_tools.py:759)；[backend/tests/test_butler_full_flow.py](D:/Desktop/mavris/backend/tests/test_butler_full_flow.py:173)。

**修复方向：** 在脱敏前计算并持久化受完整性保护的业务提交摘要，跨入口共享规范化规则；使用摘要做重复检查，继续保留日志脱敏。摘要不得包含 task/step ID，否则无法覆盖跨执行重试；同时区分当前尚未产生副作用的 claim 与真正的历史提交。

**应补回归：** 用生产 proposal/journal 流程覆盖 submit_form ↔ act 两种别名及 committed、outcome_unknown；未提交回执应允许安全重试。不要用未脱敏的手工 ToolCall 代替生产日志。

材料：[复现脚本](D:/Desktop/mavris/docs/audit-evidence-2026-10-11/reproduce_submission.py) · [实际输出](D:/Desktop/mavris/docs/audit-evidence-2026-10-11/reproduce-submission.log)。

### F03 · P2 · 候选集丢失 FTS 命中的分块，长文档返回不相关片段

**影响：** 答案明明在已建立索引的长文档中，语义检索却返回文首无关片段，下游问答和证据引用可能据此漏答。

**触发条件：** 命中内容位于较长文档靠后的分块，例如默认 80 个候选之外的 chunk_index=99。

**原因：** _candidate_chunks() 从 FTS 结果只保留 file_id，再交给 _chunks_for_files() 按 chunk_index 升序取前 limit 个分块，实际命中的 chunk 标识与排名被丢弃。_lexical_chunks() 也复用该方式。

**复现结果：** 真实 FTSIndex 索引约 110 KB 的虚构文档；唯一关键词位于 chunk 99，FTS 能找到该文件，但 80 个候选中没有命中块，VectorIndex.search() 返回 chunk 0。使用确定性 embedding 隔离模型质量变量。

**定位：** [backend/app/indexer/vector_index.py](D:/Desktop/mavris/backend/app/indexer/vector_index.py:89)；[backend/app/indexer/vector_index.py](D:/Desktop/mavris/backend/app/indexer/vector_index.py:223)。

**修复方向：** 保留实际命中的 chunk ID 与 rank，优先将这些块纳入候选；权限过滤和相关性排序应先于候选截断，邻接上下文扩展只能使用剩余预算。

**应补回归：** 新增末尾唯一关键词、多文件竞争候选预算、无 embedding 的词法回退测试，断言命中块进入候选并被返回，而不只检查是否返回了文件。

材料：[复现脚本](D:/Desktop/mavris/docs/audit-evidence-2026-10-11/reproduce_search.py) · [实际输出](D:/Desktop/mavris/docs/audit-evidence-2026-10-11/reproduce-search.log)。

### 表单复现的校正

探索阶段直接写入未脱敏 ToolCall 时出现过“首次提交被拦截”的结果。该构造不符合生产日志流程，因此已排除，不计为发现。最终报告及随附脚本以真实 proposal 脱敏、journal claim/commit、后续任务边界为依据，确认的是重复提交漏判。F02 也没有证明绕过了其他审批或原生浏览器授权。

## 验证结果

| 检查 | 结果 | 证据与范围 |
|---|---|---|
| 后端全量 pytest | 通过 | 4,775 passed / 22 skipped，0 failures / 0 errors，884.08 秒。[日志](D:/Desktop/mavris/.tmp/full-review-20261010-1756/backend-pytest.log) |
| 后端 Ruff | 通过 | 全 backend lint 通过；182 个变更或未跟踪 Python 文件格式检查通过。[日志](D:/Desktop/mavris/.tmp/full-review-20261010-1756/gates-ruff.log) |
| 桌面 TypeScript / Vitest | 通过 | typecheck 通过；107 个测试文件，486 passed / 1 skipped。[日志](D:/Desktop/mavris/.tmp/full-review-20261010-1756/desktop-test.log) |
| 桌面构建 / smoke | 通过 | renderer 构建与预算门禁通过，package.json 的 18 组 smoke 全通过。[日志](D:/Desktop/mavris/.tmp/full-review-20261010-1756/desktop-smoke.log) |
| 移动端 TypeScript / smoke | 通过 | typecheck 与 15 组 smoke 全通过。[日志](D:/Desktop/mavris/.tmp/full-review-20261010-1756/mobile-results.json) |
| 仓库卫生 / 依赖锁 / 威胁模型 / 源码规模 | 通过 | 四项机器门禁通过；源码规模 P95=789 行，阈值=800。[日志](D:/Desktop/mavris/.tmp/full-review-20261010-1756/gates-results.json) |
| Python 依赖检查 | 通过 | pip check 通过；runtime/dev/build/acceleration 四份锁文件的 pip-audit 未报告已知漏洞。[日志](D:/Desktop/mavris/.tmp/full-review-20261010-1756/dependencies-results.json) |
| 当前工作区秘密扫描 | 通过 | gitleaks 对当前 1,743 文件快照扫描约 17.38 MB，配置范围内无命中；未扫描完整 Git 历史。[日志](D:/Desktop/mavris/.tmp/full-review-20261010-1756/gates-secrets.log) |
| npm 漏洞审计 | 未完成 | 根目录、desktop、mobile 两轮均因 npm advisory endpoint ETIMEDOUT 失败，不能判定无漏洞。[日志](D:/Desktop/mavris/.tmp/full-review-20261010-1756/npm-retry-root.log) |
| 严格发布就绪门禁 | 未通过 | 7 个 stop-ship 条目仍 in_progress；候选版本、CI、签署证据未满足要求。[日志](D:/Desktop/mavris/.tmp/full-review-20261010-1756/gates-release-readiness.log) |

后端跳过 22 项：5 项需显式启用已安装 Windows 应用验收；10 项缺少创建符号链接权限；其余 7 项是当前 Windows 平台不适用的 POSIX、dir_fd 或替代密钥存储分支。跳过项不计作通过。桌面另有 1 项跳过。

本次未生成测试覆盖率报告，也未完成 Android 真机/模拟器安装、真实本地模型断网任务、真实云端 LLM 质量集、签名安装包交付、全量 MCP 官方一致性测试或干净机器验收。桌面签名 smoke 验证的是检查逻辑，不能代替真实产物签名验证。

## 发布阻断与依赖审计缺口

`npm run release:readiness:strict` 返回非零。[发布看板](D:/Desktop/mavris/docs/release/release-readiness-dashboard.md:26) 中 7 个 stop-ship 条目均为 `in_progress`：

1. Windows 干净机器安装、启动、任务、诊断与卸载验收。
2. 本地模型安装、启动、拉取及隐私任务验收。
3. Android 安装包与真机/模拟器 LAN HTTPS/WSS 链路验收。
4. 至少 30 个真实自然语言任务的结果质量评审。
5. 实际诊断包的人工外发审查及签署。
6. Public Beta/RC 候选版本、门禁和发布负责人签署。
7. 与候选版本绑定的威胁模型评审与剩余风险接受。

这些是仓库定义的 P0 发布证据条目，不是本轮发现了 7 个 P0 代码漏洞。看板候选与证据仍绑定 `307c968e421131fa7ce62afdadef404ff02e94a6`，与当前 HEAD 不同；CI 状态、干净工作树和可验证负责人签名也未满足严格门禁。威胁模型结构校验通过与正式风险签署完成是两件独立的事。

npm 审计于两轮尝试中均发生 advisory endpoint 连接超时；根目录、desktop、mobile 的 JS 依赖漏洞状态仍未知。恢复该接口访问后，须重新审计三份锁文件。Python 四份锁文件本轮 pip-audit 无已知漏洞报告，只代表本次查询结果。

## 资产与模块判断

| 分类 | 文件数 |
|---|---:|
| 配置与其他 | 66 |
| 文档 | 119 |
| 项目代码与脚本 | 1046 |
| 依赖元数据 | 8 |
| 测试与样例 | 429 |
| 静态资源 | 72 |
| 嵌入的第三方构建引导文件 | 3 |

合计 **1,743 文件**。逐文件分类见 [CSV 清单](D:/Desktop/mavris/docs/audit-evidence-2026-10-11/file-inventory.csv)；忽略的 `.venv`、`node_modules`、本地数据和构建缓存不计入源码资产。

嵌入的第三方构建引导文件为 Android Gradle wrapper 的 `gradlew`、`gradlew.bat`、`gradle-wrapper.jar`；配置指向 Gradle 9.3.1 并固定 distribution SHA-256。未在本轮目录、版权和版本标记检查中识别出额外大体积 vendored 库；这不等同于完成了第三方许可证或二进制组成的法律审计。`desktop/build/entitlements.mac.plist` 是构建配置，不是应删除的构建产物。

| 范围 | 判断 | 行动 |
|---|---|---|
| backend · 806 文件 | Core Asset | 本地执行、策略、审批、SQLite 和模型路由是核心资产。保留架构；先修复 F01–F03 的跨模块契约。 |
| desktop · 548 文件 | Core Asset | Electron 主进程承担 IPC、来源校验、浏览器执行授权和本机凭据边界。类型、单测及 smoke 通过；仍需真实打包与干净机器验收。 |
| mobile · 128 文件 | Core Asset | 保留 Preview 定位。认证会话、推送、TLS 与撤销约束具备脚本覆盖；不能据此声称 Android 真机链路已验收。 |
| 大型运行时 / 发布脚本 | Extract & Merge | 按既有模块边界逐步提取职责与重复校验。context/management.py=1391 行、run_service.py=1369 行；发布脚本最大 2359 行。规模门禁通过不等于低耦合。 |
| docs / release / CI | Core Asset | 保留现有 fail-closed 验收及证据绑定。更新候选和签署前，不应将本轮本地测试结果当作发布批准。 |
| 全栈重写 / 删除模块 | 无充分证据 | 本轮未发现足以支持 Rebuild 或 Deprecate 整个子项目的证据；不要把体积大、测试替身或 Preview 状态直接当作废弃理由。 |

关键人工审查覆盖：后端模式传递与上下文拼接、权限和审批绑定、执行日志与撤销、文件路径与资源状态、浏览器授权及回执、文档索引、IPC 与 renderer 来源校验、移动认证会话/TLS/撤销、CI 与发布证据。各模块“未新增确认问题”不等于不存在其他问题。

## 建议执行顺序

1. 修复 F01，先阻止 privacy 来源正文进入任何云端 planner 上下文；补跨事项和跨模式记忆测试。
2. 修复 F02，用生产脱敏与持久化流程验证跨执行、跨浏览器入口的业务提交去重。
3. 修复 F03，保留命中 chunk 并验证长文档召回。
4. 重跑受影响测试；恢复 npm 审计；对新的干净候选运行发布门禁，并补齐真实设备、真实模型和人工签署证据。

## 复现与完整日志

以下脚本验证缺陷边界，不替代真实外部系统端到端测试。使用仓库现有虚拟环境；各脚本自行隔离临时数据库。

```powershell
Set-Location D:\Desktop\mavris
.venv/Scripts/python.exe docs/audit-evidence-2026-10-11/reproduce_privacy_context.py
.venv/Scripts/python.exe docs/audit-evidence-2026-10-11/reproduce_submission.py
.venv/Scripts/python.exe docs/audit-evidence-2026-10-11/reproduce_search.py
```

汇总：[机器结果](D:/Desktop/mavris/docs/audit-evidence-2026-10-11/evidence-summary.json)。原始后端 JUnit：[backend-pytest.xml](D:/Desktop/mavris/.tmp/full-review-20261010-1756/backend-pytest.xml)。其余完整命令与 stdout/stderr 保存于 `D:/Desktop/mavris/.tmp/full-review-20261010-1756`。
