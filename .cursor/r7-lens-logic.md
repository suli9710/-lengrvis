# Round 7 逻辑正确性审计报告（mavris）

## 第一部分:失败测试根因分析 — `test_executable_turn_uses_local_provider_when_available`

### 被测链路还原(精确到行)

1. 入口 `handle_chat(r"open C:\Temp\report.txt", "privacy")`。Mock Supervisor 对这条英文消息**所有委派规则都不命中**(`mock_provider.py:236-271` 的规则全是中文关键词组合),返回 `delegate=False`。
2. 随后 `task_service.py:76-81` 的显式路径覆盖生效:`PATH_ACTION_RE` 匹配到 `C:\Temp\report.txt`,`"open"` 在 `FILE_ACTION_TERMS`(`task_service.py:38`),强制 `agent_hint="FileAgent"`。**hint 来源是确定的,不存在 hint 漂移**。
3. 进入 `planning_handler._create_plan`,hint=FileAgent 触发工具面收窄(`planning_handler.py:119-126`,只留 `agent_owner=="FileAgent"` 的工具 + `tool.search`),并设定 `attempts = 2`(`planning_handler.py:132`)。

### 两次调用的精确根因

**根因在 `planning_handler.py:133-171` 的 hint-retry 循环,但它不是"误判合法 plan",而是两层问题叠加:**

```132:146:backend/app/orchestration/handlers/planning_handler.py
        attempts = 2 if hint else 1
        for attempt in range(attempts):
            try:
                plan = await orchestrator.planner.create_plan(
                    task.id,
                    goal,
                    mode,
                    tools,
                    memory_context=memory_context,
                    perception_context=perception_context,
                    goal_context=goal_context,
                    session_context=session_context,
                    tool_specs=tool_specs,
                    agent_hint=agent_hint,
                )
```

- **触发条件**:第一次 plan 命中 `plan_tools_outside_visible`(`planning_handler.py:163`)即重试。`calls==2` 那次运行中,`RecordingPlanProvider` 当时的 payload 所产出的工具不在 FileAgent 收窄面内(R6-P3 报告自述"已把 payload 改为 `FileAgent + file.search_by_name(dry_run)` 对齐工具面",反推修复前 payload 越界),第一次校验失败 → 第二次调用 → 共 2 次 → strip 后走 `_guard_supervisor_hint_plan`。
- **设计缺陷(真正值得修的产品问题)**:重试时 `create_plan` 的**全部入参与第一次完全相同**(`planning_handler.py:135-146`),`last_outside` 没有任何形式回灌给 planner/provider。对确定性 provider(Mock/RecordingPlanProvider)和温度 0 的真实 LLM,第二次必然返回同一个 plan——**这次 retry 是纯浪费的重复 LLM 调用**,只产生审计噪音(`planner.supervisor_hint_retry`,`planning_handler.py:166-171`)。

### 当前代码静态推演:应该是 1 次

现在的 payload(`test_supervisor_chat_flow.py:33-47`)返回 `file.search_by_name`,该工具 `agent_owner="FileAgent"`(`file_tools.py:781`)、非 defer_loading、在 `file.` 内建命名空间内,**首轮即通过 `planning_handler.py:163-164` 两项校验并 break,calls==1**。所以"hint-retry 误判合法 plan"假说对当前 payload 不成立;历史上的 2 次是 retry **正确地**拦住了当时不合法的 mock payload。

### 修复建议(测试 + 产品各一)

- **改测试**(`test_supervisor_chat_flow.py:242`):恢复 `assert provider.calls == 1`。现在的 `1 <= provider.calls <= 2` 是遮蔽性放宽——下界由"任务能 completed"保证,上界把"无效重试又触发了"合法化,**该断言抓不住 retry 回归**。
- **改产品**(`planning_handler.py:133-146`):第二次尝试时把 `last_outside` 回灌(如注入 `goal_context["plan_violation"]` 或在 planner prompt 中追加"以下工具越界: ..."),否则 retry 没有改变结果的可能;或对确定性 provider 直接跳过 retry。

---

## 第二部分:其他逻辑发现清单

| # | 严重度 | 位置 | 问题 | 短证据 | 建议 |
|---|--------|------|------|--------|------|
| 1 | **中** | `planning_handler.py:133-146` | hint-retry 无反馈回灌,第二次调用与第一次入参完全相同,对确定性 provider 恒等重复 | `create_plan(...)` 两轮参数逐项一致,`last_outside` 仅用于事件记录 | 回灌越界信息或跳过无意义 retry(同第一部分) |
| 2 | **中** | `planning_handler.py:258-261` vs `delegation_metadata.py:28` | 空 plan 语义冲突:matcher 认为空 plan 匹配 hint(返回 True),guard 却对空 plan 直接抛 `SupervisorHintPlanError`——带 hint 的合法"无操作/澄清式" plan 必然硬失败 | `if not hint or not plan.steps: return True` vs `if not plan.steps ... raise` | 统一语义:空 plan 应走澄清/会话回复路径,而不是 hint 错误 |
| 3 | **中** | `task_phase.py:20` | `CREATED` 缺少 `→ FAILED` 迁移。任务壳建好后、首次进入 PLANNING 前若抛异常(`task_service.py:188-191` 的 catch 会 `safe_transition(FAILED)`),非严格模式下仅审计不落库 → **僵尸任务永远停在 created** | `TaskPhase.CREATED: {GOAL_ANALYSIS, PLANNING, CANCELLED}` 无 FAILED | 在迁移表中给 CREATED 增加 FAILED |
| 4 | 中低 | `delegation_metadata.py:69-74` | 中英文关键词不对称:第 73 行 FileAgent 兜底只有中文动词,英文 `delete/remove/copy/move/open` 全部缺失——`POST /runs` 旁路下 "delete C:\tmp\x" 推断为空 hint,而「删除」能路由 FileAgent;另外 57-60 行浏览器关键词优先级压过卸载/文件意图(「打开浏览器卸载应用」→ BrowserAgent) | `("清理", "删除", "移动", "复制", "文件", ...)` 无英文项 | 补齐英文动词;对混合意图按"动作宾语"而非首个命中关键词路由 |
| 5 | 中低 | `mock_provider.py:68-77` + `335-342` | `_build_goal_plan` 关键词优先级与 hint 路由冲突:「整理电脑上的发票」中「电脑」(line 68)先于「整理/发票」(line 73)命中 → goal plan 是 `system.get_info`/ComputerAgent;若 hint=FileAgent 则 agent 不匹配,`_plan_from_supervisor_hint` 退化为通用 `file.search_by_name`,**丢失 organize 的 preview+审批语义**,与 `_build_goal_plan` 的意图分类不一致 | 分支顺序 system(68) > organize(73);hint fallback 固定 search_by_name | `_build_goal_plan` 把 organize/发票分支提到 system 之前,或 hint fallback 复用意图分类 |
| 6 | 低 | `planning_handler.py:164` + `delegation_metadata.py:26-32` | `plan_matches_supervisor_hint` 在 `not last_outside` 之后**恒真**:函数体只重查同一个 `plan_tools_outside_visible`,从不校验 `step.agent_name` 与 hint 的关系;传 `visible_tool_names=None` 时无条件 True | 函数仅有 surface 复查,无 agent 维度检查 | 要么删掉冗余调用,要么让 matcher 真正校验 agent 归属 |
| 7 | 低 | `state_machine.py:57-62` | `is_transition_allowed` 的 `strict` 参数声明后从未使用;同 phase 一律放行、不查 stage 规则,与 `transition()`(77-82 行查 stage)行为不一致,用它做预检会得到假阳性 | `*, strict: bool = False` 函数体无引用 | 删除死参数或对齐 stage 校验 |
| 8 | 低 | `state_machine.py:38-41` | `_phase_of` 对未知 status 文本抛 `StateTransitionError`,发生在 strict 判定之前——`safe_transition(strict=False)` 仍可能抛异常,违背非严格模式"只审计不抛"的契约 | `except ValueError as exc: raise StateTransitionError(...)` | 非严格路径下对未知状态降级为审计事件 |
| 9 | 低 | `mock_provider.py:311-318` | hint=DocumentAgent 的 fallback args 只有 `{"dry_run": True}`,无任何文档路径/目标,与 `_args_for_tool`(113-119 行,总是带 `query`)不一致,hinted 文档 plan 在执行期大概率缺参失败 | `args={"dry_run": True}` | 与 `_args_for_tool` 统一,至少带 query/path |
| 10 | 低 | `test_golden_tasks.py:299-313` | `_assert_output_spec` 对未知 spec 键静默跳过:dataset 里把 `"equals"` 写成 `"equal"` 等 typo 会使该条检查**整体变 no-op**,无 spec 键白名单校验 | 五个 `if "xxx" in spec` 之外的键无任何处理 | 对未识别键 `raise`,或在 dataset integrity 测试里校验 spec schema |
| 11 | 信息 | `test_golden_tasks.py` 整体 / `golden_tasks.json` | **无恒真断言**:phase 期望全部为单值列表(grep 无多值 `"phase": [a, b]`),`plan_tools` 用全等比较、文件副作用用存在性断言、safety 任务用 `no_tool_results` 空集断言,均能抓回归;`_chat_entry:203-204` 等到 failed/denied 也会断言 completed 失败 | — | 维持现状,补 #10 的 spec 键校验即可 |
| 12 | 信息 | skipped/cancelled 语义 | 任务级无 SKIPPED phase(skipped 仅步骤级),全 skipped 计划由引擎收尾为 completed,且有 `test_skipped_completion_semantics.py` 专测;CANCELLED 为终态(`task_phase.py:29` 空集),非严格模式下取消不会被引擎回写覆盖 | — | 语义自洽,无需改动 |

---

## 评分

**逻辑分:78 / 100**

**一句话总评**:hint 工具面硬校验的方向正确、golden 套件断言质量扎实,但 retry 无反馈回灌使其退化为"重复同样的调用再失败一次",叠加空 plan 语义冲突、CREATED→FAILED 迁移缺失和中英文关键词不对称,核心编排路径上仍有数个会产生真实误判/僵尸态的逻辑洞,且 `1<=calls<=2` 式的测试放宽正在遮蔽其中之一。
