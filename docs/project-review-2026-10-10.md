# Lengrvis 项目审查报告

审查日期：2026-10-09 至 2026-10-10（Asia/Shanghai）。工作区：`D:\Desktop\mavris`。

**结论：确认 3 类缺陷（1 项 P1、2 项 P2）；当前版本不具备发布验收条件。** 最优先修复的是请求级隐私模式没有贯穿全部模型调用。后端、桌面、手机的现有自动化检查大部分通过，但这些检查没有覆盖本次复现的边界。

本报告审查当前工作区，包含审查开始前已有的修改和未跟踪文件。基线 HEAD 为 `00500c4094608ac636ce9e55edae74ae9d7606bc`，不能用该提交单独重建被审查的内容。本轮没有修改产品源码，也没有提交或发布。证据位于 [.tmp/project-review-current-2026-10-09](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09)。

快照复核：审查开始时记录的 1,736 个文件在结束时 SHA-256 全部一致；本轮新增内容仅为审查报告和临时证据。

## 1. 确认的问题

优先级定义：P1 应优先修复，涉及隐私或关键边界；P2 是特定条件下可触发的功能/可靠性缺陷。下面的三个问题均有隔离复现。发布仪表盘的「P0」属于发布要求的命名，不计入这里的代码缺陷数量。

### F01 · P1 · 请求的 privacy 模式在多个模型调用入口丢失

**影响：用户明确选择 privacy，仍可能把聊天内容、步骤描述/失败观察、管家目标或偏好文本交给配置的云端模型。**

入口和证据：

| 入口 | 代码位置 | 丢失方式 |
| --- | --- | --- |
| `/api/chat` 的主管分流 | [supervisor_agent.py:108](D:/Desktop/mavris/backend/app/agents/supervisor_agent.py:108) | `decide(message, mode)` 收到 mode，但 `get_provider(task="supervisor")` 读取全局配置；mode 只进入提示词。 |
| 子 Agent 非确定性推理/错误恢复 | [base.py:96](D:/Desktop/mavris/backend/app/agents/base.py:96) | `AgentContext.mode` 已存在，选择 provider 时却未使用。编排器调用 `agent.act` 时也没有注入绑定该任务模式的 provider。 |
| 管家保存偏好 | [butler_conversation.py:263](D:/Desktop/mavris/backend/app/services/butler_conversation.py:263) | 新建事项保存了 mode，但调用 `MemoryAgent.remember` 没有传入任务或模式上下文。 |
| 管家回忆偏好 | [butler_conversation.py:281](D:/Desktop/mavris/backend/app/services/butler_conversation.py:281) | `recall(matter.goal)` 同样缺少上下文，查询向量化采用全局设置。 |

共同触发条件：全局配置仍为 `efficiency` 并配置云端 provider，而本次请求/任务/事项为 `privacy`。例如通过 API 为单个请求指定隐私模式，或任务仍保存 privacy、全局配置随后切换。聊天和子 Agent 路径不需要打开文件上传开关；两个记忆路径还需要允许文件内容上传、没有可用本地嵌入实现。回忆路径需要有可检索记忆；常规管家规划还需能到达该步骤。

底层路由器 [registry.py:293](D:/Desktop/mavris/backend/app/llm/registry.py:293) 本身按传入 settings 正确区分本地和云端。问题是调用方未将请求模式绑定到 settings。[MemoryAgent._embed](D:/Desktop/mavris/backend/app/agents/memory_agent.py:60) 只在拿到已存在的 task_id 时恢复任务模式；管家事项尚未生成任务时不能依赖此补救。

**复现结果：** 在独立数据目录、虚构文本和占位密钥下，拦截实际云端 provider 方法，`handle_chat(..., "privacy")` 和 `BaseAgent.act(context.mode="privacy")` 都选择了 `efficiency` provider，且消息包含测试标记。两条记忆路径的实际 `/embeddings` 请求参数包含隐私目标/偏好。拦截在网络发送前完成，没有实际上传文本。证据：[privacy-routing.log](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/privacy-routing.log)、[复现程序](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/reproduce_privacy_routing.py)。

**修复建议：** 统一构造绑定请求/任务/事项 mode 的 settings，并传给所有 provider 和 embedding 调用。为没有 task_id 的管家会话提供显式模式参数。不要只修改提示词或只修 planner。增加「全局 efficiency + 请求 privacy」的端到端调用边界测试，断言云端调用次数为零；本地模型不可用时按现有隐私策略失败或使用明确允许的本地降级。

### F02 · P2 · 手机前后台切换可能重复使用刷新令牌，撤销整个会话

位置：[mobile/app/_layout.tsx:98](D:/Desktop/mavris/mobile/app/_layout.tsx:98)、[定时刷新:175](D:/Desktop/mavris/mobile/app/_layout.tsx:175)、[效果清理:201](D:/Desktop/mavris/mobile/app/_layout.tsx:201)。

**触发顺序：** 定时刷新已发出、响应仍在途中 → 手机进入后台，清理 effect → 快速返回前台，加载 SecureStore 中尚未替换的旧会话 → 前台初始化再次发送相同 refresh token。effect 的 `active=false` 只影响后续 UI 提交，不能收回已经发出的刷新请求。`replaceSessionIfTokenMatches` 发生在网络请求之后，也不能阻止两次令牌轮换。

后端 [mobile_identity.py:199](D:/Desktop/mavris/backend/app/security/mobile_identity.py:199) 将第二次使用已经 rotated 的令牌判定为重放，并撤销 token family。因此第一次请求刚换得的 access token 也变为无效，用户需要重新配对。

**复现结果：** 从实际 RootLayout 文件提取并执行两个 effect，配合真实移动端 API client 和受控网络延迟，得到 `request_count=2`、`same_refresh_token=true`。再用独立数据库中的真实后端轮换逻辑验证：第二次刷新返回 401，第一次的新 access token 也返回 401，family 状态为 `compromised`。这是受控生命周期复现，不是 Android 真机测试。证据：[mobile-race.log](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/mobile-race.log)、[复现程序](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/reproduce_mobile_race.cjs)、[后端证据](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/privacy-routing.log)。

**修复建议：** 将刷新和持久化放入跨 effect 生命周期的单一会话管理器，同一会话共享进行中的刷新 Promise，直到新令牌安全写入后才允许下一次刷新。保留服务端的重放防护。增加「请求进行中切后台/切前台」及退出、重新配对与刷新并发的测试。

### F03 · P2 · Office 完成条件把输入文件类型加入必需输出

位置：[butler_office_completion.py:33](D:/Desktop/mavris/backend/app/services/butler_office_completion.py:33)、[必需输出合并:68](D:/Desktop/mavris/backend/app/services/butler_office_completion.py:68)。

**触发示例：** 用户说「读取现有 Excel 数据，生成 Word 报告」，结构化完成条件也只声明 `artifact_kinds=["docx"]`。当前逻辑根据目标中出现的 Excel/Word 名称直接补充输出要求，得到 `{docx, xlsx}`，没有区分输入材料与交付物。

即使事项已经记录 Word 成果，[verify_office_completion:85](D:/Desktop/mavris/backend/app/services/butler_office_completion.py:85) 也会先返回「仍需完成请求中的 Excel 成果」。后续协调会进入未完成处理，可能额外规划或创建用户未要求的表格，或停留在无法完成状态。

**复现结果：** 中文和英文等价目标均错误要求两个格式；加入只有 docx 的成果记录后，校验在文件完整性检查之前就以缺少 Excel 为由拒绝。该复现验证的是错误的完成条件，并未声称生成或打开了真实 Office 文档。证据：[privacy-routing.log](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/privacy-routing.log)、[复现程序](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/reproduce_backend.py)。

**修复建议：** 让明确的输出契约主导完成判定；从用户意图补充契约时区分读取/引用的输入与生成/导出的输出，后续明确变更仍需生效。加入「Excel 输入 → Word 输出」「Word 输入 → Excel 输出」「双输出」「明确不打开」的回归用例。

## 2. 执行过的验证

| 检查 | 当前结果 | 证据/范围 |
| --- | --- | --- |
| 后端完整 pytest | 初跑 4,655 通过、7 失败、22 跳过；7 项定向重跑全部通过 | [初跑日志](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/backend-pytest.log)、[JUnit](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/backend-pytest.xml)、[重跑日志](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/backend-failed-recheck.log) |
| 后端 Ruff | 全树 lint 通过 | [日志](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/backend-ruff.log) |
| 后端格式 | 170 个已修改/新增 Python 文件通过 | [日志](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/backend-format.log)；不是全树 format 认证 |
| 桌面 typecheck | renderer/main 两套类型检查通过 | [日志](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/desktop/typecheck.log) |
| 桌面 Vitest | 107 个测试文件通过；486 用例通过、1 跳过 | [日志](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/desktop/test.log) |
| 桌面 renderer 构建及体积门禁 | 通过 | 入口 207.7 KB、最大非入口块 129.8 KB、总 JS 775.8 KB、CSS 190.4 KB；[日志](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/desktop/build-renderer.log) |
| 桌面标准 smoke | package.json 中串联的 18 组通过 | 含 IPC/token/preload、浏览器、初始体验、source map 与签名校验器 smoke；[日志](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/desktop/smoke.log)。签名校验器测试不等于签名发布包验收。 |
| Butler Office UI fixture | 通过 | 成果打开入口、变更文件禁用、ID/version 传递、窄屏布局；[日志](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/butler-office-ui.log)。API 为受控数据。 |
| 手机 typecheck | 通过 | [日志](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/mobile-release/typecheck.log) |
| 手机 smoke | 15 组通过 | token、consent、session lifecycle、push、task companion、remote input、wakeup、Android back/TLS/hardening/resources/prebuild；[日志目录](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/mobile-release) |
| 依赖锁完整性 | 通过 | Python 四套 hash 锁、运行/开发版本一致性、三套 npm 锁、Gradle 9.3.1 下载校验和；[日志](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/mobile-release/dependency-locks.log) |
| Python 依赖漏洞审计 | 四套锁均未命中已知漏洞 | runtime/dev/build/acceleration，pip-audit 查询当时的结果；[结果索引](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/dependencies/summary.json) |
| npm 依赖漏洞审计 | **未完成** | root/desktop/mobile 均在 npm advisory bulk 接口 ETIMEDOUT；不能解读为「零漏洞」。[错误详情](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/dependencies/npm-root.json) |
| 密钥扫描 | 当前文件快照未检出泄漏 | Gitleaks 8.30.1，严格 CI 配置，1,736 文件，输出全脱敏；[日志](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/secret-scan.log)。未扫描完整 Git 历史。 |
| 体积/维护性门禁 | 通过 | 1,129 个指定范围文件、269,192 行、P95 789 行；保留仓库已有大文件白名单。[日志](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/maintainability.log) |
| 威胁模型文档校验 | 通过 | [日志](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/threat-model.log)；不替代候选版本的安全负责人审阅 |
| 发布 readiness strict | **失败** | [日志](D:/Desktop/mavris/.tmp/project-review-current-2026-10-09/release-readiness.log)，原因见下一节 |

后端初跑使用仓库内 `--basetemp`。7 项失败均来自诊断/发布证据脚本的路径展示断言：路径处于工作区时按设计输出相对路径，测试却预期工作区外路径对应的文件名。将这 7 项的临时目录移到系统 TEMP 后全部通过，未改源码。因此合并两次证据共有 4,662 个不同后端用例通过、22 跳过；**不能描述为一次完整运行全部通过**。建议测试分别覆盖工作区内外路径，使自定义 basetemp 不再触发误报。

跳过项包括显式启用才运行的已安装应用验收、Windows 当前权限无法创建的符号链接，以及平台不适用分支。跳过项不计作通过。本轮没有计算行/分支覆盖率。

## 3. 发布状态

严格发布检查显示 7 个 stop-ship 条目仍为 `in_progress`，0 passed、0 waived。这是**尚未具备所需发布证据**，不表示发现 7 个 P0 漏洞。

1. 干净 Windows 机器安装、启动、首个任务、卸载/回滚验收。
2. 干净环境中的本地模型安装、启动、拉取和离线隐私任务。
3. Android 真机/模拟器的 LAN HTTPS/WSS、配对、远程输入与撤销验收。
4. 至少 30 个真实自然语言任务的真实 LLM 结果质量评审。
5. 实际导出诊断包的人工外发内容审阅。
6. Public Beta/RC 候选版本及发布负责人签署。
7. 候选版本绑定的 Agent 威胁模型审阅及剩余风险接受记录。

仪表盘当前候选和证据绑定的是 `307c968e421131fa7ce62afdadef404ff02e94a6`，与工作区 HEAD 不一致；CI 证据不可用、工作区不干净、负责人签名及其验证字段缺失。不能用本次本机测试替这些条目填写 passed。代码问题修复后应冻结明确候选，再生成与它绑定的验收证据。

## 4. 覆盖范围与资产清单

范围包含后端 API/认证/授权/策略、模型路由、任务编排、Butler/记忆/Office、SQLite 和隐私清除；桌面 IPC/preload、BrowserHost、原生确认、UI 与构建；移动端会话、配对、远控授权、Android TLS 配置；依赖锁、CI/发布脚本和文档。

本次采用全仓文件清点与哈希、关键链路人工追踪、自动化检查和问题复现。**不是对 374,218 行代码逐行进行形式化验证，也没有为未执行的真机/真实模型流程给出通过结论。**

| 路径 | 文件数 | 定位 |
| --- | ---: | --- |
| backend | 804 | 核心引擎、测试、配置 |
| desktop | 548 | Electron、React、smoke、资源 |
| mobile | 126 | Expo、Android 原生桥、smoke |
| scripts | 124 | 开发、打包、安全及发布门禁 |
| docs | 66 | 架构、审查、合规、发布文档 |
| 其他 | 68 | 根配置、CI、测试资料等 |
| **合计** | **1,736** | Git 已跟踪文件与未忽略的新增文件；不包含 node_modules、venv、运行数据等忽略目录 |

按扩展名分类：源文件 1,414、文档 128、图片/字体等资源 72、配置及其他 122；源文件行数 374,218，包含测试和脚本。维护性门禁采用另一套目录/扩展名范围，也包括部分测试、CSS 和脚本，所以它的 1,129 文件/269,192 行不能解释为纯生产代码量。

没有发现已提交的 node_modules、venv 或大块构建输出目录，也未识别到需要整体迁出的成片 vendored 库。Gradle wrapper 等第三方构建工具仍需按照锁和校验和管理。这个清点不是逐个二进制资源的版权/来源证明，也不是完整代码克隆检测。

### 模块判断

| 模块 | 判断 | 依据与后续动作 |
| --- | --- | --- |
| 后端策略、审批、执行引擎、审计 | Core Asset · 保留 | 是产品核心，已有大量契约/隔离测试；保持授权检查和确定性执行边界。 |
| 隐私模式到 provider/embedding 的传递 | Extract & Merge · 收敛 | F01 显示多个调用方各自补模式易遗漏，收敛为明确且可测试的请求上下文接口。 |
| Butler/Office 协调 | Core Asset · 保留并修复 | 已有完成证据和版本控制，F03 应修正输出契约推导，无整体重写依据。 |
| 记忆、索引、SQLite | Core Asset · 保留 | 使用既有命名空间、生命周期和清除机制；补齐无 task_id 的模式上下文。 |
| Electron IPC、BrowserHost、原生确认 | Core Asset · 保留 | 类型检查、测试、IPC/浏览器 smoke 通过；继续维护后端与 Electron 两侧授权绑定。 |
| React UI 与 Office 成果展示 | Core Asset · 保留 | 构建和预算通过，Office UI fixture 验证有效；真实 Office 打开仍需单独验收。 |
| 手机会话刷新 | Extract & Merge · 收敛 | F02 说明分散的 effect 不是会话轮换的可靠所有者，集中刷新及持久化职责。 |
| 手机配对、授权、原生 TLS | Core Asset · 保留 | 当前静态/行为 smoke 通过；真机生命周期、信任持久化和 LAN 流程仍属验证缺口。 |
| 脚本、依赖锁与发布门禁 | Core Asset · 保留 | 能显式阻断证据不足的发布；修正路径相关测试假设，补齐候选绑定的外部证据。 |

当前证据不足以建议整块 Rebuild 或 Deprecate。大文件门禁通过仍依赖已有例外；维护性指标接近 P95 阈值（789/800 行），可随模块变更逐步拆分，不应为尺寸单独发动全仓重构。

## 5. 与 10 月 9 日旧报告的关系

旧报告不能直接作为当前工作区的问题清单。当前 Electron 声明为 `^42.10.0`，锁定和实际安装为 `42.11.12`；旧报告针对的 `42.9.3` 状态已经改变。pypdf 当前锁定 `6.19.0`，四套 Python 依赖审计均未命中。Electron 相关旧公告的受影响范围可核对 [官方公告](https://github.com/electron/electron/security/advisories/GHSA-qmv3-fv6v-rmhq)；npm 在线审计本次仍因超时未完成，不作全生态零漏洞结论。

旧报告涉及的 Butler 聊天持久化、截图清理、隐私清除协调和 Office 条件调整都有当前实现及已通过的相关回归；本轮没有复现原先相同缺陷。F03 是 Office 修复后仍遗漏的「输入格式不等于输出格式」边界，不等于旧实现从未修复。旧修复记录中的「全量验收仍在运行」也不能代替本报告的最终测试记录。

## 6. 建议修复顺序与验收边界

1. **先修 F01。** 同时覆盖主管、子 Agent、Butler 记忆读写；核对全局模式变更与在途任务。验收标准是隐私调用不会到达云端 provider。
2. **修 F02。** 用共享刷新管理器覆盖网络请求和 SecureStore 写入；确定性竞态测试通过后再做真机前后台验收。
3. **修 F03。** 区分输入材料与输出契约，补中英文目标测试，再跑真实 Office 交付验收。
4. **完成发布前验证。** 在网络可用时重新执行三套 npm audit，形成干净候选，补齐上述 7 项发布证据。

本轮未执行：真实 LLM 质量评审、Android 真机或原生打包、Office/浏览器新增完整原生验收、全平台签名安装包与干净机器验收、MCP 外部 conformance 全套、完整 Git 历史密钥扫描、性能/并发压力测试及资源授权链审计。现有测试通过不能替代这些结果。

交互版报告可按模块、严重程度和文件名筛选，并包含全部快照文件的 SHA-256：[project-review-2026-10-10.html](D:/Desktop/mavris/docs/project-review-2026-10-10.html)。
