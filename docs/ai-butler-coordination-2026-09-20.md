# 管家事项统筹

本轮在原有事项、执行引擎、授权和通知上增加统筹能力，没有引入第二套执行器或外部账号接入。

## 使用

在管家概览点击“开启事项统筹”。概览增加“接下来处理”“等待前置事项”“需要你决定”；事项卡的“调整事项安排”可以选择前置事项、明确优先级与截止时间。

开启时可直接说“完成汇总资料后再生成报告”。明确匹配现有事项后保存关系；多项匹配时一次选择前置和后续事项，没有匹配的事项按这句话新建。其他表达继续使用原有对话规划流程，模型推断的关系仅作为建议，不能自动写入依赖。

关闭统筹后，已保存的前置关系仍然约束启动，停止统筹排序与通知合并。当前发布默认仍关闭：真实模型综合验收尚未通过，默认开启作为剩余发布门槛，没有改动用户已有设置。

## 行为与接口

- 事项 JSON 新增默认空的 `depends_on`、依赖成果修订快照、明确优先级标记及待汇总通知。旧数据按默认值加载，不批量重写已有签名记录。
- SQLite 同一写事务内验证存在性、自依赖、循环和版本；首次进入规划及首次绑定执行记录都会检查前置事项。已执行事项禁止调整依赖。
- 前置事项必须完成并保存核验证据；暂停、失败、取消、撤销均不放行。每 30 秒对被引用的已完成执行成果作只读检查；成果变化提示核对，下游已有动作不自动回滚或重放。
- 可启动事项按逾期、一小时内到期、明确优先级、请求来源和创建时间排序。前置事项继承下游紧迫程度。统筹时同时规划/运行最多两个事项，后台仍最多两个，桌面动作沿用原资源互斥。
- 偏好复用 MemoryAgent；规划与开始执行时重新读取已确认偏好。明确请求优先，偏好不能扩大权限，实质冲突由规划器合并询问。
- 普通通知从第一条起合并 60 秒；补充信息、审批、异常及临期提醒立即发送。摘要和待通知记录的消费在同一事务持久化，重启后只补一份积压摘要。即时提醒的消息与去重标记也在同一事务保存，进程在实时推送前退出时仍能从消息记录读取。事项和消息均沿用原隐私清理。
- 新增 `POST /api/butler/matters/{id}/arrangement`，请求包含 `version` 以及可选的 `depends_on`、`priority`、`due_at`。日期须带时区，空串用于清除。过期版本返回 409；非法关系返回 400。Electron 通过独立 `butlerArrangement` IPC 调用。
- `/api/butler/control` 增加 `coordination_enable` / `coordination_disable`；概览返回 `coordination_enabled` 和每个事项的只读 `coordination` 投影。

## 验证记录

只新增三组定向测试，放在已有的 `test_butler_coordinator_extra.py`：依赖接续与成果失效、排序与关系选择及偏好遗忘、通知恢复与隐私清理。覆盖新 HTTP 接口的版本冲突和概览投影。

- 受影响后端测试：87 passed。
- 桌面现有测试：469 passed、1 skipped；两套 TypeScript 类型检查通过。
- 渲染器生产构建、包体积检查及 Electron 开发编译通过；本轮 Python 文件 Ruff 通过。
- 真实 Chromium 组件验收：三组区域、版本化安排保存、关闭后保留依赖显示通过；1280 和 1100 宽桌面视口无横向溢出，无页面异常。390 宽探测超出已有桌面 1080px 最小宽度，不宣称手机布局已验收。
- 新 IPC 校验：合法安排通过，越界优先级、无时区日期和未知字段被拒绝。

证据在 `.tmp/audit-fixes/butler-coordination-*.png` 和 `butler-coordination-real-model.json`。

## 真实模型验收：未通过

新增可重复入口：

```powershell
.venv/Scripts/python.exe scripts/run_butler_coordination_acceptance.py --timeout-seconds 60
```

验收使用临时数据库和自有资料，在两个独立 Python 后端工作进程间恢复状态：前置汇总暂停、后续报告等待、插入独立紧急事项、退出、重启后恢复前置事项。使用原模型规划、引擎、工具和策略，不注入固定规划器、不自动救场；记录必要确认、成果核验、源文件修改、重复动作和模型用量。

2026-09-20 实跑结果：两个进程均执行，暂停与依赖保持；模型在出站 URL 校验阶段被拒绝（`ValueError`，`outbound_url.py:validate_outbound_http_url`），模型调用 0、运行记录 0、源文件未改变。未放宽现有网络校验，也未改动用户模型配置。真实任务完成率及默认开启门槛尚未达成；需要有效且符合现有出站策略的模型配置后重新运行，不以单元测试替代真实模型验收。

### 2026-09-21 继续排查

当前有效配置为 `openai_compatible`、`http://127.0.0.1:8000`、`gpt-4o-mini`。云提供器不能连接回环地址；该端口当前也没有监听服务。此前文档中的 `localhost:56622` 同样返回连接拒绝，尚不能完成真实模型验收。

验收入口新增 `--preflight-only`、`--provider`、`--base-url`、`--model`、`--wire-api` 和 `--prompt-key`。预检查沿用出站校验并检查连接，不发送凭据或模型请求；通过预检查不会被记为模型验收通过。临时参数只进入验收进程，不写回项目设置。`--prompt-key` 仅允许交互终端隐藏输入，也可沿用项目配置或 `LENGRVIS_API_KEY` 环境变量。

```powershell
# 先检查当前配置
.venv/Scripts/python.exe scripts/run_butler_coordination_acceptance.py --preflight-only

# 仅在对应模型服务已启动且凭据就绪后，临时指定服务进行完整验收
.venv/Scripts/python.exe scripts/run_butler_coordination_acceptance.py --provider local --base-url http://localhost:56622/v1 --model gpt-6-astra --prompt-key --timeout-seconds 180
```

验收通过条件补充为：前置核验后才有下游执行记录、紧急文件内容正确、源文件不变、无重复写入、重启消费积压摘要且再次恢复不重复通知、有真实模型用量。报告记录补充输入及审批次数。已有的显式 TUN fake-IP 环境设置会传入子进程，不自动启用该设置。

这次修复了即时提醒在“标记已通知”与“消息写入”之间退出可能丢失的问题，只扩展原有恢复与提醒测试组，验证实时推送中断后消息仍持久存在、重启不重复、隐私清理覆盖消息。受影响后端测试再次通过：87 passed（20.13 秒）；相关 Python Ruff 通过。另做了 5 项配置检查，未发送模型请求。本次没有修改桌面代码，沿用上一轮桌面验证记录。

预检查证据：`.tmp/audit-fixes/butler-coordination-preflight-2026-09-21.json` 与 `butler-coordination-local-preflight-2026-09-21.json`。两份报告均明确 `acceptance_completed=false`；默认开关仍关闭，等待可用模型服务。
