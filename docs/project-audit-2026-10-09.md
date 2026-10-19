# Lengrvis 项目审查报告

审查时间：2026-10-08 至 2026-10-09（Asia/Shanghai）  
工作区：`D:/Desktop/mavris`  
基准：`main`，HEAD `00500c4094608ac636ce9e55edae74ae9d7606bc`，包含当时全部未提交及未跟踪代码。  
交互报告：[模块与文件浏览器](D:/Desktop/mavris/docs/project-audit-2026-10-09.html)

## 结论

项目有较完整的权限边界、审批绑定、审计、路径校验和自动化测试基础，建议保留现有架构并集中修复边界衔接。本次确认 **6 项问题：1 项 P1、5 项 P2**；其中四项行为缺陷已用隔离数据复现，两项依赖问题由锁定版本、调用路径及官方公告确认，未运行攻击载荷。

**当前工作区不满足项目自己的发布条件。** 后端全量测试有一个确定失败；待提交 Python 代码存在 lint/格式问题；可维护性门禁和严格发布准备度检查未通过。发布仪表盘中的七项“P0”是尚未完成的发布证据要求，不能解释成七个已证实的严重代码漏洞。

本次没有修复业务代码、更新依赖、提交 Git 或发布产品。1711 个原始文件已与审查快照逐一校验，内容未变化。仅新增本 Markdown 报告和 HTML 报告；日志、隔离复现及构建产物保存在忽略目录中。

## 需要修复的问题

### F01 · P1 · Electron 锁定版本包含已知高危漏洞

- **位置：** [desktop/package-lock.json:3210](D:/Desktop/mavris/desktop/package-lock.json:3210)，[主窗口 preload 配置:166](D:/Desktop/mavris/desktop/src/main/main.ts:166)。
- **证据：** 锁文件安装 Electron `42.9.3`，属于 GHSA-qmv3-fv6v-rmhq 的受影响区间。虽然 Electron 声明在 devDependencies 中，它仍是最终桌面应用运行时，不能按纯开发依赖忽略。
- **影响与条件：** 官方公告描述了已被攻陷的 renderer 污染 sandbox preload 缓存、在后续加载中执行攻击者代码的路径。本产品使用 sandbox preload，并具有加载外部网页的浏览器能力。这是必须更新的运行时风险；本审查未证明具体站点可直接突破该产品，也未复现完整利用链。[Electron 官方安全公告](https://github.com/electron/electron/security/advisories/GHSA-qmv3-fv6v-rmhq)
- **修复：** 将 Electron 与锁文件更新至该公告已修复的版本（42 分支至少 `42.10.0`，或经兼容验证的其他修复版本），重新构建并执行 IPC、preload、BrowserHost 和打包验证。

### F02 · P2 · PDF 文本提取使用存在资源耗尽漏洞的 pypdf

- **位置：** [backend/requirements-lock.txt:1027](D:/Desktop/mavris/backend/requirements-lock.txt:1027)，[ocr_service.py:315](D:/Desktop/mavris/backend/app/indexer/ocr_service.py:315)，[document_tools.py:99](D:/Desktop/mavris/backend/app/tools/document_tools.py:99)。
- **证据：** 运行时锁定 `pypdf==6.16.2`；文档工具直接执行 `PdfReader` 和每页 `extract_text()`。官方 GHSA-fp3h-c4fm-7vvf 明确指出特制 `/ToUnicode` 字体映射在文本提取时会造成大量内存消耗，影响 `<6.18.1`。[pypdf 官方安全公告](https://github.com/py-pdf/pypdf/security/advisories/GHSA-fp3h-c4fm-7vvf)
- **影响与条件：** 用户授权读取的外部 PDF 可以到达该代码路径。现有文件字节上限和 OCR 图片数量上限不能等同于限制字体映射展开后的内存；普通异常捕获也不能恢复被操作系统终止的进程。未执行恶意 PDF 或测量实际耗尽规模。
- **修复：** 重新生成锁文件，覆盖本次扫描命中的全部 pypdf 公告（这些公告的修复版本最高为 `6.19.0`）；保留现有大小限制，并考虑把不可信文档解析放入有时间、内存预算的独立进程。[6.19.0 官方发布说明](https://github.com/py-pdf/pypdf/releases/tag/6.19.0)

### F03 · P2 · 管家聊天未进入聊天历史，重新加载后丢失

- **位置：** [useTaskSubmission.ts:161](D:/Desktop/mavris/desktop/src/renderer/app/useTaskSubmission.ts:161)，[butler_service.py:186](D:/Desktop/mavris/backend/app/services/butler_service.py:186)，[useWorkspaceRefresh.ts:151](D:/Desktop/mavris/desktop/src/renderer/app/useWorkspaceRefresh.ts:151)。
- **触发：** 当前桌面桥支持 Butler，用户正常发送问候或新任务，随后重新加载聊天历史或重启界面。
- **原因：** App 默认注入 `butler.submit`。成功分支只把用户消息和即时回复加入前端状态，然后提前返回，绕过原有 `/api/chat` 持久化路径；Butler 的 message 方法也没有保存这两条聊天消息。工作区恢复时又用后端历史覆盖前端消息。
- **复现：** 隔离数据库中暂停管家调度，发送“你好”和“整理我的项目进度”，两次均成功返回；`persisted_matters=1`，但 `persisted_chat_messages=[]`。事项持久化和后续通知持久化不等于原始对话持久化。[复现记录](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/repro-chat.log)
- **修复：** 在统一服务边界保存用户消息和即时回复，并向前端返回稳定消息 ID；补充“发送 → 重新读取历史/重启 → 内容及顺序保持”的集成测试。

### F04 · P2 · Office 完成检查强制要求 Excel、Word 和打开应用

- **位置：** [butler_evidence.py:279](D:/Desktop/mavris/backend/app/services/butler_evidence.py:279)。
- **触发：** 用户只要求生成一个 Excel 或 Word，或明确要求保存文件但不打开 Office。
- **原因：** 只要 `matter.artifacts` 非空，验证器就先要求种类集合恰好为 `{xlsx, docx}`，且对全部成果使用 `require_open=True`，不考虑该事项实际声明的完成条件。后面的 `file_exists` 等条件无法解除这一额外约束。
- **复现：** 使用仓库 Office 测试夹具真实生成并回读一个 xlsx，将目标设为“只生成 Excel，不生成 Word，也不要打开 Office”，完成条件为该文件存在。文件校验、执行证据校验均通过，但最终返回 `completion_ok=false`，理由仍要求生成 Word 并打开两份成果。[复现记录](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/repro-office.log)
- **影响：** 已满足用户目标的事项仍无法完成，并可能进入补做或等待输入流程。本次没有执行额外文档生成或真实启动 Office。
- **修复：** 从用户目标及明确的完成条件推导所需成果类型和是否打开；将“双成果且打开”限制在明确请求该流程的事项中。

### F05 · P2 · 异常退出遗留的浏览器截图不在下次隐私清除范围内

- **位置：** [browserScreenshotStore.ts:51](D:/Desktop/mavris/desktop/src/main/browserScreenshotStore.ts:51)，[同文件:121](D:/Desktop/mavris/desktop/src/main/browserScreenshotStore.ts:121)，[browserHost.ts:156](D:/Desktop/mavris/desktop/src/main/browserHost.ts:156)。
- **触发：** 进程生成截图后，未完成 `BrowserHost.destroy()` 清理就退出；重启后用户执行“删除本机数据”。
- **原因：** 每次实例化都用 PID 和随机 UUID 生成新的临时目录，截图清单仅存在内存中；`clear()` 只删除当前实例目录。源码中未发现追踪和清除历史截图目录的机制。
- **复现：** 使用合成图片数据与隔离 TEMP 目录，让前一个进程保存截图后结束，再让新进程保存并清除当前截图。旧截图仍存在，当前截图被删除。此为真实存储模块的进程重启复现，没有读取用户截图或实际终止桌面应用。[复现记录](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/repro-privacy.log)
- **影响：** 包含网页内容的临时图像可能在用户认为已清除个人数据后继续留在磁盘；64 MiB 全局预算同样仅约束当前实例。
- **修复：** 为应用管理的截图建立可恢复的清单或固定私有根目录，在启动和显式清除时处理遗留数据；清理须验证归属并拒绝链接/路径越界。

### F06 · P2 · 后端拒绝清除时，桌面已经不可逆删除部分数据

- **位置：** [ipcSystemSettingsHandlers.ts:88](D:/Desktop/mavris/desktop/src/main/ipcSystemSettingsHandlers.ts:88)，[local_data_lifecycle.py:74](D:/Desktop/mavris/backend/app/core/local_data_lifecycle.py:74)，[browserHost.ts:144](D:/Desktop/mavris/desktop/src/main/browserHost.ts:144)。
- **触发：** 后端有任务、索引写入或其他活动工作时，用户确认删除本机数据；后端将返回 `409 local_data_busy`。确认票据在本机弹窗等待期间过期，也存在先删后拒绝的相同顺序问题。
- **原因：** IPC 先执行桌面 eraser，清除浏览器会话、截图和密码库，随后才提交后端删除请求。获取 native challenge 时没有占用后端清除边界。后端实际拒绝时，桌面删除无法回滚。
- **复现：** 调用编译后的真实 IPC 注册处理器，模拟用户确认和后端 busy 响应。调用顺序为 `challenge_created → user_confirmed → desktop_credentials_erased → backend_rejected_busy`，响应失败而合成凭据剩余数为 0；另调用真实后端 busy guard 确认错误码。IPC 的 UI/后端传输是 mock，未删除真实凭据。[IPC 复现](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/repro-privacy.log) · [后端验证](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/repro-privacy-backend.log)
- **修复：** 在首次破坏性步骤前，完成有效确认并建立覆盖前后端的清除预留状态，防止新工作进入。明确记录和呈现部分清除结果并支持重试；简单调换两次删除的顺序不能解决另一侧失败的问题。

## 自动化验证与发布门禁

| 检查 | 结果 | 解释 / 证据 |
|---|---|---|
| 后端全量 pytest | **4641 通过、1 失败、22 跳过** | [完整日志](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/backend-tests.log)，耗时 993.22 秒 |
| 失败项单独重跑 | **仍失败**，其余 2 项通过 | [重跑日志](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/backend-failure-retest.log) |
| Desktop Vitest | **472 通过、0 失败、1 跳过** | [结果 JSON](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/desktop-tests.json) |
| Desktop typecheck | **通过** | Web 与 Node 两个 TypeScript 工程 |
| Desktop 完整 smoke | **18 组全部通过** | 包含 renderer/Electron 构建、IPC、token、preload、BrowserHost、UI、资源及 source-map 检查；[日志](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/desktop-smoke.log) |
| Mobile typecheck | **通过** | [日志](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/mobile-typecheck.log) |
| Mobile smoke | **15 组全部通过** | token、会话、配对/授权、推送、返回键、Android 硬化/TLS 配置等；[结果](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/mobile-smokes.json) |
| Python AST 解析 | **通过** | 枚举到的 Python 文件无语法错误 |
| Ruff 全树检查 | **5 项问题** | 3 个 E501、2 个 I001；[详情](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/ruff.json) |
| Ruff 全树债务预算 | **通过：5 ≤ 1116** | 当前预算明显宽于现状，建议下调；[日志](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/ruff-baseline.log) |
| 待提交 backend Python 格式 | **68 / 153 文件需要格式化** | 只运行 `format --check`，未改文件；[日志](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/ruff-format.log) |
| 依赖锁完整性 | **通过** | Python 固定版本/hash、npm lock v3、Gradle checksum；[日志](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/locks.log) |
| Agentic threat-model 检查 | **通过** | 检查脚本返回 ok=true，无 errors |
| Gitleaks 当前源码快照 | **2 处误报，退出码 1** | 命中 `secrets.token_hex(32)` 动态生成测试密钥的代码，不是明文凭据；[脱敏结果](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/secrets.json) |
| 可维护性门禁 | **未通过** | 新增超限模块与区域最大文件阈值；[日志](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/maintainability.log) |
| 严格发布准备度 | **未通过** | 七项 P0 发布条件均 in_progress，证据提交与 HEAD 不符；[日志](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/release-readiness.log) |

### 需要处理的工程门禁

1. **异常边界检查失败。** [routes_butler.py:45](D:/Desktop/mavris/backend/app/api/routes_butler.py:45)、[butler_memory.py:78](D:/Desktop/mavris/backend/app/services/butler_memory.py:78)、[office_application.py:81](D:/Desktop/mavris/backend/app/services/office_application.py:81) 的宽泛捕获没有仓库要求的 `broad-exception-boundary` 标记。这是可重复的项目规则失败，不是 Windows 环境误差；应先确认捕获的错误范围及降级行为，再精确捕获或添加经过审阅的说明，不能机械压掉检查。
2. **Ruff 门禁。** 当前 `.github/workflows/lint.yml` 对变更文件执行严格 lint 和 format。AGENTS 中“CI 不检查 Ruff”的说明已过时。需要修复 5 项 lint 并格式化本批 68 个文件；不能仅根据总量低于历史预算判断 CI 会通过。
3. **维护成本已触发仓库限制。** 新超限文件包括 `butler_service.py` 1666 行、`browserHost.ts` 1084 行、`ui_automation_tools.py` 942 行、`core/db.py` 907 行、`browser_tools.py` 902 行。前两者还超过区域上限 1400/900。优先拆出生命周期、证据验证、资源处理等明确职责，保持现有权限入口一致。
4. **发布证据过期且未完成。** 仪表盘仍引用 `307c968e…`，当前 HEAD 为 `00500c40…`；记录的 CI 状态不可用、工作区 dirty、人工签署及签名证据未完成。需在修复后针对明确候选版本重新生成和签署，不能直接把状态改成 passed。本次没有运行安装包签名/发布，也没有代表发布负责人签署。
5. **密钥扫描规则误报。** `scripts/run_butler_browser_flow.py:554` 与 `scripts/run_butler_office_flow.py:519` 的随机测试密钥生成表达式被现有规则识别为密钥。应采用精确规则或局部允许列表后复核，不能忽略整个文件，也不应把当前两项认定为真实泄露。扫描限于当前源码，不包含历史 Git 提交或用户私有配置。

## 依赖审计

| 范围 | 扫描结果 | 优先判断 |
|---|---|---|
| 根 npm lock | 7 个受影响包条目：1 critical、3 high、3 moderate | 主要是开发/MCP 工具链；应核对调用方式 |
| Desktop npm lock | 18 个条目：2 critical、7 high、9 moderate | Electron 作为产品运行时优先修复；构建链依赖单独评估 |
| Mobile npm lock | 43 个条目：1 critical、35 high、6 moderate、1 low | 大量上游影响传播到 Expo/RN 工具链，不能推断均进入 APK |
| Python runtime lock | 4 个包、26 条公告 | anyio 4.13.0、PyJWT 2.13.0、pypdf 6.16.2、soupsieve 2.8.4 |
| Python dev lock | 5 个包、29 条公告 | 在 runtime 命中基础上增加 urllib3 2.7.0 |
| Python build lock | 0 条命中 | 扫描通过，不等同于所有供应链风险为零 |
| acceleration lock | anyio 4.14.1，3 条公告 | 升级并重新执行可选加速环境检查 |

这些数字是扫描器的**受影响包条目/公告数**，不是独立、可利用漏洞总数，各锁文件之间也有重复。完整证据在 [root npm](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/audit-root.json)、[Desktop npm](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/audit-desktop.json)、[Mobile npm](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/audit-mobile.json)、[Python runtime](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/pip-audit.json) 等日志中。

例如 `shell-quote` 1.10.0 的命中需要攻击者控制相应 shell token/comment 路径，不能直接视为桌面应用任意命令执行；本次未确认这一产品利用路径。[维护者公告](https://github.com/ljharb/shell-quote/security/advisories/GHSA-pqg4-j6r4-53mv) 同样，PyJWT 的多条公告不能自动转换为本项目可绕过认证的结论：检查到的签名验证限定 HS256，未发现相应动态 JWK 路径。更新锁文件仍有必要，但优先级应考虑真实输入入口。

## 模块与资产评估

| 模块 | 结论 | 审查判断与下一步 |
|---|---|---|
| backend security / policy | **Core Asset** | 原生确认、桌面 token、审批 HMAC、作用域和路径防护值得保留；继续用负向测试保护边界 |
| backend agents / orchestration | **Core Asset** | 调度、恢复、审批和执行证据有分层；修复条件判定，不建议整套重写 |
| backend core / DB | **Extract & Merge** | 保留审计完整性与数据生命周期锁，继续按数据域拆分超限 db.py，避免多处重复实现持久化 |
| backend services / Butler | **Extract & Merge** | Butler 协调器已 1666 行；拆分消息持久化、事项生命周期和完成验证，修复 F03/F04 |
| backend tools / perception | **Extract & Merge** | 保留统一权限/执行入口；将 UIA、浏览器工具的解析、定位、执行拆为明确组件 |
| backend indexer / documents | **Core Asset** | 文件范围、内容来源和检索能力可复用；修复 F02 并增加解析资源隔离 |
| backend LLM / context | **Core Asset** | 具备隐私模式、预算与 provider 适配；真实模型质量和外网调用未在本次验收 |
| backend integrations / MCP / commerce | **Core Asset** | 连接器和协议边界有对应测试；未验证真实第三方账户或商业激活服务 |
| desktop main / BrowserHost | **Extract & Merge** | 类型化 IPC 和 sandbox 基础保留；拆分截图生命周期与跨端隐私清除，修复 F01/F05/F06 |
| desktop preload / shared | **Core Asset** | 窄桥接接口及参数验证有效；全部 IPC/preload/token smoke 通过 |
| desktop renderer | **Core Asset** | UI 组件和任务流可保留；缺少覆盖新 Butler 默认入口与历史恢复的真实联通测试 |
| mobile / Android companion | **Core Asset** | Preview 定位合理，15 组 smoke 通过；仍需原生构建、真机配对/TLS/后台通知验收 |
| scripts / CI / tests | **Extract & Merge** | 测试资产有价值；合并重复启动/检查助手，补四类负向复现，收紧 Ruff 债务预算 |
| release evidence / 操作文档 | **Rebuild** | 重新生成当前候选版本证据；校正 AGENTS 的 Ruff、smoke 数量和 Vite 现状描述 |

没有足够证据把某个完整业务模块判为 **Deprecate**。文件级完全重复仅发现 `mobile/App.tsx` 与 `mobile/index.ts` 的短入口内容，不足以推出可节约显著成本的代码重复。没有把受忽略的依赖或生成目录计入自有源码资产。

## 范围、方法与未覆盖项

本次执行了全仓文件枚举和分类、Python 语法解析、依赖/密钥扫描、后端全量测试、桌面单测/类型/构建/完整 smoke、移动端类型/smoke，以及权限、任务执行、聊天、Office 成果、隐私清除等跨模块关键路径人工追踪。**“全项目范围”不表示逐行人工证明全部源码正确。**

| 文件类别 | 文件数 | 文本行数 |
|---|---:|---:|
| 源码（含脚本、样式、原生代码） | 987 | 241351 |
| 测试及测试脚本 | 463 | 142780 |
| 文档 | 112 | 8907 |
| 配置及资源 | 149 | 未纳入文本行统计 |
| 合计 | **1711** | — |

分类依据为路径和扩展名；不同统计脚本对 smoke/fixture 的分类可能不同，不应将它们的行数直接互换。Git 可见文件未发现成片提交的 vendor 源码；`node_modules`、`.venv`、构建结果、真实 `.env`/数据目录和被忽略的 `vendor/lengrvis-code` 等不在自有源码逐文件审阅范围，第三方依赖通过 lock 审计。

测试使用隔离配置和数据目录，未读取真实密钥或操作用户数据库。22 个后端跳过项包括需主动开启的已安装 Windows 应用验收，以及当前会话缺少符号链接权限或平台不适用的测试。这次通过的 Android 检查是代码/配置 smoke，**没有运行 Android 原生构建或真机测试**；也没有执行真实模型端到端规划、真实 Office 安装环境验收、安装包安装/升级回滚、签名验证和发布。

## 建议实施顺序

1. 更新实际交付的 Electron 和 Python 运行时依赖，重新生成有 hash 的锁文件并回归对应调用入口。
2. 修复 F03/F04 的用户可见错误，以及 F05/F06 的隐私清除一致性；将本报告复现转换为稳定回归测试。
3. 处理异常边界、lint/format 和模块规模门禁。完整测试已经暴露了确定性规则失败，不应以“绝大多数通过”放行。
4. 在明确候选提交上补齐真实应用、真机和安装包验收，重新生成发布证据，再由发布负责人完成签署。

原始枚举：[inventory.json](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/inventory.json)；原文件完整性：[source-integrity.json](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/source-integrity.json)；可重跑的隐私复现：[repro-privacy.cjs](D:/Desktop/mavris/.tmp/project-audit-2026-10-08/repro-privacy.cjs)。
