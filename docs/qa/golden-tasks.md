# 黄金任务回归与结果质量基线（Golden Tasks）

Last reviewed: 2026-06-10

本文档对应市场化落地清单中的两项 P0：

- **真实端到端回归套件（黄金任务集）**：≥30 条黄金任务的 E2E 回归纳入发布门禁，断言关键产物而非仅返回码。
- **自然语言任务结果质量验收基线**：20-30 个高频真实任务由真人逐条评分（成功率 / 可读性 / 返工率）。

两者共享同一份数据集，但证据等级不同：前者是机器自证回归，后者必须由真人签收。

## 数据集

- 位置：`test_data/golden_tasks/golden_tasks.json`
- 规模：≥30 条（由 `test_golden_dataset_integrity` 守护：数量、ID 唯一性、类别覆盖）
- 类别：`system`（系统检查/配置）、`cleanup`（整理/清理预览）、`approval`（删除/整理/卸载的审批边界）、`safety`（R4 禁区与越权路径）、`file`（搜索/查重/元数据）、`document`（摘要/问答/抽取）、`chat`（对话委派与闲聊边界）、`files_api`（文件搜索 API）

每条任务声明入口（`runs` / `chat` / `files_api` / `tool`）、自然语言输入与**关键产物断言**：

- 终态相位（completed / awaiting_approval / denied / cancelled / failed）
- 计划工具序列与全局风险等级（R0-R4）
- 审批数量（修改类操作必须先停在审批；只读任务必须零审批）
- 文件副作用（审批前/拒绝后文件必须存在；批准后才允许移入回收站）
- 工具输出结构（诊断字段、重复文件组、摘要 note=extractive_fallback、引用数 ≥1 等）
- 安全边界（R4 禁区 denied；越权路径抛 `SecurityError`；dry-run 不落盘）

## 如何运行

```powershell
# 作为发布门禁的一部分（已自动包含在 backend pytest / qa:gate 中）
python -m pytest backend/tests/test_golden_tasks.py -q

# 单命令门禁 + 通过率报告（默认阈值 95%）
npm run golden:gate
# 报告输出：.tmp/qa-evidence/golden-tasks/golden-tasks-report.json
```

`backend/tests/test_golden_tasks.py` 位于 `backend/tests` 下，因此 `npm run qa:gate` / `npm run release:check` 会自动执行黄金任务回归；任一任务失败会阻断发布门禁。

## 证据边界（必须遵守的口径）

- 本套件使用 MockProvider / 确定性规划器 / extractive fallback 离线运行。它证明的是**编排、路由、风险分级、审批与脱敏契约**在版本间不回归，**不证明**真实 LLM 下的结果质量。
- 机器通过率 ≥95% 只是发布门禁的机器自证部分；不能写成"结果质量已验收"。
- 真人结果质量基线仍需：对数据集中的高频任务（或在真实 LLM 配置下重放同样的输入）逐条记录 成功率 / 可读性 / 是否需返工，目标：综合成功率 ≥90%、需返工比例 ≤10%，结果报告归档并签字。评审打包入口：`npm run evidence:result-quality-review`。
- 不要把本套件结果写成 clean-machine 验收、真机验收或 RC sign-off。

## 如何新增黄金任务

1. 在 `golden_tasks.json` 追加条目（保持 ID 唯一；优先复用既有 `expect` 断言原语）。
2. 新任务必须离线确定性可跑；依赖真实 LLM 的任务放入真人评审清单而不是机器套件。
3. 运行 `npm run golden:gate` 确认通过率与报告。
4. 修改安全/审批类别的预期前，先确认是产品行为变更而非把缺陷固化成预期。

## 已由本套件锁定的产品行为（节选）

- 自然语言"检查这台电脑"在 runs / chat 两个入口都走只读 `system.diagnostics`，零审批。
- 宽泛清理请求生成只读清理预览（`file.cleanup_plan`），未授权目录时安全降级为说明，不把自然语言当路径删除。
- 明确路径删除停在 `awaiting_approval`；拒绝→任务取消且文件保留；批准→文件移入回收站。
- 读取密码 / 导出 cookie / 下单支付等 R4 意图在 goal 审查即 `denied`，零工具执行（含本轮修复：goal 级拒绝此前在 run 相位上显示为 `cancelled`，已修正为 `denied`）。
- 越权路径与路径穿越统一抛 `SecurityError`；`dry_run` 的写/删操作不落盘。
