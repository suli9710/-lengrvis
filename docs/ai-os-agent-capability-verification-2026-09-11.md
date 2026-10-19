# Windows AI OS 能力修复与验收记录

日期：2026-09-11。目标场景：Windows 应用操作与跨应用任务。

## 结论与范围

Windows 原生自动化、工具参数契约、依赖数据绑定和逐步审批已接通。两个独立 WinForms 测试进程可以通过真实工作流编译器、调度器、ToolRuntime 和审批存储完成“读取源文本 → 审批目标写入 → 执行写入 → 回读核验”。目标内容被外部改变时，核验步骤失败，任务进入 `repair_required`。

2026-09-14 追加的真实模型验收已通过：按用户最新指示临时使用 `http://localhost:56622/v1`，模型列表中的准确 ID 为 `gpt-6-astra`。模型自行生成计划，经审批完成两个隔离 Windows 窗口之间的文本传递和精确核验。任务提供了目标选择器；这仍不代表自主发现、操作任意 Windows 应用的完整验收通过。早期 `https://lengzhehao.com/v1` 的连接失败保留为历史记录。

## 本轮确认并修复的问题

| 问题 | 修复后的行为 | 回归证据 |
| --- | --- | --- |
| COM 创建失败、工作线程未正确初始化、IUnknown 缺少明确接口 | 通过类型库创建 IUIAutomation，在持续存活的 MTA 线程查询和操作，显式获取 Value / Text / Invoke 接口 | `test_ui_automation_native_contract.py`、`test_ui_automation_windows_native.py` |
| 输入分发成功被误认为操作成功 | 不可点击的控件明确失败；原生 Invoke / SetValue 报错后不再追加另一次输入 | `test_ui_automation_native_contract.py` |
| 审批后的 UIA 状态被错误地当成文件状态比较 | 只有内置语义 UIA 工具的指定版本状态交由原生适配器核验；其他资源仍须与审批快照匹配 | `test_resource_state_approval.py`、原生审批恢复测试 |
| 同一窗口内控件替换后仍可能执行旧审批 | 在实际写入边界比对审批时的完整选择器、控件、窗口、进程与父级身份 | `test_ui_automation.py` |
| 搜索截断或控件树读取失败后误判唯一目标 | 不完整搜索不能用于唯一定位或执行写入 | `test_ui_automation.py` |
| 密码属性查询失败后继续读取文本 | 无法确认密码属性时保持文本不读 | `test_ui_automation_native_contract.py` |
| 超长文本被静默截断后复制 | 超出读取上限时失败，不将前缀内容作为完整结果 | `test_ui_automation_native_contract.py` |
| 工作流隐藏子工具执行、未注册处理器或依赖失败仍继续 | 展开成可见计划步骤，使用每个工具自己的策略、审批与取消流程；无效工作流在动作前拒绝 | `test_workflow.py`、`test_workflow_plan.py` |
| 模型重新编写从源应用读到的数据 | 上游输出通过声明的 JSON 路径绑定为具体参数，在校验与审批前完成；失败、缺失或未审核的输出不可绑定 | `test_plan_input_binding.py` |
| 模型修改核验值、目标或属性，或直接声称已完成 | 绑定数据保持不变；回读后置条件冻结整个调用；没有实际执行证据时请求修订，不报告完成 | `test_plan_input_binding.py`、`test_approval_binding.py` |
| 回读仅返回文本而没有判断是否符合预期 | `expected_value` 与回读值严格比较；不一致返回 `ui_automation_postcondition_failed`，阻止成功结论 | `test_ui_automation_tool_contract.py`、原生跨应用正反例 |

## 自动化验证

测试使用隔离临时数据目录和测试密钥，不依赖用户的任务数据库。原生测试创建自有隐藏或透明、不激活的 WinForms 窗口，不使用用户剪贴板。9 月 11 日的实际 API 探测仅请求生成计划；9 月 13 日新增的验收命令可在严格限定的自有测试窗口中继续执行有效计划，详见后文。

| 检查 | 结果 |
| --- | --- |
| 上一轮完整后端回归 | 4395 passed、17 skipped，811.43 秒；在最终边界补丁之前完成 |
| 最终 UIA、输入绑定、工作流、审批回归 | 289 passed，64.19 秒；包含 6 项真实 Windows 原生测试 |
| 本次所有改动 Python 文件的 Ruff | 通过 |
| `git diff --check` | 通过 |
| 真实 PlannerAgent 请求 | 首次返回 HTTP 521；数小时后的重试返回 HTTP 522，未取得模型响应 |

17 项跳过来自 Windows 会话缺少符号链接权限、POSIX/Unix 专属行为或平台实现差异，不能计为对应分支已验证。

原生测试使用已知测试计划，省略模型推理，保留真实编译、审批、执行和核验链路。MockProvider 固定回复未作为模型能力证据。

本地运行日志与 JUnit 结果保存在 `.tmp/audit-fixes/ai-os-final-affected-green.log` / `.xml`；上轮全量结果为 `ai-os-full-after-native.log` / `.xml`；最新模型请求结果为 `ai-os-real-planner.json`。这些临时证据不含 API 密钥。模型探测只在测试进程中覆盖模型与基址，没有改写用户持久设置。

## 剩余验收

1. 已完成下文指定真实模型的隔离跨窗口任务；后续扩展到模型自主发现目标应用与控件，及业务应用的保存等实际结果。
2. 按真实应用逐一建立任务样例并记录成功条件；目前已证明标准 WinForms 控件的执行链，并追加了下文的实际计算器操作。
3. 对没有标准 UIA 控件的界面另行验收视觉定位，以及保存、发送、提交等应用实际业务结果。

更详细的能力边界见 `ai-os-agent-capability-plan-2026-09-10.md` 的“已知范围”。

## 追加验证（2026-09-13）

本地 UIA 只读工具已启用工作代理的确定性执行路径。已确定的读取、定位与回读核验不会为同一调用再请求一次模型；失败观察仍保留既有恢复逻辑，策略与参数校验没有被跳过。保留完整 ComputerAgent 的原生回归、工作代理与工具契约测试为 50 passed。

新增 `scripts/run_windows_agent_acceptance.py` 和 `npm run eval:windows-agent`。命令从项目设置读取真实模型配置，只允许模型规划自有测试进程里的源编辑框读取与目标编辑框写入，并要求数据绑定、真实审批消费和最终精确回读。每次运行创建独立数据库；模型内容不会作为审批授权，参数与目标范围在实际工具调用前再次检查。

验收流程及原生窗口回归为 20 passed，其中包括正常 `handle_user_goal` 任务入口的原生运行、越界目标拒绝、缺失回读依赖拒绝、运行时范围重验、MockProvider 拒绝。这里的原生契约测试使用已知计划，不能计作模型规划成功。

真实命令在 2026-09-13 实跑：`gpt-6-Astra`、`https://lengzhehao.com/v1` 返回 HTTP 521，报告中的 `ok` 与 `model_plan_received` 都为 `false`，退出码为 1。证据为 `.tmp/audit-fixes/windows-agent-acceptance-2026-09-13.json`。该机器的出站 DNS 使用 TUN fake-IP，本次仅在验收进程中显式启用现有 `LENGRVIS_TRUSTED_FAKE_IP_TUN` 支持；没有修改生产 URL 校验或持久设置。

完整后端回归已结束：4448 passed、17 skipped、0 failed，831.74 秒。证据为 `.tmp/audit-fixes/ai-os-complete-backend-20260913.log` / `.xml`。该轮在后续长文本补丁前启动，不能单独证明这些补丁已通过全量回归。

### 原生长文本修复

原生探测确认：源控件实际保存 24,000 字符，ValuePattern 仅返回 4096 字符，且该 WinForms 控件没有 TextPattern。截断发生在通用结果预算之前；探测记录为 `.tmp/audit-fixes/uia-length-probe.json`。

对标准 Edit / WindowsForms10.EDIT 原生控件，现在验证 HWND 类名、进程身份和密码样式后，以 2 秒超时和有限缓冲区发送 WM_GETTEXT。非 COM 的注入适配器保留自身读取语义。达到 65,536 个 UTF-16 单元的 BMP 字符和代理对文本均能完整读取，超出时失败；读取异常不能退回已截断的 ValuePattern 前缀。相关 API 约束见 [WM_GETTEXT](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-gettext) 和 [SendMessageTimeout](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendmessagetimeoutw)。

属性工具的结果预算同时覆盖该文本上限与 JSON 转义开销，让完整值可供后续步骤绑定。正常任务入口的短文本和普通长段落均已完成源读取、目标写入审批、执行与精确回读。连续 24,000 个字母的早期测试样本触发了现有疑似凭据检测，后来改为普通段落；没有把该策略拒绝当作文本读取失败。

最终受影响回归为 **345 passed、0 skipped、0 failed**（86.40 秒），覆盖 UIA、ComputerAgent、策略快路径、输入绑定、工作流、资源状态与审批。包括 8 项原生控件测试，以及正常任务入口的 26,622 字符普通文本复制。非原生适配器被误当作 HWND 的中间回归已修复；最终全部测试通过。日志与 JUnit 为 `.tmp/audit-fixes/ai-os-final-native-and-bindings-green.log` / `.xml`，已核验 errors=0、failures=0。本次改动 Python 文件 Ruff 与 `git diff --check` 通过。

当前仍缺少成功的真实模型响应及业务应用任务验收；以上结果不改变这两项未完成状态。

### 应用启动与宿主窗口（2026-09-13）

新增回归先复现了四种失败：丰富的卸载记录覆盖启动快捷方式、实际启动和预览把安装目录当作应用、内置程序使用裸文件名。修复后清单分别保留启动入口与卸载信息；同名多入口要求精确路径或应用 ID，启动入口不会借用同名注册表记录的发布者授权。内置应用与 PowerShell 清单扫描使用 Windows 系统目录中的可执行文件。随后复现并修复了 winget 扫描的同类路径问题：解析到工作目录或其他授权工作目录的程序不会执行，也不会回退到裸程序名。

商店应用通过 Get-StartApps 的 AUMID 与当前用户包的 PackageFamilyName 对应，再调用 IApplicationActivationManager.ActivateApplication，记录该接口报告的进程 ID。该行为依据 [Microsoft 应用 ID 文档](https://learn.microsoft.com/en-us/windows/configuration/store/find-aumid) 与 [激活接口文档](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-iapplicationactivationmanager-activateapplication)。普通快捷方式仅报告启动请求；内置启动器和商店激活报告的进程都可能再转交给其他进程，后续 UI 操作需要独立检查窗口。

系统计算器的正常任务入口暴露了顶层 HWND 筛选的遗漏：应用进程存活、可见子窗口存在，但顶层窗口属于系统宿主进程。两个隔离进程的原生重挂载测试也稳定复现。现在按 PID 查找会包含目标应用位于宿主中的根子窗口，排除宿主自身和目标进程内部重复子控件，并继续在 HWND 筛选结束后才调用 COM。

| 检查 | 结果与证据 |
| --- | --- |
| 应用启动、宿主窗口修复后的完整后端回归 | 4465 passed、19 skipped，837.60 秒；`ai-os-full-after-app-launch.log` / `.xml` |
| 最后 winget 路径补丁及应用、规划、工作代理、策略回归 | 147 passed，26.52 秒；`ai-os-app-launch-final-verification.log` / `.xml` |
| 显式开启的真实计算器验收及原生控件回归 | 11 passed，32.35 秒；`ai-os-hosted-calculator-green.log` / `.xml` |
| 指定模型再次验收 | 2026-09-13 10:27 UTC，ConnectError，`model_plan_received=false`；`windows-agent-acceptance-after-app-launch.json` |

上述文件均在 `.tmp/audit-fixes/`。全量回归在最后 winget 路径补丁前启动，该补丁由后续 147 项回归覆盖；19 项跳过包含默认不开启的两项真实应用测试。这两项已单独显式执行通过，分别走直接工具调用与正常任务入口，均确认新进程和可见窗口，结束时只关闭本次新建进程。正常入口使用确定性打开应用模板，不是模型规划证据。原生窗口测试仍不能代替其他应用的输入、保存或跨应用业务验收。

开发者可以在已安装 Windows 计算器的测试机器上复现：

```powershell
$env:MAVRIS_NATIVE_APP_ACCEPTANCE = '1'
.\.venv\Scripts\python.exe -m pytest backend/tests/test_app_launch_native.py -q
```

测试使用 pytest 的临时配置和数据库；如果计算器已经运行则跳过。真实模型验收仍使用 `npm run eval:windows-agent`，当前所选接口尚未返回成功计划。

### 实际计算器操作与进程交接（2026-09-13）

新增 `calculation-and-transfer` 原生用例：通过工具执行七次按钮 Invoke，计算 37×19；读取计算器结果，要求显示值为 703，再将实际读到的完整文本绑定到隔离 WinForms 编辑框的写入参数，审批后执行并精确回读。七次按钮操作和一次写入分别生成、消费真实审批。写入审批前，目标编辑框仍保持原值；所有步骤均由实际 ComputerAgent、调度器与 ToolRuntime 执行。测试只提供已知计划，禁止鼠标或键盘全局输入回退，不计入真实模型规划验收。

| 检查 | 结果与证据 |
| --- | --- |
| 新计算与传递用例单独运行 | 1 passed，21.71 秒；`ai-os-calculator-operation-first.log` / `.xml` |
| 三项实际计算器用例与九项隔离原生控件测试 | 12 passed，49.79 秒；`ai-os-calculator-operation-native-green.log` / `.xml` |
| 应用启动、Windows 能力和 UIA 工具契约 | 77 passed，17.95 秒；`ai-os-launch-handoff-contracts.log` / `.xml` |
| 指定模型再次验收 | 2026-09-13 15:34 UTC，ConnectError，`model_plan_received=false`；`windows-agent-acceptance-after-calculator-operation.json` |

本机记事本探测同时确认：系统内置启动器及商店激活返回的进程都可能再交接给子进程。实际进程的父 PID 与本次启动记录对应，UIA 在真正拥有窗口的进程中可以读取控件；原 PID 找不到窗口并非 UIA 漏检。工具输出契约、AppAgent、ComputerAgent 与规划提示现在统一说明这一点，要求窗口发现与身份核验，避免重复启动。

现代记事本会恢复既有标签页，因此这次只读探测没有写入或保存文档，也不能作为记事本跨应用任务成功证据。后续验收须定位本次创建的临时文档。只保留去除文档名称的结构摘要 `notepad-discovery-redacted.json`，临时探测脚本与包含标签名称的日志已删除。上述临时证据均位于 `.tmp/audit-fixes/`。

本轮没有重跑完整后端；此前 4465 passed、19 skipped 的结果仅对应其启动时的代码状态。新增原生用例及本轮相关契约由上表覆盖。真实模型接口连接仍失败，整体 AI OS 目标尚未完成。

### 记事本输入、保存与文件核验（2026-09-14）

新增 `test_notepad_native.py`，只操作本次创建的临时文件和新建记事本进程。沿用真实 Orchestrator、ComputerAgent、FileAgent、ToolRuntime 与审批消费；测试提供已知计划，未使用模型生成计划。已有记事本实例时跳过，不处理用户已打开的应用。

两条路径都从自有 WinForms 窗口读取中文、emoji 和 CRLF 多行内容：第一条使用原生 ValuePattern，第二条模拟缺少 ValuePattern，先审批目标文档的 Ctrl+A，再实际发送 Windows 键盘输入。写入后核验控件文本，独立审批目标文档的 Ctrl+S，等待修改标记消失，再通过正式 `file.read_text(expected_value=源文本)` 步骤核验完整磁盘文件。文件核验保留精确换行，忽略初始 UTF-8 BOM，拒绝无效 UTF-8，并独立返回同一批读取字节的 SHA-256。成功后另经目标审批关闭测试标签页。

本机结构探测和回归测试首先发现旧 INPUT 结构在 Win64 上只有 32 字节，正确尺寸应为 40，且非 BMP 文本未按 UTF-16 代理对发送。真机键盘测试进一步复现记事本忽略 Unicode 方式发送的 CR/LF，换行需转换为单个 Enter。现已补齐联合体、固定宽度字段及 ULONG_PTR，按 UTF-16 单元发送字符，按逻辑换行发送 Enter，并保证后续按键失败时释放已按下的快捷键修饰键。相关约束见 [Microsoft INPUT](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-input)、[KEYBDINPUT](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-keybdinput) 与 [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)。

记事本 ValuePattern 会把 CRLF 转成 CR，原先完整源字符串的 text_contains 因此无法匹配。现在控件核验可显式选择 `text_comparison="normalize_newlines"`，只允许换行形式差异，仍保留原始读值；默认仍是严格 JSON 比较。保存快捷键绑定已核验的目标观察值，磁盘后置条件仍与原始源文本精确比较。核验失败时不能通过改换比较规则、路径或工具，以及工作代理声称已经完成来绕过。

快捷键现在可绑定语义控件及审批资源；在设置焦点后重新核对文档身份，再通过 GetFocusedElement / CompareElements 验证实际焦点。文字键盘回退复用了这些核对，并逐字检查焦点和取消状态。多行回退只允许 Document 控件，避免在单行字段模拟 Enter 而提交表单。焦点工具的已确定参数可直接交给工具执行，不再为了同一次切换重复请求工作代理模型；策略检查仍执行。

| 检查 | 最终结果与证据 |
| --- | --- |
| 真实记事本 ValuePattern 与键盘两条完整保存链 | 2 passed，32.91 秒；`ai-os-notepad-final-native.log` / `.xml` |
| UIA、文件工具、输入绑定、资源状态、审批、工作代理、策略、运行时与工作流 | 416 passed、1 skipped，101.43 秒；`ai-os-notepad-final-affected.log` / `.xml` |
| 本轮修改 Python 文件 Ruff 与 git diff --check | 通过 |
| 指定真实模型再次验收 | 2026-09-14 04:26 UTC，ConnectError，model_plan_received=false；`windows-agent-acceptance-after-notepad.json` |

证据位于 `.tmp/audit-fixes/`。唯一跳过项为本机不支持的 dir_fd no-follow 测试，不能计作通过。后台回归没有开启实际应用开关；两条记事本用例单独显式执行。中途另一前台应用持续接收输入，SetForegroundWindow 或控件焦点核对失败，工具停止执行；用户明确暂停操作后，最终两条链均成功，没有降低焦点条件。失败诊断只记录错误、文本长度和换行计数，不保留用户恢复标签的名称或内容。

本轮未重跑完整后端，先前的 4465 passed、19 skipped 为历史结果。本轮已证明可执行的原生任务链，但指定服务仍未返回成功计划，因此不能声称自然语言驱动的 AI OS 全链路已经完成。下一阶段是可用模型下的自主发现与任务规划，以及更多业务应用和非 UIA 界面的独立验收。

### 临时本机接口与真实模型首次完整验收（2026-09-14）

用户提供临时 API `http://localhost:56622/v1` 后，鉴权的模型列表请求成功，确认 `gpt-6-astra` 可用。验收进程使用 `provider_name=local`、`mode=efficiency`、`requires_openai_auth=true` 和 Chat Completions 协议；local 指 API 位于回环地址，不表示模型必然在本机计算。密钥由隐藏终端输入，仅进入该进程的环境与请求头，没有写入代码、文档、报告或项目持久设置。

首次真实规划请求返回 HTTP 400。最小普通对话及简单严格 schema 请求成功，实际 PLAN_SCHEMA 则被拒绝：上游以 invalid_json_schema 指出 additionalProperties 必须为 false。项目的动态 args 和 input_bindings 使用标准 JSON Schema，但不属于该服务的严格输出子集。[OpenAI 结构化输出文档](https://developers.openai.com/api/docs/guides/structured-outputs)说明严格输出要求封闭对象且所有字段均为 required，不能简单封闭动态参数对象而丢失工具实参。

修复 `OpenAICompatibleProvider` 的错误分类：auto 模式在收到明确的 invalid_json_schema 拒绝时，可以使用既有提示词结构化路径；同一服务、同一模型生成的结果仍必须通过原始 JSON Schema 校验，随后经过计划绑定、工具参数、策略和审批检查。显式 native 模式仍返回失败。回归同时覆盖直接错误、代理封装的错误和不合格的生成结果，后者继续以 schema_mismatch 拒绝。

修复后，模型首次已生成正确计划并读到源内容，但样本中的 24 字符随机串触发了凭据保护。将测试数据改为带短编号的普通中文段落、CRLF 与 emoji，并新增样本与既有敏感值策略兼容的检查；没有修改敏感输入策略。验收失败现在仅对程序自身静态检查输出 failed_check，其他异常仍只输出类型和 HTTP 状态，不记录服务响应正文。

| 检查 | 结果与证据 |
| --- | --- |
| 首次原生 schema 请求 | HTTP 400，model_plan_received=false；`windows-agent-acceptance-local-20260914.json` |
| schema 兼容修复后的第一次运行 | model_plan_received=true；写入被样本凭据形态保护拦截；`windows-agent-acceptance-local-schema-fixed.json` |
| 最终真实模型与 Windows 跨窗口链路 | **通过，31.66 秒**；task_phase=completed、model_plan_received=true、approval_consumed=true、destination_verified=true；`windows-agent-acceptance-local-final.json` |
| 模型传输、路由、SSRF、原生验收及输入绑定回归 | 160 passed、4 skipped，20.46 秒；`ai-os-local-model-final-regression.log` / `.xml` |
| 修改文件 Ruff 与 git diff --check | 通过 |

最终模型生成三个步骤：源窗口 get_property、目标窗口 type_text、目标窗口 get_property。写入和期望读值均通过 input_bindings 使用实际源观察；审批前目标仍保持原值，审批消费后读取原生目标值，与完整源文本相等。没有提供已知计划或使用 MockProvider。这些证据均位于 `.tmp/audit-fixes/`；四项跳过均来自本机缺少创建符号链接的权限。

本次临时接口解除了隔离场景的真实模型验收阻塞。下一步仍需分别验证自主发现应用、模型规划的记事本保存，以及 Office、Electron、WPF 和视觉操作任务。
