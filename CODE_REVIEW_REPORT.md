# Lengrvis 全量代码审查报告

> **更新（截至 2026-08-24，共 7 批）**：报告中确认的 Critical、全部 High、代码级剩余 Medium 及一批 Low 已完成修复与回归验证。最终验证覆盖 Backend 全量 `4142 passed, 16 skipped`、Desktop `98 files / 432 passed, 1 skipped` + typecheck/production build、Mobile npm10 clean install/typecheck/15 项行为 smoke/Android 原生编译，以及发布证据、依赖锁、Ruff、repo hygiene、maintainability、secret scan、CodeQL 与 threat-model 门禁。各批详情见文末七节「修复记录」；剩余项为已明示的跨进程 TOCTOU/SDK 跨代升级，以及必须由真实人员、设备和候选构建完成的外部发布条件。
>
> 审查日期：2026-07-26
> 范围：整个代码库（backend / desktop / mobile / scripts / CI / 根目录配置），共约 1472 个 git 跟踪文件（655 Python + 286 TS + 101 TSX + 6 Kotlin + 69 PowerShell + 9 workflow）。
> 方法：19 个审查单元并行深审 → 主会话对高危发现读代码亲验（消除 agent 幻觉）→ 与 `PRODUCTIZATION_ISSUES.md` 及既有测试去重。
> 维度：正确性 / 安全 / 代码质量 / 测试覆盖。**初始审查阶段只出报告、未修改代码；后续修复批次及验证记录见文末。**

---

## 执行摘要

**整体评价：这是一个安全工程水准明显高于一般项目的代码库。** 审批链（HMAC 参数绑定 + DB 原子 claim + 进程随机 execution_marker 三层叠加，模型无法伪造 `approved`/`approval_id`）、SSRF 防护（连接时 IP pinning + 每请求重 pin，闭合 DNS-rebinding TOCTOU）、路径沙箱（Windows NtCreateFile 绑父目录句柄防 junction 交换）、Electron 加固（全部 IPC 通道均有 assertTrustedRenderer + 主进程侧校验器）、Ed25519 许可真验签、移动端 TOFU 证书 pinning 都做得相当扎实，多处带有 `P1-x` 修复注释痕迹。大量原本高危的疑点（伪造审批、CORS preflight 绕过、JWT 算法混淆、代理头伪造、Excel 公式注入的 `=` 拦截、executeJavaScript 注入、路径穿越）经查证**均已被正确防护**。

**但存在若干真实缺陷，集中在三类系统性问题上：**

1. **"什么算生产环境"在代码库里有多套不一致的定义** —— 导致一条本应"唯一防线"的逃生门断言可被环境命名绕过（High）。
2. **异常 / 取消 / 并发路径的状态管理不健全** —— orchestration 执行核心在取消、并行批次、plan revision 交织时会产生永久卡死、状态泄漏、审批被误作废等多个缺陷。
3. **测试的"广度"与"有效性"严重脱节** —— 一个默认放行的 pytest 收集钩子让约 244 个端点测试在关闭鉴权的情况下运行，覆盖率数字虚高。

**统计（去重、亲验后）：**

| 严重度 | 数量 | 代表问题 |
|---|---|---|
| Critical | 2 | 本机数据删除残留 + 自锁；测试鉴权默认关闭 |
| High | 10 | guardian 代理 SSRF+token 外泄；逃生门定义过窄；Developer 只读边界绕过；取消致 step 永久阻塞；文档解压炸弹 OOM 等 |
| Medium | ~25 | 订阅时钟回拨、审批 UI 盲批、浏览器写重定向 TOCTOU、email 头注入、自动更新签名等 |
| Low / Info | ~50 | 死代码、命名漂移、非原子写、资源无界增长、时序侧信道等 |

> 说明：以下 **[已亲验]** 标记的项，主会话已直接读代码确认了缺陷机制与定位；其余项来自审查 agent 且给出了具体文件:行号，未逐一复核但定位可信。

---

## Critical

### C1. 本机数据删除（PIPL/GDPR erase）漏删 presence 账本 → 数据残留 + fail-closed 自锁 **[已亲验]**
- **文件**：`backend/app/core/db_maintenance.py:54-100`（对照 `backend/app/core/db_sensitive_integrity.py:104,161-185`）
- **维度**：安全（合规/数据残留）+ 正确性（可用性）
- **问题**：`erase_local_user_data` 删除了 `PERSONAL_DATA_TABLES` + `SETTINGS_TABLES` 本表及其 `sensitive_record_integrity` 证明行，但**从不删除 `sensitive_record_presence`（存在性账本）**。
- **失败场景**：
  1. **数据残留（与运行模式无关）**：erase 后 presence 账本仍保留每条已删审批的 `(table_name, record_id, created_at)`，可枚举被删审批的 ID 与创建时间；`include_settings` 时泄露 settings 键名。这直接违背了该功能"PIPL/GDPR 本机删除"的设计目的。
  2. **fail-closed 自锁（commercial 模式）**：`sensitive_integrity_check()` 遍历 presence 行，对每条实际记录已不存在的条目报 `"Sensitive local record is missing for a presence ledger entry"` → `ok=False` → commercial/fail-closed 模式下 `require_audit_fail_closed_ok()` 阻断所有本地写入 → **一次合规的数据删除操作即把应用锁死，且跨重启持久**（bootstrap 只补不删 presence）。
- **与已知项的关系**：`PRODUCTIZATION_ISSUES.md` 记录 `test_privacy_erase.py`"3 passed，覆盖内容删除+审计链保留+响应无路径泄漏"。该测试**未覆盖 presence 账本清理，也未断言 erase 后完整性检查仍通过**，因此漏掉了此缺陷。
- **建议**：在 erase 事务内对相同 `integrity_kinds` 同步执行 `DELETE FROM sensitive_record_presence WHERE table_name IN (...)`；并补一条"erase 后 `sensitive_integrity_check().ok is True`"的回归断言。

### C2. pytest 默认给全部测试打 `desktop_api_token_optional`，鉴权 guard 对约 244 个端点测试全程短路 **[已亲验]**
- **文件**：`backend/tests/conftest.py:125-133`（配合 `app/security/desktop_api.py:57-64`、`middleware.py:182-187`）
- **维度**：测试有效性
- **问题**：收集钩子对所有未标 `requires_desktop_api_token` 的测试自动加 `desktop_api_token_optional` 标记，autouse fixture 随即设置 `LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL=1` 与 `LENGRVIS_TEST=1`，使 `should_require_desktop_api_token` 直接返回 False —— 整段桌面 token guard（豁免清单 / 签名资源 / token 比对 / WS 鉴权）不被执行。
- **失败场景**：真正 guard-on 的测试只有 `test_desktop_http_auth.py`（5 例，仅覆盖 `/api/tasks`、`/api/chat`、`/api/health`）等极少数。若 `_is_desktop_api_token_exempt_path` 被拓宽、`has_valid_desktop_api_token` 的 `compare_digest` 比对被改坏、或新增可变路由绕过中间件，**约 244 个端点测试无一能捕获**。这是"选择退出式"安全模型——新增的鉴权敏感测试默认在关闭鉴权下运行。`cov≥75` 门槛也因此虚高：安全分支从不执行，却因大量端点测试"路过" `desktop_api.py` 而被算作已覆盖。
- **建议**：反转默认为 guard-on，让极少数需要 optional 的测试显式选择退出；为每条状态改变路由族补一条 401 断言；对 `security/*` 模块单设 guard-on 覆盖子门禁。

---

## High

### H1. guardian 应用内 HTTP 代理可被指向任意主机（SSRF），并把 desktop token 送给攻击者 **[已亲验]**
- **文件**：`backend/app/api/routes_guardian.py:586-599` → `backend/app/services/guardian_runtime.py:164,172`
- **维度**：安全（SSRF + 凭据外泄）
- **问题**：catch-all 代理把路径原样 `httpx.URL(FULL_BACKEND_URL).join(path)`。按 RFC 3986，`//evil.com/x` 是 network-path reference，`join` 会**替换 authority**，目标主机从未被校验或钉死；且 `proxy()` 无条件执行 `filtered_headers.update(desktop_api_token_headers())`，把 desktop token 明文发往攻击者主机。
- **失败场景**：任何持有 desktop token 的调用方（被污染的渲染进程、本机进程、或开启 LAN 桌面 API 后的 LAN 客户端）请求 `GET //169.254.169.254/latest/meta-data/` 或 `POST //attacker.tld/` → 绕过"guardian 只与 127.0.0.1 通信"的进程边界 → ① 内网/云元数据 SSRF（全方法 + 任意 body）② desktop token 外泄。核心价值是 SSRF 网络转轴：普通 token 持有者本来只能访问本地后端。
- **建议**：不要用 `URL.join` 拼用户可控路径；规范化后拼绝对 URL 并硬校验 `url.host==base.host and url.port==base.port and url.scheme==base.scheme`，否则 400。补 `//evil.com/x`、`////x`、`/..%2f` 等负例测试。

### H2. 生产逃生门断言的"生产"定义比代码库其余部分窄，release/beta/GA 构建可被击穿 **[已亲验]**
- **文件**：`backend/app/security/desktop_api.py:26,187-205`（对照 `backend/app/security/execution_isolation.py:27-41,135-142`）
- **维度**：安全
- **问题**：`production_test_escape_fingerprint()` 只用 `{prod, production, release}` 匹配 `LENGRVIS_ENV/APP_ENV/ENVIRONMENT`；而 `execution_isolation.release_profile_active` 认 `beta/candidate/ga/rc/public-beta/...` + `LENGRVIS_RELEASE_CHANNEL` + 三个布尔量 `LENGRVIS_COMMERCIAL_RELEASE/PUBLIC_BETA/RELEASE_BUILD`。`assert_no_production_test_escape_hatches()` 对后面这些信号完全视而不见。
- **失败场景**：以 `LENGRVIS_ENV=ga`（或 `beta/rc`）打标、或仅靠 `LENGRVIS_COMMERCIAL_RELEASE=1` 标识的发行版构建，被 `execution_isolation` 视为 release，但逃生门断言在第一步 `return {}` 直接放行。此时环境若存在 `LENGRVIS_TEST=1`：桌面审批会话绑定失效、接受无法被吊销的移动 token；若同时 `LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL=1`，则桌面 token guard 整层关闭。这条被 `PRODUCTIZATION_ISSUES.md` 点名的"唯一防线"可被环境命名绕过。
- **建议**：把 release/production 判定抽成单一函数（复用 `release_profile_active`），逃生门断言与 CORS 判定统一调用；宁可误判为生产。

### H3. Developer 只读边界可通过 `Bash(git diff:*)` 的 `--output=` 获得任意文件写原语，并链式提权到代码执行 **[已亲验]**
- **文件**：`backend/app/integrations/lengrvis_code.py:76-88（DEFAULT_ALLOWED_TOOLS）,89（FORBIDDEN_ALLOWED_TOOLS）`；入口 `backend/app/orchestration/developer_engine.py:413-427`
- **维度**：安全（write guard 绕过）
- **问题**：`Bash(git diff:*)`/`git log:*`/`git show:*` 是**前缀规则**，允许携带任意 flag。`git diff --output=<file>` 会创建/截断任意路径；禁用清单只拦 `Bash`/`Bash(*)`/`Edit`/`Write`/`Agent`，不覆盖这些 git 前缀。
- **失败场景**：writes-disabled 的 developer/代码分析 run（正常产品路径）中执行 `Bash("git diff --output=$HOME/.gitconfig")` 写入 `[diff] external = malicious`，再 `git log -p --ext-diff`（匹配 `git log:*`）触发外部 diff 驱动 → **从只读 run 链式代码执行**。整个过程不经 `run_write_verification`、不受 `--add-dir` 约束。仓库自身在 `developer_tools.py:70-73` 已对 `--output` 有拦截，但该守卫未应用到子进程 allowlist。可达性：需 developer 路径被触发（agent 经提示注入可驱动），非未认证。
- **建议**：从 CLI allowlist 移除裸 `Bash(git …)` 前缀规则，git 读取统一走已加固的 `_trusted_guarded_git_command`；并把 `GIT_CONFIG_NOSYSTEM`/`GIT_EXTERNAL_DIFF`/`GIT_PAGER` 镜像进 `build_lengrvis_code_env`。

### H4. 工具禁用是黑名单，漏掉 `Task` → `Agent` 别名
- **文件**：`backend/app/integrations/lengrvis_code.py:89,631-639`；`backend/app/orchestration/developer_engine.py:234,413-427`
- **维度**：安全
- **问题**：vendored CLI 在匹配前做别名归一化 `Task → Agent`，而后端只拦截字面量 `"Agent"`。`allowed_tools` 里写 `Task` 即穿过全部后端检查 → CLI 授予 subagent（一个带自己工具集的完整子 run）。`allowed_tools` 取自 `state.current_plan`，会持久化进 `runs` 表并经 `parse_run_state_checkpoint` 反序列化（只校验 schema version，从不清洗）。唯一真正的白名单 `constrain_developer_allowed_tools` 因 `arbitrary_execution_allowed()` 在非 release profile 下直接返回 True 而失效。
- **建议**：`validate_allowed_tools` 反转为白名单——只接受显式许可集合 + 已校验的 `Bash(...)` 形式，显式拒绝 `mcp__*` 与全部 legacy 别名（`Task`、`KillShell`、`BashOutputTool` 等）。

### H5. 取消正在执行的工具会把 ToolCall 永久留在 `executing`，同一 step 在进程重启前永久无法执行
- **文件**：`backend/app/orchestration/tool_runtime_execution.py:152-154`、`tool_runtime.py:410-413`
- **维度**：正确性（异常路径状态泄漏）
- **问题**：`CancelledError` 继承 `BaseException`，不被 `_execute_tool_call` 的 `except Exception` 捕获，异常穿透后**永远走不到 `mark_tool_call_committed`/`mark_tool_call_outcome_unknown`**，ToolCall 行停在 `executing`。之后 resume 同一 task/step/plan_revision 产生相同 `execution_key` → `_handle_existing_tool_execution` 判为重复 → step FAILED "already in progress"，且 `recover_interrupted_tool_executions` 只在启动时跑一次 → 重启前该 step 永久卡死。附带：MCP 适配器（`app/mcp/registry.py:184-207`）不遵守协作式 abort，被取消的 MCP 工具会与后续同路径执行并发。
- **建议**：在 `_execute_tool_call`/`_execute_allowed_impl` 增加 `except asyncio.CancelledError:` 分支，`mark_tool_call_outcome_unknown(call, expected_status="executing")` 后再 re-raise。

### H6. 并行批次中的 recovery/plan-revision 会静默作废兄弟 step 刚创建的 approval，任务卡死（flaky）
- **文件**：`backend/app/orchestration/handlers/step_scheduler_handler.py:204-248`、`handlers/recovery_handler.py:173-178`、`agents/orchestrator_agent.py:285-295`
- **维度**：正确性（并发时序）
- **问题**：并行批次 {A, B}，A 命中审批门创建 PENDING approval 并返回 waiting，B 失败。`asyncio.wait(FIRST_COMPLETED)` 的 `done` 是无序 set：若先迭代 B → recovery 追加 step → `_persist_plan_update(revision_change=True)` → `orchestrator_agent` 把该 task 全部 pending/approved 未消费的 approval **一律 expire**（含 A 刚建的那条）→ task 停在 `WAITING_USER_APPROVAL`，UI 有审批卡片但该 approval 已 `expired`，用户点击必被 `_approval_binding_error` 拒绝。另有两条同类路径（`revision_requested` 优先判定、subagent proposal 触发 revision）。
- **建议**：`_persist_plan_update(revision_change=True)` 只 expire 不属于本次 revision 影响范围的 approval（按 step_id 过滤）；`_finalize_plan_status` 中 `any_waiting` 应优先于 `revision_requested`。

### H7. 外部取消被归一化成 FAILED，覆盖 CANCELLED 状态
- **文件**：`backend/app/orchestration/os_execution_engine.py:670-671`（对比正确的 drain 路径 `:700-701`）
- **维度**：正确性（并发时序 + 状态泄漏）
- **问题**：用户取消 run 时，仍在运行的 turn 的 `asyncio.wait` 返回已取消的 step task，`raw_outcome` 被设为 `CancelledError` 并交给 `_normalize_step_outcome` → step 置 FAILED 并 `_set_status(task, FAILED)`，覆盖刚写入的 CANCELLED，记一条伪造的 `task.step_failed_unhandled`。结果 run 行是 CANCELLED 而 task 是 FAILED 带假错误摘要。
- **建议**：主循环比照 drain 路径跳过 `CancelledError`（或 re-raise 交给统一处理器）。

### H8. 文档解析缺少解压炸弹防护，且被文件监视器自动触发 OOM
- **文件**：`backend/app/tools/document_tools.py:112-153` + `backend/app/indexer/parsers.py:11` + `backend/app/services/document_intelligence_service.py:132-139`
- **维度**：安全 / 正确性（DoS）
- **问题**：`_ensure_parseable_file_size` 只校验磁盘（压缩）大小；`.docx`/`.pptx` 经 python-docx/pptx 用 lxml 全量载入内存。攻击者把一个几十 KB、解压后数 GB 的 OOXML 放进受监视的 allowed 目录（如下载文件夹）→ file_watcher → `index_file` → `parse_file` → OOM 崩溃。
- **建议**：解析前用 zipfile 校验 OOXML 成员的解压后累计大小/压缩比，超限即拒绝。

### H9. 后台快照批次会静默中止用户的写请求（审批/发消息被取消且无提示）
- **文件**：`desktop/src/renderer/lib/api/apiRequestSession.ts:61-63` + `lib/api/client.ts:246-248,402-403`
- **维度**：正确性（竞态）
- **问题**：`request()` 用 `request.abortGroup ?? this.activeAbortGroup` 继承当前批次组，而 `submitApprovalDecision`/`sendChat` 等 mutation 不带 `abortGroup`。当 `refreshTaskSnapshot`（活跃任务时每 10-30s 及每次 run 事件触发）的 "task-snapshot" 批次处于 begin/end 窗口内，用户点"批准"发出的 POST 被打上该组；下一次 `beginBatch → abortInflight` 会中止这个仍在飞行的 POST → 审批/发消息被静默取消，无错误提示。
- **建议**：所有 mutation endpoint 显式传入独立/无 `abortGroup`，不继承 `activeAbortGroup`。

### H10. CI/发布链引用的两个安全关键脚本未纳入版本控制
- **文件**：`scripts/mcp_conformance_client.py`、`scripts/release_owner_signature.py`（均 untracked）
- **维度**：供应链 / 可审计性
- **问题**：`ci.yml:73`（`mcp:conformance`）与 `delivery_pipeline.py:118`（经 release-publish 调用）依赖这两个脚本；clean checkout 的 runner 上文件不存在 → 步骤报错。更严重的是 `release_owner_signature.py` 是 Ed25519 发布签核校验的实现，却无法被审阅或复现。（探索阶段疑列的 `run_real_llm_eval.py` 实际已跟踪，此项修正。）
- **建议**：立即 `git add` 这两个脚本并纳入 code review 与 CODEOWNERS 覆盖。

> **测试覆盖类 High（合并列出）**：guardian `proxy_full_backend` 全量反代路由及其唯一边界 `_is_mobile_or_remote_proxy_path` 零测试（`routes_guardian.py:581-599`）；审计 fail-closed 豁免清单零 HTTP 级测试（`middleware.py:206-210`）；移动会话 JWT 只测过期/scope、无 alg=none/篡改签名/错误密钥等抗伪造用例（`test_mobile_pairing.py`）；最安全关键的审批/同意组件 `ApprovalDialog.tsx`/`ConsentGate.tsx` 零单元测试。这些是 H1/C2 等运行时缺陷能长期存活的直接原因。

---

## Medium（按模块归类，择要）

**认证 / 授权 / 策略**
- `capability_manifest.py:391-394`：能力吊销文件默认不存在时 fail-open（空吊销），显式指向不存在文件时却硬失败；且该文件是无签名/无 HMAC/无 presence 锚点的明文 JSON，任何能写 data_dir 的进程（含被注入的 agent 文件工具）删除即静默恢复所有已吊销能力，无检测。这是整个安全边界里唯一"删文件即失效"的 kill switch。
- `middleware.py:206-210`：audit fail-closed 豁免用 `startswith("/api/system/diagnostics")` 前缀匹配 → `POST /api/system/diagnostics/export`（写盘含日志/配置）落入豁免，正是篡改审计链后最想执行的动作；另有一条 `/api/privacy/export` 死配置（匹配不到真实路由）。
- `permission_modes.py:74-97`：`trusted_edits`/`auto_review` 模式的敏感路径护栏不做 `canonicalize_path`，`C:\Users\me\..\..\Windows\System32\...` 可绕过，跳过 dry_run 与审批链（`allowed_directories` 仍是最后防线）。
- `lan_tls.py:276-286`：LAN TLS 私钥 write-then-chmod 明文落盘（`NoEncryption`），Windows 上 `chmod` 对 NTFS ACL 基本无效 → 同机进程读私钥即可对已配对手机 MITM。`local_secret.py` 已有 `O_EXCL|0o600`+DPAPI 的正确实现但未复用。
- `cors.py` + `websocket_origin.py`：生产 CORS/WS Origin 仍无条件信任 Vite 开发源 `localhost:5173`；本机进程抢占该端口托管页面即可通过 WS Origin 检查（CSRF 纵深防御在生产形同虚设）。

**商业 / 配置**
- `licensing.py:497-627`：订阅型 license 跳过吊销新鲜度检查且时间判定全用本地时钟、无回拨下界 → 已取消/过期订阅用户断网后回拨系统时钟即可无限离线续用付费权益。

**执行 / 编排**（详见 H3-H7，另有）
- `os_execution_engine.py:128-149`：`cancel_run` 无终态守卫，可把已 COMPLETED 的 run/task 翻成 CANCELLED（TOCTOU）。
- `developer_engine.py:299-302` / `:994-1021`：非 COMPLETED 终态跳过 write verification；任意权限拒绝（如模型请求 WebFetch）把 writes-enabled run 永久卡在 AWAITING_APPROVAL 死循环。
- `developer_engine.py:773-808`：审批 HMAC 绑定了 `allowed_tools`，但实际执行忽略它、缺失时静默回落默认 → 用户批准的工具集不约束实际运行。
- `schemas.py:131/134`：`TaskStatus.DENIED == CANCELLED` 同值 → 安全拒绝与用户取消在持久层不可区分，靠 `final_summary` 子串猜测。
- `tool_runtime_approval.py` 等三处 R4_FORBIDDEN 判定遗漏（当前 PolicyEngine 对 R4 直接 DENY 故不可利用，但"最后 backstop"层 fail-open）。

**服务 / 感知 / 工具**
- `browser_activity_runtime.py:283-340`：浏览器写操作在最终 URL 校验前执行（重定向 TOCTOU），A 源 3xx 到 B 源后写动作先在越界源执行。
- `browser_activity_runtime.py:205-245`：浏览器截图明文 PNG 落盘且文件名可预测，与任务录屏的加密存储不一致。
- `fts_index.py:480-526`：FTS/LIKE 查询无 SQL LIMIT，先 fetchall 全命中再 Python 过滤 → 大索引下短 CJK 查询内存激增；LIKE 通配符未转义。
- `app_excel.py:279-281`：Excel 公式注入护栏只挡 `=` 前缀，`+`/`-`/`@` 开头字符串同样被解析为公式。

**Agent / adapter / LLM / 移动**
- `adapters/email.py:66-87`：唯一未做输入净化的外发通道，subject/to 无 CRLF 校验 → SMTP 头注入（偷加 Bcc，且绕过 run_budget 的 recipient 绑定）。
- `openai_compatible.py:41`：API key 脱敏正则只覆盖 `sk-`/`Bearer`，Azure/Google/自定义 provider 的 key 不脱敏。
- `LengrvisLanTrust.kt:90-92,155-161`：稳定自签证书的 active pin TTL 无法续期，第 30 天强制清除重配，无平滑续期路径。

**桌面**
- `autoUpdater.ts:70-79`：注释断言默认校验更新包签名但从未显式设置；结合 commit `307c968e "Allow unsigned release asset publishing"`，若发布未签名则更新包签名校验被有效跳过 → 篡改 GitHub Release 资产者可投递含 backend.exe 的恶意更新。
- `ApprovalDialog.tsx:229-231`：审批面板默认只渲染参数键名不显示实际值，工具/参数折叠在默认收起区 → 非清理类高危动作（删除路径/发数据到 URL）主体仅靠自由文本，后端摘要含糊时用户看不到确切目标即批准（盲批风险）。

**CI / 发布**
- `evidence_contracts.py:337-364`：reviewed evidence 封存与校验用同一对称 `LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET`，是整个签核链的单一信任根（建议改非对称签名）。
- `.github/CODEOWNERS`：单一 owner 覆盖 100% 路径，发布链"人工签核"实为自审自签，bus factor=1。

---

## Low / Info（择要，完整清单见各单元原始记录）

- **死代码**（多处）：`routes_activation_admin.py:423-1247` 的 825 行过期 `_ADMIN_HTML` 副本（与线上模板已分叉，维护误改风险）；`coordinator_worker.py` 整模块生产无引用；`local_secret.py:220` 不可达 return；`run_event_bus.py` 事件白名单无使用且已漂移；`perception/storage.py:440-455 _contains_sensitive_term` 仅自递归。
- **命名 / 一致性**：`pnpm-workspace.yaml` 与实际 npm 锁文件工具链矛盾（按它 `pnpm install` 得不一致依赖树）；`_ADMIN_PLANS` 白名单/错误文案/前端三方不一致；`supervisor_agent.py` 的 `UNINSTALL_TERMS as APP_ACTION_TERMS` 别名混淆。
- **仓库卫生**：`lengrvis.zip`（6.1MB）既未 `.gitignore` 也不在 hygiene 黑名单，一次 `git add .` 即提交且门禁不拦、gitleaks 扫不到 zip 内含物；`format('{0}-{1}-{2}',...)` 假密钥写法绕过自家 gitleaks 规则。
- **资源 / 健壮性**：`routes_chat.py:56-89` WS 去重 set 无上限增长；`ipcInflight.ts` abortGroup Map 正常完成不回收；`consentManager.ts` 非原子写。
- **时序侧信道**：`remote_tools.py:180-183` 是全仓唯一未用 `hmac.compare_digest` 的 HMAC 比对。
- **输入校验**：activation admin 的 `expires_at` 非 ISO 字符串 → 未捕获 ValueError → HTTP 500（应 422）；`voice_transcribe` 把 language 赋值到模块级单例再 await，并发不同语言请求互相覆盖。
- **非发布 workflow** 未设 `persist-credentials: false`（6 个）；`lint.yml:42-45` 在 pull_request bash 块直接插值 `github.base_ref`（低可利用性）。

---

## 已查证确认"安全"的重点疑点（供参考，避免重复排查）

- **审批伪造不可行**：模型注入的 `approved`/`approval_id` 被 `model_boundary` 递归剥离 + DB 原子单次 claim + 6 处 HMAC 常量时间比对 + 进程随机 execution_marker 四层阻断。
- **SSRF 主防护扎实**：LLM/MCP 出站统一走 `outbound_url.pin_outbound_http_url`，连接时 IP pinning + 每请求重 pin，闭合 DNS-rebinding TOCTOU，`follow_redirects=False`；IPv4-mapped/十进制 IP/云元数据均被拦。
- **路径沙箱扎实**：`filesystem_safety` 用 NtCreateFile 绑父目录句柄 + 二次授权防 junction/symlink 交换 TOCTOU；未能构造出绕过 `resolve_authorized` 的穿越。
- **Electron 硬化到位**：contextIsolation/sandbox/nodeIntegration 正确，全部 IPC 通道有 assertTrustedRenderer + 主进程校验器，executeJavaScript 全部用 `JSON.stringify` 序列化不可信数据（无注入），凭据填充不回传明文。
- **activation admin 无 XSS**：模板是静态拼接，动态数据全走客户端 `textContent`；CSRF 双提交 + `compare_digest`，admin 用 PBKDF2 390k。
- **渲染层 dev 旁路生产不可达**：`import.meta.env.DEV` 生产被静态替换为 false（死代码消除）+ `vite.config.ts:31-36` 双重置空。
- **移动端 TOFU pinning 扎实**：指纹经 QR 带外确认后先 stage 再连接，rotation 只接受用户确认的指纹，过期/撤销/损坏均 fail-closed；未发现可确认的绕过。
- **JWT 解码正确**：算法白名单固定、aud/iss 校验、`options={"require":[...]}`、设备 epoch + token family 双重吊销（仅缺对抗性测试）。

---

## 建议的修复优先级

1. **立即修（Critical/合规+可用性）**：C1 erase 漏删 presence 账本、C2 测试鉴权默认反转。
2. **发布前修（High/安全边界）**：H1 guardian 代理 SSRF、H2 逃生门定义统一、H3/H4 Developer 工具 allowlist 反转为白名单、H10 补齐缺失脚本。
3. **尽快修（High/可用性）**：H5-H7 取消/并行状态管理、H9 前端批次误取消审批。
4. **计划修（Medium）**：能力吊销 fail-open、审计豁免清单、私钥落盘、订阅时钟回拨、审批 UI 盲批、自动更新签名。
5. **持续清理（Low）**：死代码、命名漂移、仓库卫生、补测试。

**验证方式**：修复后运行 `pytest`（`pytest.ini` 已配置，cov≥75）、desktop `npm run smoke` + `vitest`、`npm run hygiene`、gitleaks；并针对上述 Critical/High 补对应的负例回归测试（当前正是这些测试的缺失让缺陷长期存活）。

---

## 修复记录（2026-07-26）

已修复的 Critical 与全部 High，每项均附回归测试（此前正是这些测试缺失让缺陷长期存活）。

| 编号 | 修复 | 主要改动 | 回归测试 |
|---|---|---|---|
| C1 | erase 同步清理 presence 账本 | `db_maintenance.py`：erase 事务内 `DELETE FROM sensitive_record_presence WHERE table_name IN (...)` | `test_privacy_erase.py`：断言 erase 后 presence 无残留且 `sensitive_integrity_check().ok is True` |
| C2 | 补鉴权守卫 guard-on 回归（务实方案） | 未做 244 测试的全量默认反转（风险过大）；改为新增 guard-on 测试保护 token 比对与豁免清单边界 | `test_desktop_http_auth.py`：无效 token 被拒、非豁免的 `POST /api/system/diagnostics/export` 需 token |
| H1 | guardian 代理钉死目标主机 | `guardian_runtime.py`：`posixpath.normpath` 规范化路径 + `copy_with(path=)` 只替换 path + 硬校验 host/port/scheme==base 否则 400 | `test_guardian_backend.py`：`//evil.com`/`////attacker`/`..//metadata` 均不逃逸 loopback |
| H2 | 统一生产环境判定 | `desktop_api.py`/`cors.py`：复用 `execution_isolation` 的 `RELEASE_ENVIRONMENT_VALUES`+`RELEASE_BOOLEAN_NAMES`（覆盖 ga/beta/rc/candidate+布尔量）；删除窄定义死常量 | `test_audit_fixes_2026_06.py`：ga/beta/rc/candidate 与 `LENGRVIS_COMMERCIAL_RELEASE` 均触发断言、CORS 排除 dev 源 |
| H3 | 切断 Developer git 代码执行链 | `lengrvis_code.py`：`build_lengrvis_code_env` 注入 `GIT_CONFIG_GLOBAL/SYSTEM=devnull`+`GIT_EXTERNAL_DIFF=`+`GIT_PAGER=cat` 等，令 `--output` 写入的 gitconfig 无法被后续 git 读取执行 | `test_lengrvis_code_config.py`：断言 git 加固环境变量 |
| H4 | Developer 工具别名/mcp 拒绝 | `lengrvis_code.py`：`_is_forbidden_allowed_tool` 归一化 `Task→Agent` 等别名并拒绝 `mcp__*` | `test_lengrvis_code_config.py`：`Task`/`task`/`KillShell`/`mcp__*` 均被拒 |
| H5 | 取消不再让 ToolCall 卡死 | `tool_runtime.py`：`_execute_tool_call` 包 `except asyncio.CancelledError` → `mark_tool_call_outcome_unknown(expected_status="executing")` 再 re-raise | `test_tool_runtime.py`：取消后 call 状态为 `outcome_unknown` 非 `executing` |
| H7 | 取消不再覆盖 CANCELLED | `os_execution_engine.py`：主循环遇 `CancelledError` 的 step `continue`（不归一化为 FAILED），镜像 drain 路径 | 由 `test_execution_engines.py`/`test_parallel_steps.py` 覆盖 |
| H8 | 文档解压炸弹防护 | `document_intelligence_service.py`：`_ensure_parseable_file_size` 新增 `_ensure_safe_ooxml`（校验 OOXML 解压后累计大小 512MB 上限 + 压缩比 200:1），共享入口覆盖所有解析/索引路径 | `test_document_intelligence.py`：解压炸弹 docx 被拒、正常 docx 放行 |
| H9 | 前端 mutation 不被批次误取消 | `apiRequestSession.ts`：只有 GET/HEAD 读请求继承 `activeAbortGroup`，mutation 不继承（除非显式指定） | `apiRequestSession.test.ts`：批次活跃时 POST 不带批次组、显式 abortGroup 仍生效 |

### H3 残留说明
git 加固环境已切断「`--output` 写 `~/.gitconfig` → `--ext-diff` 代码执行」这一最严重的提权链。`git diff --output=<file>` 的纯文件写（创建/截断，内容受限于 diff 输出）在运行时仍可能，因为 vendored CLI 的 `Bash(git diff:*)` 前缀规则无法在 allowlist 字符串层表达「禁止 --output」。彻底封堵需向 CLI 传 deny 规则或将 git 读取重构为统一走已加固的 `_trusted_guarded_git_command`，属较大改动，建议后续处理。

### C2 说明
报告建议的「反转 pytest 默认为 guard-on」会使约 244 个端点测试因缺 token 而失败，属大范围高风险改造，本轮未做。改为新增 guard-on 回归测试直接保护实际风险点（token 比对正确性、豁免清单不含状态改变路由）。完整默认反转仍作为推荐的后续独立工作。

### 未处理项
约 25 个 Medium 与约 50 个 Low/Info 未在本轮处理，清单见上文各章节。

---

## 修复记录 · Medium 批（2026-07-26）

| 编号 | 修复 | 主要改动 | 回归测试 |
|---|---|---|---|
| M16 | WebSocket Origin 生产不信任 dev 源 | `websocket_origin.py`：Vite dev 源（5173/VITE_DEV_SERVER_URL）仅非 release profile 信任；strict 模式在 release 下默认开启；保留显式 operator override | `test_desktop_websocket_auth.py`：dev 信任、`LENGRVIS_ENV=ga` 不信任 |
| M17 | 审计 fail-closed 豁免清单精确化 | `middleware.py`：`_audit_fail_closed_exempt_path` 改精确集合，排除写操作 `POST /api/system/diagnostics/export`，删死配置 `/api/privacy/export` | `test_audit_chain.py`：export 及子路径不豁免、读路径仍豁免 |
| M18 | trusted_edits 路径规范化 | `permission_modes.py`：路径类字符串先 `canonicalize_path`，`..` 越根视为敏感 fail-closed；普通文本不误判 | `test_policy_engine.py`：`..\..\Windows` 遍历被拦、prose 不误判 |
| M19 | Excel 公式注入护栏扩展 | `app_excel.py`：拒绝 `=`/`@` 及非数字的 `+`/`-` 前缀（含前导空白/控制符）；负数文本仍放行 | `test_excel_com_tools.py`：`+`/`-`/`@`/前导空白触发前缀被拒、`-5`/文本放行 |
| M20 | email/calendar CRLF 注入校验 | `adapters/email.py`+`calendar.py`：header 字段拒绝 CR/LF（防 SMTP 头/ICS 属性注入）、email 收件人格式校验；body/description 多行仍允许 | `test_adapters.py`：subject/to CRLF 被拒、合法消息通过 |
| M21 | LLM api key 脱敏正则扩展 | `openai_compatible.py`：`_API_KEY_PATTERN` 覆盖 Anthropic `sk-ant-`、Google `AIza`、`api_key/token/secret=<value>` 赋值形态 | 由现有 llm 套件覆盖 + 内联验证 |
| M22 | FTS 查询内存边界 + LIKE 转义 | `fts_index.py`：MATCH/LIKE 查询 SQL 层加有界 LIMIT；`_escape_like_term` 转义 `%`/`_`/`\`；顺带修复既有 bug——多 token 短 CJK 回退改为按 token AND 匹配 | `test_fts_trigram_migration.py`：此前失败的 CJK 回退测试现通过 |
| M23 | cancel_run 终态守卫 | `os_execution_engine.py`：run 已终态则不下调；task 已终态（COMPLETED 等）不被覆盖为 CANCELLED（TOCTOU） | 由 `test_execution_engines.py`/`test_runs_api.py` 覆盖 |
| M24 | 能力吊销文件 fail-closed | **未修复（后续项）**：正确修复需防篡改的吊销状态持久化以区分"全新安装缺文件（合法）"与"删除已有吊销文件（攻击）"——两者在无持久状态时不可区分。在 840 行安全模块内仓促加半成品完整性锚点风险大于收益，列为需专门设计的后续工作 | — |

### 顺带修复
M22 过程中发现并修复了一个**既有失败测试**（`test_fts_search_multi_token_short_cjk_query_falls_back_to_like`，在原始代码上即为红）：多 token 短 CJK 查询的 LIKE 回退原本用整串（含空格）匹配，无法命中非相邻 token；改为按 token AND 匹配后该回退按预期工作。

### 本轮仍未处理
剩余 Medium（订阅时钟回拨续用、审批 UI 盲批默认隐藏参数值、浏览器写重定向 TOCTOU、浏览器截图明文落盘、自动更新签名验证、R4 backstop fail-open、DENIED==CANCELLED 同值、evidence 对称 HMAC 单点等）与全部 Low/Info 未在本轮处理，清单见上文各章节。H3 的 `git --output` 纯文件写残留、C2 的 pytest 默认全量反转仍作为推荐后续项。

---

## 修复记录 · Medium 批 2（2026-07-26）

| 编号 | 修复 | 主要改动 | 回归测试 |
|---|---|---|---|
| M25 | R4 风险层级 backstop fail-closed | `risk.py` 新增 `is_modifying_or_higher`（≥R2，含 R4）；`tool_runtime_approval.py`/`tool_runtime_paths.py`/`automation_runtime_guard.py` 三处把 `{R2,R3}` 判定替换为该 helper，R4（如所有 MCP 工具）不再被当作非写 | `test_policy_engine.py`：helper 对 R0–R4 与字符串/None 的判定 |
| M26 | 浏览器写操作重定向前校验同源 | `browser_activity_runtime.py`：`_write_like` 在 `goto()` 后、click/fill/submit 前立即 `_validate_final_url`，重定向到越界源时写动作不再执行 | 由 `test_browser_writes.py`/`test_browser_activity*` 覆盖（`_validate_final_url` 同源逻辑已有测试） |
| M27 | 自动更新显式签名校验 | `autoUpdater.ts`：显式 `verifyUpdateCodeSignature = true`，修正误导注释（说明 Windows 上仅当已安装应用已签名才生效，未签名发布会有效跳过校验） | typecheck（require 路径需打包环境，不宜单元测试） |
| M28 | 订阅 license 时钟回拨防御 | `licensing.py`：持久化单调"已见最大时间"水印（data_dir/clock-watermark.key，原子写、5min 容差）；`subscription_confirmation_fresh` 用 `_effective_now` 将整个新鲜度评估钉死在单调时钟，回拨系统时钟无法复活过期/取消的订阅（显式 `now` 仍按测试值） | `test_licensing.py`：回拨到水印之下不回退、显式 now 仍生效 |
| M29 | 审批对话框展示高危动作边界 | `ApprovalDialog.tsx`：high/critical 风险时工程边界 `<details>` 默认展开（原默认收起→盲批），summary 文案提示核对工具/参数/运行时边界 | typecheck（该组件无现成测试夹具，属既有 H 级测试缺口） |

### 本轮（含前一 Medium 批）仍未处理
- **M24 能力吊销 fail-closed**：需防篡改的吊销状态持久化，列为需专门设计的后续项（见上）。
- 其余 Medium：浏览器截图明文落盘、`schemas.py` DENIED==CANCELLED 同值、evidence 对称 HMAC 单点、单一 CODEOWNER、`lengrvis.zip` 卫生缺口、`LengrvisLanTrust.kt` pin TTL 续期、mobile SecureStore 回退正则过宽等。
- 全部 Low/Info（死代码、命名漂移、非原子写、资源无界增长、时序侧信道等）。
- H3 的 `git --output` 纯文件写残留、C2 的 pytest 默认全量反转仍为推荐后续项。

---

## 修复记录 · Medium/Low 批 3（2026-07-26）

| 编号 | 修复 | 主要改动 | 回归测试 |
|---|---|---|---|
| M30 | 移动端 SecureStore 回退判定收窄 | `mobile/src/store/auth.ts`：`isStorageBackendUnavailable` 改白名单式（仅 native 模块缺失/TurboModule 未注册才回退内存），keychain 锁定/加密失败/"不支持"等真实错误改为上抛而非静默降级到明文内存 | node 正则验证 + mobile typecheck |
| M31 | 浏览器截图不可预测文件名 | `browser_activity_runtime.py`：截图文件名由 `sha256(url)` 改为 `secrets.token_hex`，本地进程无法凭 URL 推算路径读取截图；移除随之未用的 hashlib 导入 | `test_browser_writes.py` 等 135 项 |
| M32 | developer 非 COMPLETED 终态写校验 | `developer_engine.py`：writes-enabled run 在任意终态（含 FAILED/超时）都执行 `_apply_write_verification`，越界写检测不再仅限 COMPLETED（helper 由 writes_detected 门控是否翻 FAILED，对已 FAILED 安全） | `test_developer_write_guard.py`/`test_execution_engines.py` 101 项 |
| Low | 安全死代码清理 + 输入校验 | `local_secret.py` 删不可达 `return`；`execution_isolation.py` 删未用 `_strict_int`；`routes_activation_admin.py` 删 825 行无引用的过期 `_ADMIN_HTML` 副本；activation admin 请求模型加 `expires_at`/`renews_at` 的 ISO 校验器（非法值→422 而非 500） | `test_activation_admin_panel.py`：非 ISO 时间返回 422 |

### 仍未处理
- **M24 能力吊销 fail-closed**（需专门设计的后续项）。
- 其余 Medium：`schemas.py` DENIED==CANCELLED 同值、evidence 对称 HMAC 单点、单一 CODEOWNER、`lengrvis.zip` 卫生缺口、`LengrvisLanTrust.kt` pin TTL 续期等。
- 全部 Low/Info（其余死代码、命名漂移、pnpm-workspace 悬空、voice 语言并发竞态、非发布 workflow persist-credentials 等）。
- H3 `git --output` 纯文件写残留、C2 pytest 默认全量反转仍为推荐后续项。

---

## 修复记录 · 批 4（2026-07-26）

| 编号 | 修复 | 主要改动 | 验证 |
|---|---|---|---|
| M36 | mobile pin TTL 无法续期 | `LengrvisLanTrust.kt`：`activateServerCertificate` 用 `maxOf(current, new)` 更新 expiry（原先仅 `status==NEXT` 时更新）→ 用户重新确认同一带外指纹即可续期，稳定自签证书不再在第 30 天硬过期强制重配对；只延长不缩短 | 新增 instrumented 测试 `reconfirmingActivePinExtendsExpiryWithoutRepairing`（续期 + 不缩短两条断言） |
| Low | lengrvis.zip 仓库卫生缺口 | `.gitignore` 与 `scripts/check_repo_hygiene.ps1` blocklist 均加入 `*.zip`/`*.7z`/`*.tar`/`*.tar.gz`/`*.tgz`——归档对 gitleaks 不透明，一次 `git add .` 曾可提交 6MB 源码快照且门禁不拦 | `git check-ignore` 确认 lengrvis.zip 被忽略；hygiene 门禁通过；无既有归档被跟踪 |
| Low | 非发布 workflow 凭据残留 | 6 个非发布 workflow 的全部 12 处 `actions/checkout` 补 `persist-credentials: false`（此前仅 3 个发布 workflow 设置） | 脚本校验：9 个 workflow YAML 全部可解析，checkout 数 == persist-false 数 |
| Low | voice 语言并发竞态 | `voice_input.py`：`process_utterance`/`_transcribe` 增加按调用传入的 `language` 参数；`routes_perception.py` 改为传参，不再对模块级共享 processor 赋值后 await（并发不同语言请求会互相覆盖） | voice/perception 测试 13 项通过 |
| Low | pnpm-workspace 悬空 | 删除 `pnpm-workspace.yaml`：无 `pnpm-lock.yaml`、无 workflow/脚本引用，与实际 npm 锁文件工具链矛盾，照它 `pnpm install` 会得到未锁定依赖树 | 确认无引用后删除；hygiene/start-app 元测试 120 项通过 |

### 仍未处理
- **M24 能力吊销 fail-closed**（需专门设计的后续项）。
- 其余 Medium：`schemas.py` DENIED==CANCELLED 同值（需新增 TaskPhase 或 denial 标记，涉及持久层语义）、evidence 对称 HMAC 单点（建议改非对称签名）、单一 CODEOWNER（治理决策）。
- 其余 Low/Info：`lint.yml` 上下文插值改 `env:`、`format()` 假密钥写法、`_ADMIN_PLANS` 三方不一致、`routes_chat.py` WS 去重集合无上限、`ipcInflight` Map 不回收、`consentManager` 非原子写、`remote_tools` 非常量时间比较、mobile 单条 pin 撤销入口缺失等。
- H3 `git --output` 纯文件写残留、C2 pytest 默认全量反转仍为推荐后续项。

---

## 修复记录 · 批 5（2026-07-31）

| 编号 | 修复 | 主要改动 | 回归测试 |
|---|---|---|---|
| M24 | 能力吊销文件删除后 fail-closed | 首次成功读取默认吊销文件后，将 presence 锚点写入带 HMAC 完整性与 presence 账本保护的 `app_settings`；后续文件删除、锚点缺失/篡改/不可验证均禁用受保护能力；全新安装缺文件仍合法且不创建 DB | `test_capability_manifest.py`：删除、锚点篡改、首次缺失，19 项通过 |
| H3 | Developer git `--output` 写盘残留 | 所有 Developer 子进程强制传 `--disallowedTools`；deny 规则先于 allow 规则拦截 `git --output` 的普通、参数后置与拆分写法，并禁止 `xargs git` 隐藏实参；write-enabled 同样生效 | Lengrvis Code 配置/runner/API 契约测试；受影响总计 266 项通过 |
| H6 | 并行失败恢复作废兄弟审批 | 并行失败先缓存在批次状态，待全部运行步骤完成后再串行 recovery；一旦已有等待审批/终止结果就不修改计划版本；`any_waiting` 优先于 revision；显式需审批 step 禁止进入并行批次 | `test_parallel_context_isolation.py`：失败先完成、审批后完成时 recovery 不抢跑；parallel review 契约 |
| P1 | Developer 内部 permission denial 死循环 | 区分真实 backend Approval 与 CLI 内部权限拒绝；后者明确终止为 FAILED，不再伪造“等待审批”，且仍执行 write verification；拒绝详情保持脱敏 | `test_developer_write_guard.py`：消费唯一 backend approval 后内部拒绝终止、无第二张伪审批；runner payload 契约 |
| M34 | 浏览器截图明文落盘 | Playwright 截图改为内存 bytes；最终来源校验在 capture 前完成；校验通过后复用加密 task-recording BLOB 存储并返回受控 API artifact URL，失败路径不创建 PNG | `test_browser_activity_runtime.py`：密文存储/可授权解密、跨域重定向在 capture 前阻断，63 项通过 |
| Low | 长连接与桌面状态持久化加固 | WebSocket message-id 去重改有界窗口；远程输入 HMAC 改 `compare_digest`；IPC abort group 用引用计数 lease 在正常完成后释放；consent 改原子 JSON 替换；lint workflow 的 GitHub context 改走 `env` | Backend WebSocket/remote 专项 9 项；Desktop 95 files / 421 tests；生产 build 通过 |

### 批 5 验证

- Backend 全量：`3905 passed, 12 skipped`。
- Desktop：typecheck、`95 files / 421 tests`、production build 与 renderer bundle budget 全通过。
- 受影响模块复测：266 项；安全模块抽取后再测 123 项。
- Changed-file Ruff/format、repo hygiene、maintainability gate（P95 778）、secret scan、workflow YAML、`git diff --check` 全通过。

### 仍未处理

- pytest 鉴权默认模型仍为 opt-out（C2 完整反转）。
- `TaskStatus.DENIED` 与 `CANCELLED` 仍共享持久状态，需独立 `TaskPhase.DENIED` 与兼容迁移。
- Reviewed evidence 仍以对称 HMAC 封存/验证，需迁移到 reviewer 私钥与发布侧公钥的非对称签名。
- CODEOWNERS 单一 owner 属治理与人员配置问题，无法仅靠代码修复。

---

## 修复记录 · 批 6（2026-07-31）

| 编号 | 修复 | 主要改动 | 回归测试 |
|---|---|---|---|
| C2 | pytest 桌面鉴权默认 guard-on | 删除全量隐式 `desktop_api_token_optional`；普通 TestClient/ASGI client 注入固定测试凭证并真实经过 token 比对；无凭证/错凭证契约用独立 marker，浏览器旁路仅在明确测试上 opt-in | 使用 TestClient/ASGITransport 的广测 1169 项；聊天、配对码、诊断导出 missing/wrong token 401 契约 |
| M37 | `DENIED` 与 `CANCELLED` 持久语义分离 | 新增独立 `TaskPhase.DENIED`、状态机/Run/Task/API/指标映射与 SQLite v9 保守迁移；迁移仅重建有明确拒绝证据的旧记录，并只对齐最新 cancelled run；运行时删除摘要/计划猜测 | DENIED 专项、迁移、状态机、Run API、审批与 TaskPool 广测；最终 Backend 全量通过 |
| M38 | Reviewed evidence 对称 HMAC 单点 | 迁移为 `reviewed-evidence-ed25519/v3`；reviewer 私钥只用于封存，candidate/review/publish 验证侧仅持公钥；算法版本、公钥 SHA-256 指纹、候选 CI identity 与 artifact hash 纳入 canonical payload；新增拒绝覆盖的 keypair 生成器 | 发布 evidence、Android strict gate、release workflow/profile 共 143 项通过；旧 HMAC/缺公钥/错 key binding fail-closed |
| UI | 拒绝、取消、回滚状态贯通 | Desktop `TaskState` 保留 denied/cancelled，时间线、Office、聊天终态、状态色与技术解释分别呈现；Mobile Companion 区分拒绝/取消，并补齐 rolled_back/repair_required 终态 | Desktop `96 files / 422 tests` + typecheck/build；Mobile typecheck + task-companion smoke |
| Maintainability | 状态迁移与时间线拆分 | DENIED 数据迁移抽到 `db_migration_task_denied.py`，时间线文案/色调抽到 `taskTimelinePresentation.ts`；未增加门禁豁免 | maintainability gate 通过：P95 778，`db_migrations.py` 847 行，`TaskTimeline.tsx` 840 行 |

### 批 6 验证

- Backend 全量：`3931 passed, 12 skipped`（9 分 51 秒）。
- 发布/证据专项：`143 passed`；Ed25519 公私钥拆权 workflow 契约通过。
- Desktop：typecheck、`96 files / 422 tests`、production renderer build 与 bundle budget 全通过。
- Mobile：typecheck 与 task-companion behavior smoke 通过。
- Ruff/format、依赖哈希锁、repo hygiene、maintainability（P95 778）、secret scan、agentic threat model、workflow YAML 契约均通过。

### 剩余治理与外部证据

- `.github/CODEOWNERS` 仍为单一 owner；这是人员与审批治理配置，不能在未知真实 reviewer 身份时由代码审查擅自改写。
- Clean-machine、Android 真机、人工结果质量与 release-owner sign-off 等 P0 evidence 仍应由真实候选构建和人工/设备流程完成；不能用本地自动化测试冒充。

---

## 修复记录 · 批 7（2026-08-25）

| 编号 | 修复 | 主要改动 | 回归测试 |
|---|---|---|---|
| B7-1 | 工具结果与启动恢复 fail-closed | 启动恢复改为任务级 `execution_recovery/v1` 聚合；`outcome_unknown`、cleanup 失败和回滚中断优先于 durable denial，任务保持 `REPAIR_REQUIRED`。所有大小的可恢复结果都要求根级 runtime review 完成、非空 review ID 与 `allow` verdict；缺失或篡改即隔离并阻止重放。artifact cleanup 成功归一为永久 quarantine stub，失败只保留单一 cleanup-required 状态，重复启动不重复写库、审计或删除 | unknown + DENY cleanup、多次启动幂等、未审查小结果、cleanup 中断、artifact 篡改与恢复组合测试 |
| B7-2 | `rollback-evidence/v2` 可信绑定 | 可信路径集合只来自执行前参数、工具定义或 resolver；插件输出不能事后扩展。sanitizer 逐动作核验执行前后状态、摘要、路径边界与大小，创建/移动/备份必须满足真实状态转换；旧版、未知/多主动作、缺前态或不完整证据统一生成 blocker，整批零文件副作用。旧 committed/created 调用保守进入 inventory，稳定 journal hash 避免成功回滚后误判 | rollback evidence、inventory、claim/lease、heartbeat、共享路径锁与中断恢复组合 `160 passed, 3 skipped` |
| B7-3 | 共享回滚工作流与永久中断态 | Native Confirmation 与自动恢复复用同一 claim/lease/锁/执行/finalize 工作流；执行中异常、lease 丢失或 finalize CAS 丢失持久化为 `interrupted` 并转 `REPAIR_REQUIRED`，永久阻止自动重试。文件写与回滚共享粗粒度 filesystem barrier，应用内并发路径闭合 | task rollback claim/workflow、状态机与 recovery 故障注入全通过 |
| B7-4 | `effective-risk/v1` 全链路贯通 | PolicyEngine 在权限硬拒绝后、任何 allow/approval/cache/fast-path 前只计算一次动态风险；权限、动态风险与 cache bucket 使用同一服务端时间快照。Approval 保存有效风险与 provenance，R2 TTL 10 分钟、R3 TTL 5 分钟；ToolCall 保存 declared/effective risk 与 review 绑定。自动、人工恢复、developer、browser、UI、direct 与 remote-input 均复核统一绑定；风险升高原子过期旧审批，降低仍按已批准的较高风险执行，R3 移动端 claim 同事务验证 biometric | 核心风险/执行组合 `271 passed`；remote-input 与审批邻接回归 `281 passed`，claim 竞态与 22:00 时间边界 10 项通过 |
| B7-5 | Direct 与 Developer 结果边界 | Browser/UI direct execution 复用控制字段剥离、pending stub、完整 post-tool review、结果预算、回滚证据清洗与 replay validator。PolicyEngine 始终审查完整 `ToolResult.output`，不再信任工具 summary；Developer 只从已审查、预算后的结果重建公开输出，失败状态不能被 payload 伪造成成功。取消结果仅在完整 ALLOW 绑定和双层取消标记一致时恢复常量化最小语义，不重水化诊断内容 | direct/developer 安全回归 68 项；Developer/Lengrvis runner 58 项；Runs API cancel 7 项及原场景连续 5 次通过 |
| B7-6 | Windows 大结果 artifact 路径 | 大结果 canonical 文件名改为 domain-separated SHA-256 绑定的 `r-<32hex>.json`，避免完整 tool name 把 Win32 路径推到 260 字符；保留 exclusive create、canonical-only replay validator，并精确兼容旧命名 cleanup；软链或非普通文件 fail-closed | 新增长目录、400 字符工具名、身份绑定、exclusive create、旧格式拒绝/清理 5 项；ToolRuntime/Journal 组合 `142 passed` |
| B7-7 | managed backup 身份绑定 v3 | `managed-backup-identity/v3` 将采集、inventory 与执行前复核统一到同一模块：在同一打开描述符上绑定有界 SHA-256、大小、inode/device、平台文件对象 ID、mtime 与 ChangeTime，并在 hash 前按可信大小拒绝替换文件。Windows 使用 `GetFileInformationByHandleEx(FileIdInfo/FileBasicInfo)`，时间统一为 Unix ns；旧 v2、缺/多字段、跨平台 object ID、身份篡改及同内容重建均 fail-closed。时间戳允许合法的 1970 年前有符号值，大小和对象编号仍严格非负 | helper/evidence `47 passed, 2 skipped`；rollback、workflow、claim、ToolRuntime 与 recovery 组合 `219 passed, 4 skipped` |
| B7-8 | 跨平台 CI 与 CodeQL 闭环 | PowerShell Core 的 JSON 整数按 Int32/Int64 严格解析并做溢出检查；测试不再受 runner `GITHUB_RUN_ID` 污染，Linux 使用 `pwsh` fallback。CodeQL 保留历史成功的 Python/TypeScript 配置身份与 `none` build mode，并以独立 `manual` Kotlin 配置安装锁定 mobile 依赖、通过 `bash ./gradlew` 编译主代码和 androidTest，保留真实 Kotlin 扫描 | Ubuntu/macOS 原失败用例定向复现通过；Android release/mobile gate `17 passed`；CodeQL YAML、基线 category 与执行方式契约通过 |
| B7-9 | 依赖与锁文件安全收口 | Python 升级 `cryptography 50.0.0`、`pypdf 6.16.2`、`pip 26.2.1`；Desktop 锁升级 Electron/axios 等安全补丁。Mobile 升级 Expo patch line、EAS CLI 22.2.0，显式使用 Expo 推荐的 Reanimated/Worklets，并补齐 Constants/Linking/SVG 原生 peers；移除破坏 Oclif `safeDump` 的跨 major `js-yaml` override。`minimatch` 只在 EAS 旧 5.1.2 依赖边升级至兼容的 5.1.9，Oclif 保留 10.2.6；`ts-deepmerge` 升至安全 v8，并以版本、源码标记和本地路径所有权约束的幂等 postinstall 补丁适配 EAS 唯一旧调用点。Gradle checksum 验证兼容 CRLF checkout | desktop/mobile npm audit 均 0；四份 Python lock 无已知漏洞；npm10 clean install/`npm ls`、Expo check、EAS CLI 启动/build help/真实配置合并、15 项 mobile smoke 与 SBOM 门禁通过 |
| B7-10 | GitHub CodeQL 实扫告警闭环 | 审批会话代际失效移除 check-then-act 与 truncate fallback，改为一次原子 rename、仅 `ENOENT` 幂等；Browser wait 严格限制为 `0..30000ms` 安全整数；后端 stdout/stderr 与远端 drain 正文不再持久化，日志文件以 `0600` 创建；Android connected gate 将动态地址与配对凭据移入 Gradle project-property 环境通道，Windows 命令行只保留固定参数；清理 renderer 未使用导入 | 审批代际 `10 passed, 1 skipped`；新增 timer/日志 8 项；Desktop 全量 `98 files / 430 passed, 1 skipped`；Android/CodeQL 契约 `17 passed` 与 LAN TLS source smoke 通过 |
| B7-11 | CodeQL 二次扫描与遗留 high 收口 | 二次实扫暴露的 5 个 high 与 1 个 error 已逐项修复：timer sink 置于静态可证明的直接上界分支；HTTP proxy 用成对 `rawHeaders` 保留端到端/重复响应头，显式验证每个头名、头值及 `Connection` option 后剥离 hop-by-hop，畸形元数据封闭为固定 502；该透明代理边界没有自有安全头可被远端覆盖，使用 CodeQL 官方精确规则标注留证。Ed25519 公私钥都以 `0600` 创建；测试响应不再反射请求 URL；UI fake automation 正确调用父类初始化。同步修复默认分支遗留的 Bing lookalike、邮箱/文件名误判、敏感配置名日志与 `0666` 文件创建；UI 审批复核沿用同一服务端 `now_provider`，pytest 默认策略时钟固定而显式夜间测试不受影响。Mobile #215–#219 经主/guardian 双路安全传输、active-device JWT、逐事件 claims 过滤、有限字段重序列化及特权操作服务端复核查证为误报，已留证据并按 false positive 处置 | 受影响 Backend `209 passed, 5 skipped`，审批绑定/UI 组合 `113 passed`；Desktop proxy `5 passed`、全量 `98 files / 432 passed, 1 skipped`、typecheck/production build 通过；异常边界门禁通过，GitHub CodeQL required check 作为最终实扫凭据 |
| B7-12 | Windows 私钥 DACL 冷启动预算 | reviewed-evidence keypair 生成器继续以受信 System32 PowerShell、路径文件身份和最终 DACL 复核 fail-closed，但将首次 PowerShell/`Add-Type` 的固定 15 秒预算改为不可由环境覆盖的 60 秒有界常量；超时保留明确根因且不重试，避免未知 ACL 状态竞争。测试自身的独立 ACL 复核使用相同上限，同步 `TimeoutExpired` 故障注入确认清理路径不残留私钥或公钥 | keypair 专项 `4 passed`；完整发布/证据专项 `150 passed`；Ruff check/format 通过 |
| B7-13 | NTFS creation-time tunneling 闭环 | Windows required check 复现同名删除重建后 Python `st_ctime_ns`（CreationTime）被 NTFS tunneling 保留，证明等待或沿用 ctime 都不可靠。v3 改用 native ChangeTime、LastWriteTime 与 File ID，并按前态大小精确读取后额外探测 1 byte，避免持续追加导致无界 hash；真实 Windows 测试验证 ChangeTime 区别于 tunneled CreationTime，纯逻辑测试再模拟其余对象身份复用并确认整批零文件副作用 | 独立复审无阻断 finding；rollback 复核 `89 passed, 4 skipped`；Backend 最终全量通过 |
| B7-14 | MCP conformance、Node LTS 与工具链锁 | Windows CI 的 Backend 与 golden gate 均通过后，官方 conformance CLI 在 Node 20 因使用 `fs.globSync` 且依赖要求 Node `>=22` 而启动失败。源码开发最低版本提升到 Node 22，CI、CodeQL、安全审计及候选/正式发布工作流统一使用当前 Node 24 LTS。conformance `0.1.16` 改为根级 exact devDependency，提交完整 SRI lock，CI/发布以 `npm ci --ignore-scripts --engine-strict` 安装；根 lock 同时纳入 Dependabot、依赖门禁、audit 与 SBOM，消除带发布权限流水线中的 `npx` 传递依赖漂移 | Node 24 根 lock clean install；本地官方 conformance：initialize `1/1`、tools `1/1`、SSE retry/resume `3/3`，全部通过；workflow/lock/startup 契约 `190 passed`；root npm audit 0，JSON/YAML 解析、依赖锁与 repo hygiene 通过 |
| B7-15 | 发布 conformance producer/sealer 隔离 | 单纯 scrub 子进程 env 不能阻止第三方 CLI 改写同一工作区、污染后续解释器或留下后台进程，因此不再把它当发布安全边界。Candidate 先在无 environment、仅 `contents: read` 的 producer 运行 exact-lock CLI 并只上传原始 `checks.json`，再由 clean checkout、无第三方依赖的 sealer 解析同一份 bytes，输出 `mcp-conformance-job-evidence/v2`：绑定 repository、commit、run/attempt、根 lock SHA-256、conformance/Node 精确版本、完整 results summary 与原始结果摘要；raw JSON 全层级拒绝 NaN/Infinity 及 `1e999` 形成的非有限值。Candidate RC gate 与只读 publish preflight 均在安装依赖前验证 sealed artifact；strict/candidate/paid 不执行第三方 CLI，非 strict 本地 env 最小化仅作纵深防御。默认 setup 的 workspace QA 安装也与 UI skip 解耦 | evidence/summary、目录歧义、重复/未知检查、替换、篡改及非有限数专项通过；本地官方 conformance initialize `1/1`、tools `1/1`、SSE retry/resume `3/3` |
| B7-16 | 真实 LLM 正式证据不可变绑定 | 正式 producer 仅在 provider 调用 step 注入受保护 API key，以 `--quality-gate --release-evidence` 固定档案运行；拒绝 mock、过滤器、任务/阈值/超时/报告路径覆盖，完整物化当前 25 个 eligible golden + 105 个 versioned benchmark。报告以 exclusive temp+hardlink 发布且拒绝 symlink/reparse、覆盖、身份切换和双读 TOCTOU。独立 clean sealer 无 environment、密钥或依赖安装，按当前语料逐任务重算 intent/tool/risk/schema/chat/denial/memory/benchmark contract、summary 与 quality gate，再输出 candidate-bound `real-llm-quality-evidence/v2`；所有有计划或拒绝语义的 corpus 都必须声明权威 `global_risk`，early DENY 必须由真实 `SafetyReview`、`run.denied` 与 actual/expected risk 共同绑定。普通 PR/非默认分支 CI 不取得 key，advisory skip 不能冒充正式 evidence | evidence 定向 `60 passed`；golden/real-LLM 完整相关回归 `280 passed`；语料扫描 plan/denied 风险遗漏为 0 |
| B7-17 | 候选产物 run-attempt 与发布资产闭环 | Candidate、reviewed evidence 及两类 quality evidence 这四类 promotion-input artifact 名称全部绑定 run ID + attempt 并固定保留 30 天；两类 raw producer 输出保留 14 天。Attempt-bound publish preflight diagnostics 只是发布运行的诊断输出，不是 promotion input。局部 rerun 缺 producer/sealer 时 fail-closed。Readiness 优先使用显式 candidate repository/commit/run identity。Publish 从精确 attempt 下载候选与 reviewed evidence，保持 draft；只有远端 release tag、target candidate commit、唯一资产名、uploaded state、SHA-256 digest 和 byte size 全部一致才转正式发布 | delivery/readiness/signing/startup/quality-evidence 合同组合通过；YAML 解析与 artifact 命名/远端资产负向契约通过 |
| B7-18 | Clean publish、tag 与 readiness 精确绑定 | 仓库代码 preflight 降为 `contents: read`；唯一 `contents: write` 移入全新 Windows runner 的单一内联步骤，该 job 不 checkout、不安装依赖、不执行仓库脚本，只下载原 candidate run-attempt artifact，并重新验证固定 8 个 manifest subject、provenance/SBOM attestation、SBOM predicate 与固定 9 个发布路径；删除可变资产清单文件。发布变更前、转公开前与公开后均经 GitHub API 递归 peel remote tag 到同一 candidate commit，并在每个 checkpoint 重新读取 repository 元数据以拒绝运行中默认分支重命名；reviewed-evidence 封存也执行同样的 live default-branch 复核。Strict readiness 改为完整 40 位 SHA、candidate run ID/attempt 精确匹配，candidate attempt 缺失不得回退 publish attempt；`.tmp`/反斜杠路径先解析并限制在证据根目录，Actions URL 只接受当前 repository/run 的规范路径 | clean-job 权限/无 checkout/无仓库执行契约、PowerShell AST、remote tag/default branch 前后绑定及 readiness 绕过测试通过 |

### 批 7 验证

- Backend 最终全量：`4255 passed, 17 skipped`（10 分 34 秒，Windows）。
- 发布、证据、golden、Android 契约与启动脚本最终组合：`591 passed`（2 分 37 秒）；官方 MCP conformance 实跑 initialize `1/1`、tools `1/1`、SSE retry/resume `3/3`。
- Desktop 最终复测：typecheck、`98 files / 432 passed, 1 skipped`、production renderer build 与 bundle budget 全通过；renderer entry 201.7 KB、最大 chunk 129.8 KB、JS 总量 748.6 KB、CSS 186.0 KB。
- Mobile 最终复测：typecheck 与 task-companion behavior smoke 通过；此前 npm10 clean install、有效依赖树、Expo compatibility、EAS CLI 与 Android 编译门禁结果保持通过。
- PR-range Ruff/format（308 个 Backend Python 文件）及二次修复的 14 个 Python 文件、`git diff --check`、异常边界门禁、依赖哈希锁、repo hygiene、secret scan 与 agentic threat model 全通过。
- 依赖审计全通过：Workspace QA/Desktop/Mobile `npm audit` 均 0，四份 Python lock 均无已知漏洞；CycloneDX SBOM 共 1390 个组件（npm 1258、Python 132）。
- Maintainability gate 通过：1048 个文件、P95 `799/800`，Backend 最大文件 1399 行（阈值 1400）。

### 剩余风险与外部条件

- 应用内文件写与回滚已由共享路径锁闭合；v3 能拒绝普通同名重建、NTFS tunneling 与采集期间变化，但外部进程仍可能在路径授权、状态比较与后续操作之间抢写，具备直接文件系统权限的进程也可能刻意克隆可写元数据。彻底消除这类跨进程 TOCTOU 需要后续 native handle-bound 重构。
- Windows DACL helper 超时时，若受信 PowerShell 派生的系统工具在父进程结束后仍短暂占用目标，身份安全的清理可能保留一个仅当前用户可访问的私钥文件；生成仍失败且不会生成公钥。彻底约束后代进程与删除完成性需要后续 Windows Job Object 集成。
- 未纳入 task journal 的 direct 路由没有 durable replay 记录；由于缺少可信执行前后状态，这些路径已统一阻止自动回滚并转人工修复，而不是猜测性补偿。
- `expo-doctor` 当前为 `19/22`：本地精确 pin EAS CLI 与提交原生 Android 目录是项目为可复现构建和原生安全审计做出的有意选择；另有 Expo SDK 56 / React Native 0.85 所带 Hermes V1 已知内存回归，彻底修复需独立升级至 Expo SDK 57 / React Native 0.86.2 以上并重新完成真机回归，本批不做跨代升级。
- Mobile approval WebSocket 当前对已鉴权服务端 JSON 使用 TypeScript 类型断言而非完整运行时 schema；异常结构会被外围 catch/polling fallback 吸收，但后续仍应增加有数组上限和嵌套字段约束的 `parseApprovalEvent`，作为客户端韧性加固，不应误当作授权边界。
- Maintainability P95 为 `799/800`，余量仅 1 行；后续新增共享执行逻辑应优先继续拆分，而不是扩大豁免。
- 根级 QA/交付工具链当前已在 Node 22.0、22.20 与 24 实测 clean install，并在 Node 22/24 跑通 MCP conformance；正式 CI/发布统一使用 Node 24。为防未来 lock 更新引入未声明的 Node 24-only API，后续应增加独立轻量 Node 22 最低版本门禁，无需复制 Backend 全量矩阵。
- MCP 与真实 LLM 的不受信 producer 已和 clean evidence sealer 分离，最终 GitHub 写操作也已移入不 checkout、不安装依赖、不执行仓库脚本的 clean publish job；candidate 签名/attestation 仍与此前执行过仓库构建代码的 runner 共存。Repository/environment secrets 与显式 shell/`gh` CLI token 注入已收敛到需要的 step 且输入有摘要/allowlist 绑定；GitHub 自动创建的 `GITHUB_TOKEN` 仍以 job-level 最小 `permissions` 为能力边界，未显式导出 `GH_TOKEN` 不代表 action 无法访问 `github.token`。进一步缩小 runner 级供应链 blast radius 仍需把 build/test 与 clean sign/attest 拆成独立 job。
- Candidate 成功后到最终 publish 完成前，default branch 必须冻结在同一 candidate commit；若分支已前进，应生成并重新评审新候选。Protected branch/tag rulesets、release freeze 与 immutable-tag 记录、production environment reviewer、受限 `contents: write` 与 `.github/CODEOWNERS` 的真实多人治理，以及 Android 真机、clean-machine、人工结果质量与 release-owner sign-off 等发布证据都属于外部上线条件；workflow 的发布前后 tag 复核能检测漂移，但不能单独赋予 tag 不可变性，本次审查不伪造人员、设备、治理配置或人工证据。
