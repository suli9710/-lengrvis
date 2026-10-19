# Windows AI OS 能力增强计划

用户优先场景：Windows 应用操作与跨应用任务。保留既有审查修复与审批边界，按可复现的能力增量推进。

## 1. 原生自动化基础

- [x] 修复 UI Automation COM 创建失败：本机复现旧 ProgID 返回无效类字符串，类型库 + IUIAutomation 可成功创建。
- [x] 让 COM 创建、查询、控件操作在持续存活的 MTA 工作线程执行，避免未初始化或已退出的 apartment。
- [x] 将 GetCurrentPattern 返回的 IUnknown 转成明确的 Invoke / Value / Text 接口。
- [x] 读取编辑框与文档文本，保护密码控件；不能实际点击时明确失败。
- [x] 修复标准 WinForms 编辑框的 ValuePattern 只返回 4096 字符的问题；按原生控件类型与进程身份检查后，使用有时限、有限长度的 WM_GETTEXT 完整读取，并验证 UTF-16 上限。
- [x] 在隔离的 Windows 原生控件测试程序上验证读取、输入、点击和跨窗口数据传递。
- [x] 支持由其他进程承载的应用窗口；只读取目标进程自己的 HWND 子树，并用系统计算器复现与验证。
- [x] 修正 Windows SendInput 的 INPUT 联合体尺寸、UTF-16 代理对、换行事件与异常后的修饰键释放；中文、emoji、多行内容经过记事本键盘输入和落盘验收。

## 2. 模型可调用的桌面工具

- [x] 为全部桌面工具提供参数、范围、必填项和返回值契约。
- [x] 保持旧参数别名兼容，在运行边界验证参数，给出可采取的恢复动作。
- [x] 为观察、定位、输入后的结果核验提供明确引导，避免把发送动作等同于完成任务。
- [x] 已确定参数的本地 UIA 只读步骤直接进入既有工具运行流程，不再多调用一次工作代理模型；参数校验与策略检查仍由 ToolRuntime 执行。
- [x] 应用清单保留独立启动入口和卸载信息，可按名称、路径或应用 ID 过滤；多入口不猜选，启动时按入口来源重新检查授权。
- [x] 内置应用使用 Windows 系统目录中的程序；清单扫描不从工作目录或其他授权工作目录执行 winget。
- [x] 按注册的应用 ID 激活商店应用并返回激活接口报告的进程 ID；启动请求与窗口就绪分别描述，进程仍可能交接给另一个进程。
- [x] 快捷键支持目标文档选择器、审批资源绑定及执行时焦点核对；键盘文字回退在焦点设置后重新核对文档，并逐字检查焦点和取消状态。
- [x] 文本后置条件保留默认精确比较，对会转换换行的控件提供显式换行比较；原始观察值不改写。
- [x] FileAgent 的 file.read_text 可核验完整 UTF-8 保存内容；截断预览不影响完整比较，工作代理不得跳过、篡改核验调用或更换目标路径。

## 3. 跨应用执行链

- [x] 修复工作流运行时未注册处理器、依赖失败仍执行、焦点失败仍继续的问题。
- [x] 明确步骤输入与上游输出之间的数据引用，拒绝缺失或未成功的依赖。
- [x] 将工作流步骤接入既有 ToolRuntime 的策略、审批、审计与取消流程；父工作流审批不得冒充子工具审批。
- [x] 用两个独立应用窗口验证读取源内容、写入目标控件、读回核验的完整链路。
- [x] 普通长段落通过正常任务入口完成审批与跨进程复制；属性读取结果预算覆盖支持的文本长度，供后续步骤直接绑定。
- [x] 实际计算器通过逐步审批完成 37×19，读取显示结果，再经审批传到隔离编辑框并精确回读。
- [x] 实际记事本分别通过 ValuePattern 与键盘回退接收跨应用文本，经过独立审批保存，并在正式任务中精确核验磁盘文件；关闭的仅为自有测试标签页。

## 4. 完成标准

- [x] 聚焦回归、受影响模块检查通过，并保留 Windows 原生运行证据。
- [x] 跨应用失败能定位到步骤并停止依赖动作；不会报告未实际执行的成功。
- [x] 用真实规划模型验证自然语言任务：2026-09-14 经临时本机接口完成隔离窗口的读取、绑定、审批写入和精确核验；目标选择器由验收任务提供，尚不代表自主发现任意应用。
- [x] 提供可重复运行的 `npm run eval:windows-agent`，真实模型失败与隔离原生场景成功分别记录，失败不降级成 MockProvider。
- [x] 记录尚不支持的应用控件、需要视觉回退的情形与未验证范围。

## 技术依据

- [Microsoft：创建 CUIAutomation](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-creatingcuiautomation)
- [Microsoft：UI Automation 线程模型](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-threading)
- [Microsoft：GetCurrentPattern 返回 IUnknown](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getcurrentpattern)
- [Microsoft：WM_GETTEXT 的缓冲区与返回长度](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-gettext)
- [Microsoft：SendMessageTimeout 的超时和跨进程系统消息](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendmessagetimeoutw)
- [Microsoft：通过 Get-StartApps 查找应用 ID](https://learn.microsoft.com/en-us/windows/configuration/store/find-aumid)
- [Microsoft：ActivateApplication 返回目标应用进程](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-iapplicationactivationmanager-activateapplication)
- [Microsoft：CreateProcess 的程序搜索顺序](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw)

## 当前证据（截至 2026-09-14）

- `backend/tests/test_ui_automation_windows_native.py`：Windows 原生测试 6 passed。测试创建两个独立、透明且不使用用户剪贴板的 WinForms 进程，覆盖 MTA 重入、Unicode ValuePattern 传递、Invoke 点击、按 PID + accessibility name 定位、真实 ToolRuntime 审批恢复，以及目标内容被外部改变后的 `repair_required` 结果。
- `backend/tests/test_ui_automation_tool_contract.py`：UIA 参数契约与读回后置条件 20 passed；`expected_value` 使用严格 JSON 比较，读回不一致返回 `ui_automation_postcondition_failed`。
- 上一轮完整后端回归为 4395 passed、17 skipped；后续审批与核验边界修复的最终结果记录在同目录的 `ai-os-agent-capability-verification-2026-09-11.md`。
- 早期模型验收：按用户指定的 `gpt-6-Astra`、`https://lengzhehao.com/v1` 发起真实 PlannerAgent 请求，首次返回 HTTP 521，数小时后的重试返回 HTTP 522。没有将 MockProvider 或失败请求计入真实规划模型证据。
- 2026-09-13：新验收命令走正常任务入口实测，上游返回 HTTP 521，`model_plan_received=false`，命令退出码为 1；没有执行目标写入。常规原生回归已保留完整 ComputerAgent。
- 2026-09-13 完整后端回归为 4448 passed、17 skipped（831.74 秒）。此轮在长文本补丁前启动，后续原生读取与参数绑定回归另行记录在验收文档中。
- 2026-09-13 应用启动与宿主窗口修复后，全量回归为 4465 passed、19 skipped（837.60 秒）；最后的 winget 路径补丁另经 147 项相关回归验证。显式开启的真实计算器验收与原生控件测试为 11 passed，包含正常任务入口。指定模型再次验收时发生 ConnectError，仍未收到计划。
- 2026-09-13 追加实际计算器操作后，原生验收为 12 passed（49.79 秒），相关工具契约回归为 77 passed（17.95 秒）。新用例执行七次按钮操作和一次目标写入，各自消费真实审批；703 的显示文本经数据绑定传到另一进程并精确回读。15:34 UTC 再次调用指定模型仍发生 ConnectError。
- 2026-09-14 记事本两条输入路径的最终真机验收为 2 passed（32.91 秒）；本轮 UIA、文件、输入绑定、策略、审批和工作流回归为 416 passed、1 skipped（101.43 秒）。中途前台被其他应用占用时，焦点检查拒绝输入；用户腾出桌面后完成最终验收。04:26 UTC 指定模型再次连接失败，model_plan_received=false。
- 2026-09-14 04:53 UTC，按用户最新指示在验收进程中临时使用 `http://localhost:56622/v1`，模型列表确认实际 ID 为 `gpt-6-astra`。真实模型规划与原生跨窗口执行完成，model_plan_received=true、approval_consumed=true、destination_verified=true，31.66 秒。模型传输、验收和绑定回归为 160 passed、4 skipped（20.46 秒）。过程修复了严格 JSON Schema 拒绝未触发 auto 模式兼容路径的问题，并把形似凭据的验收随机串改为短编号的普通文本。

## 已知范围

- 原生运行证据包括两个独立 WinForms 进程的文本传递、已安装计算器的计算与跨进程传递，以及现代记事本的文本输入、保存与文件核验。计算器正常任务入口使用既有确定性“打开应用”模板；其余原生业务用例提供已知计划，保留真实工作代理、审批、执行和核验。WPF、Electron、Office 和其他业务应用尚未逐一完成实际任务验收。
- 本机现代记事本会交接进程并恢复既有标签页。验收通过新建进程、窗口及唯一临时文档内容共同限定范围；源内容来自测试自有 WinForms 控件，不读取或修改恢复的用户文档。
- 已验证标准 WinForms 编辑框的完整原生文本读取与 ValuePattern 写入，以及按钮的 InvokePattern；TextPattern 分支有接口契约测试。自绘界面、画布、远程会话及未暴露可访问性信息的控件仍需其他适配或视觉定位；这些回退未包含在本轮原生跨应用验收内。
- ValuePattern 写入替换编辑值；键盘回退在当前选区插入，需要另行审批的选区操作。已验证记事本文本保存；富文本格式、附件、发送消息与提交表单未验收。为避免 Enter 提交单行字段，多行键盘回退仅支持 Document 控件。
- Windows 前台切换被拒绝或焦点不匹配时停止输入，不强行抢占正在使用的窗口。文字可能已部分输入，恢复前须重新观察，不能直接重放。
- 文本读取最多支持 65,536 个 UTF-16 单元，超出时明确失败，不能静默截断后声称复制完成。长文档应使用文档或文件工具。
- 控件树读取失败、遍历预算耗尽或目标身份改变时停止操作并要求重新定位。原生 COM 调用本身不能被安全强制中止；工具超时不能解释为未发生任何外部动作，也不能直接重放写入。

## 下一阶段

1. 验证模型自主发现应用和目标控件，再基于观察生成动态步骤；当前真实模型用例的目标选择器由任务明确提供。
2. 把真实模型规划验收扩展到记事本保存等业务结果，沿用已验证的审批、焦点和文件后置条件。
3. 分别验收 Office、Electron、WPF 与无标准 UIA 控件的视觉操作路径；按实际任务完成率记录，避免用隔离控件通过代替通用 AI OS 能力。
