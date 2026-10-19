# Lengrvis 审查缺陷修复记录

日期：2026-10-10（Asia/Shanghai）。对应 [项目审查报告](project-review-2026-10-10.md) 的 F01、F02、F03。

本轮在现有工作区上增量修复，保留原有已修改和未跟踪文件；没有提交或发布。原审查报告继续保留为修复前证据。

## F01：绑定请求、任务和事项的隐私模式

- `get_settings_for_mode` 构造独立 settings，不修改全局配置。主管、子 Agent、planner、Butler 规划与自动记忆采用该接口。
- MemoryAgent 读写和 lesson 保存支持显式 `mode`，已有任务仍采用保存的任务模式；管家尚未创建任务时传入事项模式。
- 嵌入上传判定采用实际路由 provider 的 profile，避免自动探测的本地模型被包装器的云端配置误分类。本地不可用仍采用既有本地哈希降级。
- 视觉描述、图片比较、扫描 PDF/OCR 提取和屏幕定位传入执行上下文的 settings。常驻屏幕监控随当前全局模式切换，任务或显式注入的配置保持独立。
- CUA 模型解析、探测和发送边界拒绝 privacy 调用。开发 CLI 的 privacy 模式要求显式本地 OpenAI-compatible 地址，云端、空地址或非法地址在 CLI 探测或启动前拒绝。普通 provider 路由仍支持既有本地探测。

回归使用虚构内容和占位配置，在实际 provider 方法或子进程边界拦截，未向真实云端模型发送内容。覆盖全局 efficiency 与请求 privacy 不一致、并行模式隔离、在途任务与全局切换、本地不可用、无 task_id 的偏好读写及工具的间接模型调用。

## F02：跨 effect 生命周期管理刷新与存储

- 新增 `mobile/src/sessionManager.ts`，同一会话共享刷新 Promise，覆盖网络请求直到新令牌持久化完成。
- RootLayout 的恢复与定时刷新接入同一管理器；配对保存和退出清理同步递增会话代次，防止旧成功响应或旧 401 覆盖、清理新配对。
- 存储清理等待全部 native/存储删除结束后再抛错，避免某一项早期失败释放队列后，晚到删除清掉新配对。
- 保留服务端 token family 重放检测与撤销机制。

新增 11 个确定性竞态场景，执行真实布局 effect、真实 API client 与 auth 存储逻辑，覆盖网络/写入延迟、前后台切换、退出、重新配对、失败写入和失败清理。未执行 Android 真机生命周期验收。

## F03：按交付契约判定 Office 完成

- 非空 `artifact_kinds` 主导初始输出要求，读取、引用或打开的来源格式不再追加为交付物。
- 中英文生成、转换和来源表达区分输入与输出；后续明确追加、替换、排除和只保留一种格式仍可更新契约。
- 完成证据继续验证文件完整性，打开要求单独处理，明确“不打开”仍生效。

完成条件测试共 76 项，覆盖 Excel 输入到 Word 输出、反向转换、双输出、仅打开来源、来源定语、口语“写一份”、只输出/只保留、后续替换和不打开。相关真实文档保存及完整性测试已通过，未执行 Office 原生应用打开验收。

## 最终验证

| 检查 | 最终结果 | 证据 |
| --- | --- | --- |
| 后端完整 pytest | 一次完整运行 4,775 passed、22 skipped，零失败；耗时 16 分 57 秒 | [日志](../.tmp/project-review-fixes-2026-10-10/backend-pytest-final.log)、[JUnit](../.tmp/project-review-fixes-2026-10-10/backend-pytest-final.xml) |
| 后端全树 Ruff | 通过 | [日志](../.tmp/project-review-fixes-2026-10-10/backend-ruff.log) |
| 本轮 25 个后端 Python 文件格式 | 通过 | [日志](../.tmp/project-review-fixes-2026-10-10/backend-format.log) |
| 手机 typecheck | 通过 | [日志](../.tmp/project-review-fixes-2026-10-10/mobile-typecheck.log) |
| 手机全部 15 组 smoke | 最后存储修补后全部通过；含 11 个会话竞态场景 | [生命周期](../.tmp/project-review-fixes-2026-10-10/smoke-session-lifecycle.log)、[token](../.tmp/project-review-fixes-2026-10-10/smoke-token.log)；同目录保留其他 13 组日志 |

修补过程中的提前停止运行不计入最终通过结论。最终 pytest 使用独立的系统 TEMP 目录并将 venv 加入 PATH，避免原报告已经确认的工作区内 basetemp 路径断言误报。

本轮 31 个源码和测试文件的 SHA-256 记录在 [源文件清单](../.tmp/project-review-fixes-2026-10-10/source-sha256.json)。全量运行结束后再次核对，31 个文件全部一致。

22 个跳过项来自未启用的已安装 Windows 应用原生验收、当前会话的符号链接权限限制，以及 Windows 不适用的 POSIX/非 DPAPI 分支。跳过项不计作通过。本轮没有重新计算行或分支覆盖率。

## 发布边界

本次代码修复和本机回归不能替代原报告列出的 7 项发布证据。未更新发布条目为 passed，未冻结或签署候选版本，未进行真实 LLM 质量评审、Android 真机、Office 原生打开或干净机器安装验收。本轮没有重新进行依赖在线漏洞审计。
