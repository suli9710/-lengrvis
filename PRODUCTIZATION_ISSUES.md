# Lengrvis 产品化问题清单

> 来源：2026-06-07 多 agent 代码审计与竞品对比。安全硬化项暂缓处理，本清单优先收敛“能否像一个成熟产品交付、安装、演示、留存、跨端使用”的问题。

## 目标

- 把 Lengrvis 从“能跑的工程原型”推进到“可交付、可演示、可回归、可解释失败原因”的产品状态。
- 优先解决影响首日体验、发布可信度、跨端闭环、竞品叙事的缺口。
- 安全问题不在本轮展开，但涉及发布阻断的安全门禁缺口会保留为产品化门禁项。

## 当前证据边界快照（2026-06-09）

> 测试数量口径：本文件保留历史开发证据和定向套件证据，但不得作为“当前最新通过数”引用。当前发布证据以 `docs/release/current-release-evidence.md` 为准；测试数引用规则见 `docs/qa/test-evidence-policy.md`。

- **新手发布口径（fail-closed）**：当前文档只能证明 dirty workspace 的开发回归和若干 artifact/template 证据正在收敛，不能写成 release ready。与 `docs/qa/release-gate.md` 保持一致：release evidence packet 仍应记录 `release_ready=false`、`claimable_release_signoff=false`，并把 clean-machine 本地模型、移动真机 LAN/WSS、自然语言结果质量签收、诊断包外发内容复核/`public_safe=false`、RC handoff 作为阻断项或 residual risks；缺任何一项都不要 tag、publish、announce 或宣称可发布。
- **证据分层口径**：dev smoke/typecheck/unit evidence 只代表当前 dirty workspace 的开发回归信心；packaged evidence 代表打包产物路径被启动或检查，但不自动等于 clean-machine 或 RC sign-off；clean-machine evidence 必须来自干净机器/干净 profile 的安装、启动、模型安装/拉取或明确 blocked handoff；real-device evidence 必须来自目标手机/模拟器的相机、LAN/WSS、证书信任路径和截图/日志复核；RC sign-off 必须绑定候选 commit/build、平台、完整命令、人工 P1 检查、waiver 与 residual risks。
- **历史自动门禁证据**：本节记录 2026-06-09 前后的 dirty-worktree 开发回归和定向套件结果。不要把这里的硬编码测试数写成“最新通过数”；需要引用时必须同时带 exact command、日期、commit/workspace label 和日志/CI artifact。审查提出的 public task text 裸文件名/hidden prompt 泄漏、malformed realtime 原文采样泄漏、QR transport metadata 可被手动输入绕过三个 P1 已关闭；前序 P1 review findings 也已在当前工作区关闭。`git diff --check` 当时 exit 0，只有 LF-to-CRLF working-copy conversion warnings。上述结果都是 dirty workspace 的开发/格式证据，不等同于 release candidate commit/build sign-off、clean-machine RC 验收、真机验收或外部人工签收。
- **Dev-Evidence 口径同步**：2026-06-09 mobile/remote combined pytest 以 `python -m pytest backend/tests/test_mobile_pairing.py backend/tests/test_remote_desktop.py -q` 为准，历史本地结果为 `132 passed`；旧的 `88 passed`/`28 passed` 可作为单文件历史子集，不要写成最新 combined gate。`mobile_remote_input_active_grant_contract` evidence bucket 只索引 mobile UI/client/smoke source markers，`latest_execution_status=not_run_by_this_packet`，不能替代 `npm --prefix mobile run smoke:remote-input-grant` 实跑、backend TestClient、desktop smoke、packaged smoke、clean-machine RC 或真实设备 LAN/WSS 证据。scheduler/preflight 计数只有在附 exact command/log 时才可引用；不要把未绑定命令的 `9 passed` 当作可复用证据。
- **Desktop diagnostics/update boundary**：当前能力是本机版本与诊断可见，而不是在线更新器。`GET /api/system/diagnostics` 返回 `product.version`、`update_channel.status=not_configured`、`check_action=refresh_local_status`、本地发布说明、`local_paths.data_dir/database/log_dirs`、audit verification、LAN readiness、local model readiness、`product_metrics` 和 `product_funnel`；桌面“系统信息”展示桌面/后端版本、“刷新本机状态”、日志目录、导出诊断包入口和本地保存位置。`POST /api/system/diagnostics/export` 写入 `<data_dir>\diagnostic-packages\lengrvis-diagnostics-*.json`，导出包会把 data/database/log 绝对路径、release notes 路径和进程用户名标签化，并覆盖 secrets、任务正文、设备名、grant id、pairing code、模型路径、任务录屏图片/文件名/路径等种子。证据命令是 `python -m pytest backend\tests\test_system_diagnostics.py -q`（当前 8 passed）与 `npm --prefix desktop run smoke:system-diagnostics-ui`；这不能写成完整在线自动更新、自动安装更新、完整 crash/update pipeline、clean-machine RC sign-off，或诊断包可以公开发布。
- **External diagnostics review/sign-off gap**：`support_package_redaction.external_review`、`collect_release_evidence_packet.ps1`、诊断导出 pytest 和 UI smoke 只能证明脱敏契约、字段存在和 handoff 模板；它们没有逐项复核任何真实导出的诊断包内容。诊断包外发前仍需要人工检查实际包里的路径、日志片段、组织信息、设备/任务/模型痕迹和其他上下文泄漏，但这项人工内容复核仍不是 `public-safe` approval、clean-machine 验收、RC sign-off 或完整发布签收；`public_safe` 必须保持 false，复核结果只能作为外发内容检查状态记录。
- **Task recording / replay privacy boundary**：任务录屏/步骤截图是本机 opt-in 证据，不是默认采集。默认 `LENGRVIS_TASK_RECORDING_ENABLED` 未设置时不采集，只有显式开启或测试专用 `LENGRVIS_TASK_RECORDING_FORCE=1` 才会写入本机 SQLite BLOB；perception 截屏不写入 task recordings。`GET /api/tasks`、`/timeline`、`/replay`、`/agent-messages`、`/safety-reviews`、`/progress` 和 `/explain` 只返回 redacted summary、计数、状态、result_quality 和标签；tool args/result、agent message 正文、review reasons、metadata、hidden prompt、文件正文、本地路径、裸文件名、截图 URL、file name 和 recording id 都不能作为公开 timeline/replay 输出。`/api/system/diagnostics/export` 只保留 `task_recording=status_only_no_images_or_file_names`，不包含图片、文件名或录屏路径。原始图片只能通过显式本机文件名路由读取，不能从 timeline/replay 自动发现。证据来自 `backend/tests/test_task_recordings.py`、`backend/tests/test_tasks_replay.py` 和 `backend/tests/test_system_diagnostics.py`；本轮 `backend/tests/test_tasks_replay.py` 追加裸文件名和 hidden/developer prompt public-surface 回归。剩余缺口是真实 Electron timeline/replay UX、手机端任务查看 UX、真实设备截图/录屏证据和外发诊断包逐项安全复核。
- **Remote WS / realtime client-error boundary**：远程屏幕/远程输入 WebSocket 对客户端只返回泛化 `type=error`、稳定 `code` 和安全文案，例如 invalid control、screen temporarily unavailable、input rejected/could not be handled；原始 exception、selector、host/path/token/device 细节只能进入审计侧的 redacted error，不应回显给手机端或截图材料。桌面 malformed realtime JSON 不再展示任何 raw sample，只显示固定安全摘要与计数，避免裸文件名、hidden prompt、路径、URL 或 token 进入聊天区。非 loopback 仍要求 WSS，auth/scope 失败关闭原因保持泛化；如果配对元数据声明 backend TLS disabled 或 websocket scheme 非 WSS，手机端会按不安全局域网连接 fail-closed，不能仅凭 `https://` 外观显示“安全连接已开启”。2026-06-09 历史定向运行可记录为既有 remote auth/revoke/expiry smoke 加后端定向测试证据：`python -m pytest backend/tests/test_mobile_pairing.py backend/tests/test_remote_desktop.py -q` 为 `132 passed`，覆盖 auth/scope、query-token rejection、remote view/input 交叉 scope rejection、revoke/expiry/disable close behavior，以及 invalid screen control、screen capture failure、unsupported input、policy/tool rejection 和 remote input unexpected exception redaction 等 targeted backend branches；`npm --prefix mobile run smoke:token` 覆盖 misconfigured HTTPS/TLS-disabled metadata 阻断及 PairScreen QR metadata 不能被手动输入切换绕过；真实 Android/WSS UX、弱网/锁屏/后台证据仍未完成。
- **Mobile approval/task payload redaction boundary**：移动端审批 list/detail/WebSocket created event 面向手机只暴露脱敏审批证据；model action nested args、本地路径文本、selector、token、value、support note 等会被压成 redacted keys/安全摘要，不能把桌面内部 plan args 或私有文件路径展示到手机截图。手机任务 companion payload 现在携带严格 completion_evidence 摘要，只有 `completed_result + result_verified=true + signoff=false` 才能显示为可核对完成结果，宽泛 `result_verified`/`credibility=verified` 不能单独触发完成结果文案。2026-06-09 后端定向证据来自 `python -m pytest backend/tests/test_mobile_pairing.py backend/tests/test_remote_desktop.py -q` 的 combined `132 passed`，覆盖移动审批脱敏、移动 token scope、设备绑定、LAN TLS metadata、companion task 边界与严格 completion evidence；这仍不是手机真机审批截图、锁屏通知、代理抓包或 LAN/WSS artifact review。
- **Skill Product Manifest remaining gap**：自动化证据已覆盖 backend manifest/schema/catalog、非私有 showcase 样本、`legacy.unspecified` 回归，以及 `desktop/scripts/skill-manifest-ui-smoke.cjs` 的 Vite/Playwright DOM 渲染 smoke；该 smoke mock `/api/skills` 并生成 `.tmp/qa-evidence/skill-manifest-ui-smoke.png`，证明 declared permissions 与 inferred signals 在 UI 中分开标注。当前未看到真实 release-candidate import 完成证据；zip/schema path 相关测试只能算导入安全边界证据，不能写成 marketplace 或真实导入通过。剩余缺口是真实 release-candidate import path、更多生产样本、签名/市场分发证据。
- **Portable GUI task evidence boundary**：自动化证据已覆盖 `release:check` 的可执行 backend/portable backend `/health` smoke，以及 `npm run smoke:portable-first-screen` 的 portable 窗口、后端健康、token-authenticated read-only diagnostics GET 和 packaged renderer DOM 点击“检查电脑状态”。2026-06-08 记录的证据来源是 `.tmp\portable-first-screen-smoke\run-20260608-154045-41396-6013e259\portable.status.log`：read-only entry 为 `[pass]`，packaged renderer 已观察到 `/api/system/diagnostics` 和只读诊断文案，且该只读点击后仍为 `tasks=0`、`runs=0`、`chat messages=0`、`diagnostic-packages=0`。随后自然语言命令 dock 填入 `帮我检查这台电脑`，记录为 `[pass] portable renderer DOM natural-language read-only task evidence passed`，观察到 packaged renderer `POST /api/runs`，并由后端生成 read-only/system diagnostics task evidence：`task_99963aecac4841d2af25feb2f675c2ad`，同次统计为 `tasks=1`、`runs=1`、`chat messages=0`、`diagnostic-packages=0`；portable smoke 会记录 explain `completion_evidence.level` / `result_verified`。这可以写成 packaged command-dock 提交 + 只读系统诊断任务证据；只有 `completion_evidence.level=completed_result` 且 `result_verified=true` 才能称为 completed-result evidence，而且仍不是 result quality review、clean-machine 验收、RC sign-off 或 release sign-off。剩余缺口是 clean-machine 候选包 sign-off、自然语言 agent 任务完成/结果质量签收，以及平台分发验收。
- **Clean-machine/local model readiness remaining gap**：自动化证据已覆盖 privacy mode 不静默回退云端、local setup-plan、文件搜索/系统诊断/确定性文档摘要 fallback、Ollama status/setup-plan/install/start/pull/install-local-model 后端契约（Ollama backend tests `53 passed`），以及 `desktop/scripts/settings-local-model-experience-smoke.cjs` 的 Vite/Playwright Settings smoke；该 smoke 展示 quick/privacy/hybrid、本地模型下一步、推荐模型、大小、硬件、速度估计、“不静默回退云端”边界和 mock 下可点击的一键准备入口，并在 1366px desktop、900px narrow desktop 两个视口断言模型边界卡片、setup panel 和操作按钮没有横向溢出/挤压/重叠，生成 `.tmp/qa-evidence/settings-local-model-experience-smoke-desktop.png`、`.tmp/qa-evidence/settings-local-model-experience-smoke-desktop-setup.png`、`.tmp/qa-evidence/settings-local-model-experience-smoke-narrow.png`、`.tmp/qa-evidence/settings-local-model-experience-smoke-narrow-setup.png`。但这些仍是后端契约与 mocked/Vite 证据，没有证明干净机器上一键安装、真实启动、真实拉取推荐模型、bundled/offline model 可用、真实 local model smoke 或失败修复按钮闭环；该 Vite/mock 视觉回归也不是 packaged release-candidate Settings UX 签收。
- **Mobile real camera/LAN TLS remaining gap**：自动化证据已覆盖桌面 QR payload/PNG 生成、移动端 payload parser、PairScreen 内置 `expo-camera` / `CameraView` 二维码扫码入口及 native camera permission/source smoke 断言、本地 HTTP/WS behavior stub、非 loopback HTTP LAN token-bearing flow 阻断、后端设备绑定，以及 LAN TLS ready/misconfigured metadata 的后端测试；但没有证明真机/模拟器相机真实完成扫码配对、真实 LAN router/firewall 路径、HTTPS/WSS 服务端到设备侧的证书信任链或显式 trust path。
- **当前仍需置顶的 P1 缺口**：Task Workspace 和后端/mobile result_quality 已能区分 verified result、visible progress、safe failure、task evidence only，并通过开发 smoke/API tests 锁住“不能过度声称完成”；但自然语言真实任务仍缺人工确认用户可读结果、下一步/成果物和 result quality sign-off。本地模型还缺 clean-machine packaged install/start/pull 与真实 local model task；移动端还缺真机/模拟器 camera QR、LAN/WSS、证书信任和红线截图/日志复核。

## P0 发布门禁与验收

- [x] **建立黄金任务 E2E 回归集并纳入发布门禁。**
  - 证据：`test_data/golden_tasks/golden_tasks.json`（≥30 条真实任务，覆盖 system/cleanup/approval/safety/file/document/chat/files_api 八类）+ `backend/tests/test_golden_tasks.py`（2026-06-10 本地 `34 passed`，含数据集完整性守护）。断言对象是关键产物而非返回码：计划工具序列、全局风险等级、审批数量、审批前后文件副作用、工具输出结构（诊断字段/重复组/摘要 note/引用数）、R4 拒绝与 `SecurityError` 越权边界。套件位于 `backend/tests` 下，`qa:gate`/`release:check` 自动执行；`npm run golden:gate` 产出通过率报告（`.tmp/qa-evidence/golden-tasks/golden-tasks-report.json`，阈值 95%）。
  - 修复：建套件时发现 goal 级安全拒绝（如"读取浏览器保存的密码"）在 run 相位上显示为 `cancelled` 而非 `denied`（`TaskStatus.DENIED` 落库为 cancelled 且 goal 拒绝路径无 plan 可供升级)；已修正 `os_execution_engine._create_reviewed_plan` 的拒绝摘要与 `run_service._phase_for_task_plan` 的 plan-None 短路，并由黄金任务 `gt-run-deny-*` 锁住回归。同日全量 backend pytest 曾本地复跑通过（历史 dirty-worktree 开发证据；不要引用为当前 release evidence）。
  - 边界：本套件用 MockProvider/确定性规划器/extractive fallback 离线运行，是机器自证版本回归证据；不证明真实 LLM 结果质量，不等同真人评分、clean-machine、真机或 RC sign-off。真人结果质量基线（成功率≥90%、返工≤10%、签字归档）仍为开放项，流程见 `docs/qa/golden-tasks.md` 与 `npm run evidence:result-quality-review`。

- [x] **修复 Android release gate 脚本在 PowerShell 7（含 preview）父进程下的可移植性缺陷。**
  - 证据：`scripts/verify_android_release_gate.ps1` 此前依赖 `Get-FileHash` 与模块自动加载；当父进程是 pwsh 7 时 PSModulePath 被前置 7.x 模块目录污染，Windows PowerShell 5.1 子进程无法解析 `Microsoft.PowerShell.Utility`（Get-FileHash/ConvertTo-Json 缺失），严格 gate 在写出 redacted packet 之前崩溃，`backend/tests/test_android_release_gate.py` 与 `test_start_app_script.py` 共 2 项失败。已改为 .NET SHA256 哈希并在 Desktop edition 下显式从 `$PSHOME` 导入 Utility/Management；2026-06-10 本地定向重跑通过，全量 backend 结果只保留为历史开发证据。
  - 附带约定：`scripts/*.ps1` 必须保持 ASCII/英文注释（仓库 ps1 为 UTF-8 无 BOM，Windows PowerShell 5.1 会按 ANSI 解析多字节注释并吞掉换行导致语法错误）。
  - 边界：这是开发机修复与回归证据，不替代 clean-machine 安装验收或真机 Android gate 证据。

- [x] **补齐依赖漏洞扫描入口与安全披露文档。**
  - 证据：根目录 `SECURITY.md`（报告渠道、响应 SLA、高优先级攻击面、协同披露）；`npm run audit:deps`（desktop/mobile `npm audit --audit-level=high` + backend `pip-audit -r requirements-lock.txt`，任一高危或 pip-audit 缺失即非零退出，可用 `-SkipPython` 显式记录 waiver）。
  - CI 接入：2026-06-10 新增 `.github/workflows/security-audit.yml`（每周一定时 + 手动触发跑 `audit:deps`）与 `.github/workflows/ci.yml`（push/PR 跑 hygiene、deps:verify、backend pytest、golden gate、desktop/mobile typecheck、mobile behavior smokes）。两个 workflow 已通过 YAML 解析校验，但**尚未在 GitHub 上发生首次绿色运行**；在首次远端运行通过之前，只能写成"CI 配置已提交"，不能写成"CI 已上线/门禁已生效"。
  - 边界：这是 SCA 入口与流程文档，不等同第三方渗透测试或外部安全背书。

- [x] **补齐 P0 gate：mobile remote-input grant smoke 必须进入统一门禁。**
  - 证据：`docs/qa/e2e-acceptance-matrix.md` 把 `npm --prefix mobile run smoke:remote-input-grant` 列为 E2E-006 P0，并把 `npm --prefix mobile run smoke:task-companion` 列入 E2E-010 移动伴侣验收；`scripts/run_tests.ps1`、根目录 `qa:gate` 和 `docs/qa/release-gate.md` 已纳入 mobile typecheck、`smoke:token`、`smoke:task-companion` 与 `smoke:remote-input-grant`。
  - 影响：发布流程会漏掉“远控输入授权边界”这类核心跨端能力，门禁名义上严肃，实际像纸糊的。
  - 验收：`.\scripts\run_tests.ps1`、根目录 `qa:gate`、`docs/qa/release-gate.md` 的 expanded commands 三处一致包含 mobile token、task companion 和 remote-input grant smoke；这些仍是本地行为/契约证据，不是真机 LAN/WSS、证书信任或真实设备远控验收。

- [x] **把 regex smoke 升级为行为级 smoke。**
  - 证据：`mobile/scripts/behavior-smoke-helpers.cjs` 提供本地 HTTP/WS smoke server 和原始 WebSocket 握手；`mobile/scripts/mobile-token-smoke.cjs` 会真实调用 `POST /api/pair/confirm`，验证 approvals/remote screen WebSocket 通过 `Sec-WebSocket-Protocol` 携带 token 且 URL 不含 token；`mobile/scripts/remote-input-grant-smoke.cjs` 会真实调用 grant token claim、DELETE revoke、`/ws/remote/input` 握手，并覆盖 wrong token、revoked、expired 拒绝。
  - 已核验：2026-06-08 本地执行 `npm --prefix mobile run smoke:token`、`npm --prefix mobile run smoke:task-companion` 与 `npm --prefix mobile run smoke:remote-input-grant` 均通过。
  - 剩余边界：这是自包含本地行为桩；`mobile-token-smoke` 对 PairScreen 相机入口和原生权限配置的断言属于源码/配置证明，不等同于真机 LAN、证书信任或真实设备扫码验收；这些仍应保留在 demo/release manual evidence。

- [x] **建立“一条命令发布前验证”而不是散落脚本。**
  - 证据：曾经 `scripts/run_tests.ps1`、`scripts/build_all.ps1`、`scripts/verify_packaging.ps1`、docs release gate 存在口径差；现已由根目录 `release:check` 收敛，`release:gate` 仅保留兼容别名。
  - 当前核验：2026-06-08 严格状态机下运行 `npm run release:check` 完整 exit 0；`qa:gate`、`release:safety`、结构检查、portable directory/zip source-map 检查和 backend/portable backend runnable smoke 均通过。
  - 影响：开发者不知道哪个才是准发布标准，产品质量靠记忆力。
  - 验收：根目录提供 `release:check`，`release:gate` 作为兼容别名，发布门禁文档引用 `release:check`。

- [x] **把平台卖点证据纳入 release/demo gate。**
  - 证据：`docs/qa/release-gate.md`、`docs/qa/e2e-acceptance-matrix.md` 和 `docs/demo-script.md` 已要求记录 local model readiness/smoke、mobile companion flow、Skill Product Manifest sample、document citation 和 template demo path。
  - 影响：避免演示和 release notes 只讲“本地模型、Skill、文档库、移动伴侣、模板路径”这些卖点，却没有同候选版本绑定的证据。
  - 验收：自动门禁继续使用既有 `qa:gate` / `release:check`；未自动化的平台卖点必须进入人工 gate、waiver 或 residual risk。

- [x] **补齐依赖锁验证入口与验收口径。**
  - 证据：根目录 `deps:verify` 入口验证 `desktop/package-lock.json`、`mobile/package-lock.json` 与后端 `backend/requirements-lock.txt`；后端锁现在要求完整解析后的 Python transitive lock（`uv pip compile` 生成、全量 pinned、带 `# via` resolver provenance），`docs/qa/release-gate.md` 已把 `npm run deps:verify` 列为依赖变更时必须记录的 preflight evidence。
  - 剩余风险：当前 Python lock 固定版本和传递依赖，但不包含 hash pins、包签名或 registry provenance；这些仍需作为供应链 residual risk 或单独审计证据。
  - 验收：依赖清单、lockfile 或后端 requirements 变更时，QA handoff 必须记录 `npm run deps:verify` 的命令、日期、提交和结果。

- [x] **补齐 LAN TLS readiness 的证据口径。**
  - 证据：`docs/qa/release-gate.md` 和 `docs/qa/e2e-acceptance-matrix.md` 已要求移动/LAN 演示记录 `https/wss` scheme、证书来源、设备侧显式信任路径；非 loopback HTTP LAN 只能作为 blocked-path evidence，不能作为移动 token 配对通过证据。
  - 剩余风险：这只是 readiness/configuration/manual evidence，不代表系统级证书信任链已经完成；真机 HTTPS/WSS 信任路径仍需候选版本人工证据。
  - 验收：任何 demo/release 文案提到 LAN TLS、HTTPS/WSS 或证书信任时，必须附候选版本手工证据；否则只能记为 residual risk。

- [x] **补齐 Skill 样本迁移的自动/人工验收口径。**
  - 证据：`docs/qa/e2e-acceptance-matrix.md` 已把 E2E-019 写成可复制的 pytest 命令，并要求记录迁移样本 id/source、import path 和 Product Manifest 卡片证据。
  - 边界更新：Product Manifest 证据必须区分 manifest 声明的权限和从名称、描述、安全文案推断出的风险信号；推断信号只能作为 UX 提醒，不能写成权威权限边界。
  - 剩余风险：这证明样本迁移和 manifest 风险表达有验收口径，不代表 Skill 生态已有足够多真实生产样本；也不代表当前 UI 截图已经完成“声明权限 vs 推断信号”的最终分栏验收。
  - 验收：每个 release candidate 至少导入或展示一个非私有迁移样本；样本不得回退到 `legacy.unspecified` 权限表达；截图或 handoff notes 必须标清 declared permissions 与 inferred signals。

## P0 安装、打包、分发

- [x] **打包验证从“文件存在”升级为“可运行”。**
  - 证据：`release:smoke` 和 `scripts\build_all.ps1 -VerifyOnly -RunExecutableSmoke` 已提供 release runnable smoke；`docs/qa/release-gate.md` 与 E2E-012 已要求记录 structural verification、runnable smoke 和 `.tmp\packaging-smoke` 失败诊断。
  - 历史核验：2026-06-08 严格状态机下 `npm run release:check` 完整 exit 0；后续曾有 `npm run qa:gate` 完整 exit 0 的 dirty-worktree 开发证据，包含 backend pytest、desktop/mobile typecheck、mobile behavior smoke 与 desktop smoke。release:check 的结构检查、`dist\backend.exe` `/health` smoke、portable backend `/health` smoke 仍以前述历史门禁结果为证据；当前发布状态必须看 `docs/release/current-release-evidence.md`。
  - 历史补充：2026-06-08 本地执行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\portable_first_screen_smoke.ps1 -TimeoutSeconds 60 -RemoveTempOnPass` 通过，记录证据目录为 `.tmp\portable-first-screen-smoke\run-20260608-154045-41396-6013e259`。portable 窗口进程出现，`/health` 可达，带一次性 desktop token 的 `GET /api/system/diagnostics` 返回 `diagnostic_scope=local_only`、`product=Lengrvis`，并确认临时 data/database 路径；脚本还通过 packaged renderer DOM 点击“检查电脑状态”，观察系统信息/只读诊断文案，并确认该只读点击没有 diagnostics export 包、chat/run/task 写入。
  - 自然语言边界：同一证据日志显示 command dock 填入 `帮我检查这台电脑` 后自然语言证据为 `[pass]`，原因是 packaged renderer 观察到预期的 `POST /api/runs`，且后端生成 read-only/system diagnostics task evidence：`task_99963aecac4841d2af25feb2f675c2ad`；统计为 `tasks=1`、`runs=1`、`chat messages=0`、`diagnostic-packages=0`。portable smoke 会记录 explain `completion_evidence.level` / `result_verified`，但只有 `completion_evidence.level=completed_result` 且 `result_verified=true` 才能称为 completed-result evidence；这仍不是结果质量签收、clean-machine 验收、RC sign-off 或 release sign-off。因此当前只能写成 packaged natural-language command-dock submission + 后端只读系统诊断任务证据，不能写成完整任务完成、结果质量签收、clean-machine 验收或 release-candidate sign-off。
  - 剩余缺口：干净机器真实启动、自然语言 agent 任务完成/结果、真实设备/平台分发和候选版本 sign-off 仍保留为人工 P1 evidence；自动 gate 已覆盖 release artifacts、source-map policy、PE header、launcher preflight、backend runnable smoke、portable launcher/backend 只读诊断 smoke、packaged renderer 只读入口点击，以及自然语言 command dock 的 `/api/runs` 提交与后端 read-only/system diagnostics task evidence。
  - 验收：Windows release candidate 必须跑 `npm run release:check` 或等价命令并完整 exit 0；portable launcher/backend 只读诊断 smoke 必须单独记录；portable GUI 证据必须区分 read-only entry pass、natural-language `/api/runs` submission/task-evidence pass、natural-language visible-safe-failure/unsupported、`completed_result + result_verified=true` completed-result evidence，以及 natural-language result quality / RC sign-off。

- [x] **明确发布产物矩阵：Windows、macOS、Android 分别到什么完成度。**
  - 证据：`desktop/package.json` 有 mac dist 脚本，根目录脚本和启动文档偏 Windows/PowerShell；移动端是 Expo app，但分发状态不清。
  - 影响：对外承诺跨平台，实际交付像 Windows-only 内测包。
  - 验收：README 顶部已给出平台支持表，标注 `Supported / Preview / Planned`，并写明当前交付与已知限制。

- [x] **停止在正式启动脚本里现场安装依赖。**
  - 证据：`scripts/start_app.ps1` 不再执行 `pip install` / `npm install`；旧 `-InstallMissingDependencies` 参数会明确拒绝并指向 `scripts/setup_dev.ps1`；README 已拆分“正式包直接启动”和“源码开发先 setup”。
  - 已补闭环：开发依赖安装迁到 `scripts/setup_dev.ps1` / `scripts/dev.ps1 -InstallMissingDependencies`；依赖锁验证已有 `deps:verify` 入口和后端 transitive lock 证据，可在发布前阻断明显 drift。
  - 剩余风险：Python lock 已完整解析传递依赖并固定版本，但尚未强制 hash pins、包签名或 registry provenance。
  - 影响：用户第一次启动时网络、registry、依赖新版本都可能把体验炸掉。
  - 验收：正式启动脚本只启动已锁定产物；开发环境安装迁到 `setup` 脚本；依赖使用 lock/constraints 固定。

- [ ] **补齐桌面在线更新；版本展示、本机刷新、故障日志入口已有边界证据。**
  - 证据：设置/系统区域已经能展示桌面版本、后端版本、后端诊断、日志目录、本地发布说明和“刷新本机状态”；该刷新只会重新读取本机版本、服务状态和诊断快照，不会调用在线 updater、下载更新或自动安装更新。后端 `GET /api/system/diagnostics` 返回 `update_channel.status=not_configured`、`check_action=refresh_local_status`、`local_paths.data_dir/database/log_dirs`、audit verification、LAN readiness、local model readiness、`product_metrics` 和 `product_funnel`。
  - 诊断包导出：`POST /api/system/diagnostics/export` 会在 `<data_dir>\diagnostic-packages` 写入本地 JSON 支持包。`backend/tests/test_system_diagnostics.py` 覆盖导出包 schema、版本、路径标签、进程用户名标签、release notes 路径标签，以及 API key、任务正文、tool 输出 secret、approval message secret、设备名、grant id、pairing code、模型路径、组织式路径片段和任务录屏图片/文件名/路径不进入导出包；当前文件已扩至 8 个诊断用例，本轮本地重跑为 `8 passed`。
  - UI smoke：`npm --prefix desktop run smoke:system-diagnostics-ui` 在 Vite/Playwright 预览里 mock diagnostics/settings 后端，断言“版本与更新”卡只显示本机版本/本地发布说明，“刷新本机状态”不会请求在线 updater endpoints；同时断言诊断包文案说明脱敏路径、本机范围摘要、导出必须由用户点击触发，且成功状态不会把完整本机路径写成可公开信息。
  - 边界更新：本地 diagnostics payload 和 UI 可以显示诊断包保存位置、data/database/log 路径和进程用户名，方便同机排障；面向分享的 diagnostics export 当前会写入 `support_package_redaction`，把 data/database/log 绝对路径替换为 path labels，并把进程用户名替换为本地用户标签。该证据覆盖当前测试种子和导出包路径/用户名红线，仍不能写成完整 crash/update pipeline、“任意日志内容都已安全公开”或“诊断包可以公开发布”。
  - 剩余缺口：完整在线自动更新、自动下载/安装更新、崩溃收集与 update pipeline、clean-machine RC sign-off 仍未完成；诊断包 UI 不能替代完整 crash/update pipeline，也不能过度表述为 shareable support bundle 可以放心公开。
  - 影响：真实用户遇到问题只能截图喊救命，不像产品。
  - 验收：设置/系统页展示桌面版本、后端版本、后端状态、本机更新状态、本地发布说明、日志目录、导出诊断包按钮；文档和 release notes 明确当前只有本机刷新与导出诊断证据，没有完整在线更新或 crash/update pipeline。

## P1 首次体验与核心闭环

- [x] **缩短 time-to-first-win：首屏必须引导用户完成一个真实任务。**
  - 证据：产品能力很多，但 README/桌面 UI 更偏能力陈列；竞品 Marvis/Copilot/ChatGPT Agent 都强调“马上替你做事”。
  - 当前核验：`npm --prefix desktop run smoke:first-launch` 已在本轮修复后通过，覆盖首屏只读“检查电脑状态”模板、系统检查页面、Home 成果区 recent-result/fallback、Task Workspace 只读边界，以及后端不可用时的离线连接引导和禁用发送动作。该 smoke 是 Vite/Chromium 预览证据，不替代 packaged Electron main/preload 生命周期或 clean-machine portable GUI 任务签收。
  - 影响：用户打开后不知道第一件事该让它做什么。
  - 验收：首屏已提供 5 个任务模板：整理下载目录、总结本地文档、查找大文件、检查电脑状态、文档问答；每个模板展示本机处理、云端边界、审批、回滚、预计耗时。

- [ ] **补齐自然语言结果质量与 Task Workspace 证据。**
  - 证据：portable smoke 已证明 `帮我检查这台电脑` 能在 packaged renderer 里触发 `/api/runs`，并生成后端只读系统诊断任务证据；first-launch smoke 也覆盖 Task Workspace 的只读边界。2026-06-10 起黄金任务回归集（`backend/tests/test_golden_tasks.py`，≥30 条）进一步锁住自然语言任务的路由、风险分级、审批与产物契约（机器自证）。
  - 剩余缺口：这还不是用户可读成果质量签收，也不证明 Task Workspace 已展示可复核的结果摘要、下一步、成果物或失败行动建议；不能把 submission/task evidence 写成 completed task-result、result quality review 或 RC sign-off。
  - 验收：候选版本必须在 Task Workspace 中展示一个自然语言只读任务的可读结果、来源/系统诊断边界、无写入副作用、下一步或成果物，并记录人工结果质量结论；失败路径必须给出用户可执行的下一步。

- [ ] **把本地模型能力做成可完成路径，而不是配置谜题。**
  - 证据：设置页已经把快速、隐私、智能混合三档做成用户可理解的 Model Boundary Profile，并展示推荐模型、大小、硬件、速度预估和修复动作。
  - 历史补充：2026-06-08 `backend/tests/test_privacy_mode_offline_eval.py` 覆盖隐私模式无本地 LLM 时不构造云端/Mock provider，返回本地模型 setup-plan；同文件还证明文件搜索保持本地边界，首屏 `检查电脑状态` 可通过确定性 `system.diagnostics` 完成并展示本地 AI readiness，不需要 LLM 规划。
  - 剩余缺口：真实“一键安装/启动推荐模型”、local model smoke、失败修复按钮和 clean-machine 候选版本证据仍需闭环；不能把当前证据表述为“默认离线模型已随包可用”。Settings DOM/screenshot 证据已补上 1366px desktop、900px narrow desktop 的 Vite/mock 视觉回归，但仍不能替代 packaged Settings UX 签收或真实本地模型运行验收。
  - 影响：本地隐私是核心卖点，若只能看懂不能完成，仍会落后于发布级本地模式的可信体验。
  - 验收：断网时隐私模式至少完成文件搜索、简单摘要、系统查询中的两类；安装失败展示修复路径，不允许自动降级成云端处理。当前已自动化覆盖文件搜索与系统查询，简单摘要和真实本地模型安装/运行仍需候选版本证据。

- [ ] **移动端配对流程产品化。**
  - 证据：移动端已新增 pairing payload parser，behavior smoke 覆盖 JSON、`lengrvis://pair` URL 和自然语言文本中的地址/配对码解析，也会区分缺地址/缺 code；PairScreen 已内置 `expo-camera` / `CameraView` 二维码扫码入口，同时保留粘贴与手动输入 fallback，`mobile/scripts/mobile-token-smoke.cjs` 会断言 `useCameraPermissions`、`onBarcodeScanned`、QR-only scanner settings、“打开相机扫码”入口、粘贴文案和原生 camera permission 配置；桌面端已用 `qrcode` 生成真实 PNG QR data URL 并在 Settings 面板渲染，`desktop/scripts/mobile-pairing-qr-smoke.cjs` 覆盖 payload、QR 生成和渲染断言；后端测试覆盖 LAN TLS metadata 的 ready/misconfigured 口径。这些证明内置扫码源码路径存在并有自动 smoke/source 断言，但真实手机/模拟器扫码配对、LAN 真机配对和设备侧证书信任验收尚未完成。
  - 影响：演示可以，普通用户会被 IP、端口、同网段这些词劝退。
  - 验收：桌面端 QR 展示和移动端扫码源码入口已有自动化证据；剩余验收为真实手机/模拟器扫码配对、真机 LAN 路径，以及失败页在真实设备上区分“不在同一网络 / 后端未启动 / code 过期 / 权限不足”。

- [x] **远程桌面/远控做成明确的模式切换。**
  - 证据：移动端已有 RemoteScreen 和 remote input grant，但产品语义仍散在审批事件里。
  - 边界更新：远程屏幕/输入 WebSocket 面向客户端的错误只允许泛化 code/message，不回显底层异常、selector、host/path/token/device 信息；原始异常只能进入 redacted audit/log 侧。2026-06-09 mobile+remote combined 本地重跑为历史定向证据，覆盖 auth/scope、query-token rejection、remote view/input 交叉 scope rejection、revoke/expiry/disable close behavior，以及 invalid screen control、screen capture failure、unsupported input、policy/tool rejection 和 remote input unexpected exception redaction；仍需补真实 Android/WSS UX、弱网、锁屏和后台证据。
  - 影响：用户不知道现在是只读、可接管、已过期，容易恐慌或误操作。
  - 验收：移动端远程屏幕固定显示 `只读观看 / 已授权输入 / 授权剩余时间 / 结束接管` 状态与按钮；远程输入仍需短期授权。

- [x] **通知内容默认隐私保护。**
  - 证据：`mobile/src/notifications.ts` 已不再把 approval message 放到高优先级通知正文。
  - 影响：即便安全问题稍后处理，这也是产品信任问题；锁屏泄露任务内容会让用户立刻卸载。
  - 验收：默认通知只显示“有任务等待审批”，详情进入 App 后展示；设置中可选择是否显示敏感摘要。

## P1 文档、品牌、竞品叙事

- [x] **补齐面向用户与商业化的文档批次（2026-06-10）。**
  - 证据：`docs/user-guide.md`（安装到首个任务的快速上手 + FAQ + 故障排查，覆盖 5 个首屏模板、模型三档、手机配对、诊断包导出）；`docs/legal/privacy-policy-draft.md` 与 `docs/legal/eula-draft.md`（基于真实产品行为起草，**显式标注 DRAFT/未经法务定稿不得对外发布**）；`docs/business/pricing.md`（Free/Pro/Team 三档，高风险远控绑定付费层+强审批，标注 entitlement/license/支付通道均未实现）；`docs/business/target-segment.md`（定位一页纸：首选隐私敏感专业用户，3 条差异化主张，标注用户访谈未做）。
  - 边界：用户手册描述的是当前 dirty workspace 行为，候选版本发布前需按包内实际行为复核；法务两份是草稿不是可发布法务文件；定价/定位是内部定稿，对外投放前需访谈与市场验证。

- [x] **清理旧品牌/竞品名残留。**
  - 扫描证据：旧本机 checkout 路径名已从文档中移除；剩余命中仅应是竞品引用、旧 env prefix 拒绝测试和本条白名单说明；文件名无旧名命中。
  - 用户可见文案：README、启动脚本、package/mobile manifest/display name 未命中旧名，当前显示名统一为 Lengrvis。
  - 保留白名单：竞品对比文档可引用腾讯 Marvis；`backend/tests/test_env_prefixes.py` 必须保留 `MARVIS_`/`MAVRIS_` 用例，证明旧环境变量前缀不会被兼容接收。
  - 验收：代码、文档、启动脚本、截图、产物名、vendor manifest 全量 grep 无非兼容必要的旧名；后续新增外部官网/发布页/截图时必须复跑同一命名审计。

- [x] **更新过时的 parity 文档。**
  - 证据：`docs/LENGRVIS_PARITY.md` 已重写为 `已实现 / 可演示 / 需要硬化 / 未开始` 四栏路线图；file watcher、通知、手机远控、本地模型准备、release gate 等状态已按当前仓库证据重新归类。
  - 剩余风险：这是文档口径修复，不代表四栏里“可演示/需要硬化”的能力已经获得发布级人工验收。
  - 验收：文档不再把已接入的 file watcher、通知桥、远程屏幕/短授权输入写成纯占位，同时不把默认未捆绑本地模型写成开箱完成。

- [x] **重写竞品对比：别喊“杀手”，讲清差异化。**
  - 竞品事实：腾讯 Marvis 已有 Win/macOS/Android、本地模式、手机接管电脑；微软有 OS 原生入口和 agent workspace；OpenAI/Anthropic 强在模型与工具生态。
  - 当前定位：仓库内 README 与 `docs/OS_AGENT_MARKET_DIFFERENTIATION.md` 已改为“本机 OS agent + 可审计 + 可扩展 + 自托管”，并明确不硬碰平台分发和大模型品牌。
  - 剩余风险：若后续存在官网、发布页或外部营销文案，还需要按同一口径复核。
  - 验收：README/docs 避免“全面领先”“替代 Marvis”等空话，改成具体场景对比、当前限制和验收证据。

### 竞品差距 Checklist

| 对标产品 | 对方强项 | Lengrvis 当前差距 | 90 天动作 | 验收证据 |
| --- | --- | --- | --- | --- |
| 腾讯 Marvis | Win/macOS/Android 分发、本地模式、手机接管、AI 图库/文档库 | 跨端分发弱，本地模型不是开箱即用，图库/文档库消费体验不够顺 | Windows + Android demo path；隐私模式一键安装；手机审批/只读查看任务 | demo script 录屏、local model smoke、mobile companion flow |
| Microsoft Copilot+ PC | OS 原生入口、agent workspace、硬件/安全叙事 | 没有用户一眼看懂的任务隔离空间，Windows 入口不够产品化 | Task Workspace、Manifest、时间线回放、文件右键/通知轻入口 | workspace 截图、审计事件、Explorer 入口 smoke |
| ChatGPT Agent / Operator | 云端虚拟电脑、connectors、可暂停/接管、成果产出 | 任务运行中协作弱，结果区不像交付物，connector 生态弱 | 成果区、下一步按钮、Skill sample、浏览器/文档 demo | template demo path、Skill sample、document citation |
| Lengrvis Code / Computer Use | 开发者工作流、移动路由、权限/差异预览 | 手机不能完整续写任务，审批缺规则记忆/替代建议 | 手机发起/续写、审批 preview、follow-up、暂停/取消 | mobile task create/follow-up tests、approval replay |
| Manus / Genspark | 模板工作台、Slides/Sheets/Docs 成果包装 | 首页模板仍需向导化，产出库和导出成果不足 | 5 个任务向导、清理计划/摘要/表格成果区、导出路径说明 | browser smoke、demo-script、成果区截图 |

- [x] **补齐截图、录屏、演示脚本。**
  - 证据：桌面有 smoke screenshot 资源，但还没有稳定的一分钟产品演示路径。
  - 隐私边界：任务步骤录屏/截图不是默认采集能力；只有显式开启 `LENGRVIS_TASK_RECORDING_ENABLED=true` 或测试专用 force 时才写本机 task recordings。公开 timeline/replay 只能展示 redacted summary、状态、计数和截图是否存在，不返回原始图片、URL、文件名或 recording id；演示录屏仍应使用干净 profile 和脱敏素材。
  - 影响：没有 demo，产品价值只能靠讲，讲得越多越像没做完。
  - 验收：`docs/demo-script.md` 已包含 60 秒、3 分钟、10 分钟三档演示脚本；每档有准备数据、失败兜底、预期画面。

## P2 工程交付卫生

- [x] **清理根目录重复 `/app` 别名包。**
  - 证据：2026-06-10 删除根目录 `/app`（原为指向 `backend/app` 的 `__path__` 别名），`pytest.ini` 的 `pythonpath` 由 `.` 改为 `backend`，import 解析直接落到 `backend/app`，结构歧义消除。`npm run hygiene` 通过；定向回归 `test_system_diagnostics.py + test_golden_tasks.py` 通过；同日全量 backend pytest 复跑结果仅作为历史 dirty-worktree 开发证据。
  - 附带修复：`test_mobile_pairing.py` 的 `_run_mobile_jwt_subprocess` 此前以仓库根为 cwd 启动 `python -c "from app...."` 子进程，依赖已删除的根 `/app` 别名解析；已改为 cwd=`backend`（与 pythonpath 同口径），定向重跑 `test_mobile_pairing.py` 94 passed。
  - 剩余边界：GitHub 仓库名 `-lengrvis` 的前缀连字符仍在（重命名远端仓库是用户操作）；产品改名议题（市场化清单 #28）独立未启动。

- [x] **源代码 map 策略产品化。**
  - 证据：开发 watch 仍使用 `desktop/tsconfig.node.json` 保留 source map；发布构建改用 `desktop/tsconfig.node.release.json`，`desktop/vite.config.ts` 显式 `sourcemap: false`，`desktop/electron-builder.yml` 排除 `dist/**/*.map`。
  - 当前核验：2026-06-08 严格 `npm run release:check` 完整 exit 0；`desktop/scripts/source-map-policy-smoke.cjs`、portable directory source-map check、portable zip source-map check 均通过，未发现 `.js.map` 或 `sourceMappingURL`。
  - 影响：公开发布包默认不携带 renderer/main/preload/shared source map，避免内部实现随包暴露；调试构建仍可保留 map。
  - 验收：`npm run release:check` 必须在 portable directory/zip source-map 检查和 runnable smoke 上完整 exit 0。

- [x] **进程管理从模糊 kill 变成受控生命周期。**
  - 证据：`scripts/start_app.ps1` 对已占用的 backend/frontend 端口改为健康则复用、不可复用则提示用户关闭，不再按端口停止工作区或旧 Lengrvis 进程；最终清理只停止本次启动记录的 `$startedBackend` / `$startedFrontend` / `$startedDesktop` 进程对象。`backend/tests/test_start_app_script.py` 覆盖“不停止 workspace-owned full backend”“不按端口停止已发现进程”“复用或阻断已有 listener”。
  - 剩余边界：真实用户机器上的端口占用提示仍需要手工可用性验证，但自动化已防止 broad port kill 回归。
  - 验收：只管理本产品本次启动并记录 PID 的进程；占用端口给出下一步；日志记录原因。

- [x] **统一配置入口和错误文案。**
  - 证据：README、`Start-Lengrvis*.cmd`、`scripts/start_app.ps1`、Settings 和 System Info 均把普通用户引导到桌面设置与诊断导出；启动失败会指向日志/Debug 启动器，并提示普通用户不要自行编辑 `.env` 或 `config.yaml`。
  - 已核验：`backend/tests/test_start_app_script.py` 覆盖普通用户配置入口、启动器文案、redacted log tail、诊断包入口和“不现场安装依赖”提示。
  - 剩余边界：高级配置文件仍保留给开发/部署；真实安装包上的失败文案还需要候选版本人工验收。

- [x] **建立产品指标而不是只看测试绿灯。**
  - 证据：`backend/app/core/db.py` 的 `local_product_diagnostics()` 输出匿名本地 product metrics/funnel；`backend/app/api/routes_system.py` 将其接入 `/api/system/diagnostics` 与 `/api/system/diagnostics/export`，并补充 local model/Ollama/ONNX readiness。覆盖项包括配对设备、remote input grants、tasks/runs/tool_results 成败、approval pending/approved/rejected/expired、local model next action。
  - 已核验：2026-06-08 本轮本地执行 `python -m pytest backend\tests\test_system_diagnostics.py -q`，结果为 `8 passed`；测试确认诊断 payload/export 不包含 API key、任务正文、tool 输出 secret、approval message secret、设备名、grant id、pairing code 或模型路径原文，并确认 task recording 在诊断导出里只保留状态边界，不包含图片、文件名或路径。
  - 边界更新：diagnostics payload 可以保留本机 UI 需要的保存位置/日志位置提示；面向分享的 export 当前已改为 redacted path labels 和本地用户标签，避免把用户名或完整 data/database/log 绝对路径当作 support bundle 证据外发；任意日志片段和组织式路径仍需要后续扩展种子覆盖。
  - 剩余边界：这是本机匿名诊断证据，不是云端 telemetry/dashboard；首次启动成功率、模型安装成功率等仍需要后续事件埋点或 dogfood 采样才能变成长期趋势指标。

## 暂缓处理的安全硬化项

这些不在本轮产品化清单中展开，但不能忘：

- [x] PIPL/GDPR 本机数据删除入口（市场化清单 #14）：`POST /api/system/privacy/erase-local-data`（显式确认词 fail-closed），删除任务/对话/运行/录屏/审批/配对/记忆/索引/LLM 用量/感知数据与已导出诊断包，DB 执行 VACUUM；默认保留 settings 与防篡改审计链并追加 `privacy.local_data_erased` 审计事件。证据：`backend/tests/test_privacy_erase.py`（2026-06-10 本地 3 passed，覆盖确认词拒绝、内容删除+审计链保留+响应无路径/正文泄漏、include_settings 路径）。合规自查清单见 `docs/compliance/pipl-gdpr-checklist.md`。剩余缺口：桌面 UI 删除按钮、日志目录自动清理、完整用户数据导出/导入、隐私政策法务定稿与安装时同意——这些未完成前不得对外宣称 PIPL/GDPR 合规。

- [x] 移动端 LAN 明文 token 与 `ws://` 传输已默认阻断：非 loopback HTTP LAN 即使传入 `allowInsecureLan` 也不能配对、恢复旧 session、调用 token-bearing mobile API，或构造 approvals/remote screen/remote input WebSocket；本地 loopback HTTP 仅保留给开发/行为 smoke。证据：2026-06-08 `npm --prefix mobile run smoke:token` 覆盖旧持久化 session 清理、stale metadata 防绕过和 API/WS 拒绝。剩余风险是系统级证书信任链和真机 HTTPS/WSS 信任路径仍需人工证据。
- [x] 桌面 preload 通用 API 代理扩大 renderer XSS 影响面已收窄：`api.request` 会递归 clone/sanitize plain JSON data，拒绝 function、symbol、accessor、非枚举字段、危险键名、class instance、File/Blob/ArrayBuffer、稀疏数组和数组额外字段。证据：2026-06-08 `npm --prefix desktop run smoke:preload-api` 通过；主进程仍保留 endpoint/method/query/body 二次校验。
- [x] backend URL 任意 origin 携带桌面 token 已阻断：desktop token-bearing HTTP proxy、desktop realtime WebSocket、notification WebSocket、BrowserHost bridge 和 runtime foreground/background POST 均要求 loopback backend base URL；renderer web/dev fallback 也限制为 loopback。证据：2026-06-08 `npm --prefix desktop run smoke:ipc`、`npm --prefix desktop run smoke:desktop-ws`、`npm --prefix desktop run smoke:preload-api` 和完整 `npm --prefix desktop run smoke` 通过。剩余边界：普通不带 token 的健康探针和用户配置仍可显示非 loopback 失败状态，但不得携带 desktop token。
- [x] Developer Engine 默认只读边界：`Edit`、`Write`、`Agent` 和不安全 Bash allowlist 会在启动前拒绝；写意图自动路由到 OS engine，不再让 Developer Engine 暗中获得写能力。
  - 剩余边界：若未来要恢复“可写 Developer Engine”，必须先实现真实审批绑定、拒绝 forged `approved/approval_id`、审计脱敏和子 Agent 工具继承测试；当前产品口径是不启用这条可写路径。
- [x] Electron/electron-builder/tar/tmp 安全升级：desktop 已升级到 `electron@42.3.3`、`electron-builder@26.15.2`；`npm --prefix desktop audit --audit-level=high` 通过并报告 `found 0 vulnerabilities`。
- [x] BrowserHost 远程 action 桌面侧二次 grant/approval 校验。
  - 证据：`desktop/src/main/browserHost.ts` 对 renderer/BrowserHost WS 的 takeover、click、fill、submit 等写入动作在无桌面可验证 approval grant 时拒绝，且 `desktop/scripts/ipc-security-smoke.cjs` 覆盖 forged `approved/approval_id` 不可绕过、observe/screenshot 只读动作仍可用。
- [x] 审计链 HMAC secret 存储强度与宣传口径对齐：未配置 `LENGRVIS_AUDIT_HMAC_SECRET` 时会在本地数据目录生成并复用 `audit_hmac.secret`，写入失败时抛出 `RuntimeError`，不再静默回落到空 key。证据：2026-06-08 `python -m pytest backend/tests/test_audit_chain.py -q` 结果 `8 passed`。剩余边界：这是本地审计链防篡改证据，不是外部不可抵赖、硬件-backed key storage 或集中审计系统。

## 90 天 Beta 路线

| 时间窗 | 目标 | 必须交付 | 退出标准 |
| --- | --- | --- | --- |
| D0-D30 | 把“能演示”变成“能安装后完成第一件事” | `release:check` 统一门禁、打包可运行检查、5 个任务向导、Task Workspace 初版、demo 数据包 | 新机器 10 分钟内完成第一个本地任务；5 个模板均可 1-2 步启动 |
| D31-D60 | 把“本机可控”做成可信体验 | Trust Manifest、时间线回放、审批 preview、隐私模式向导、手机配对/审批/暂停/取消 | 每个修改本机状态的任务都有 preview、审批记录、审计事件；隐私失败不静默回云端 |
| D61-D90 | 把“技术底盘”包装成 Beta 产品 | local model smoke、3 个 Skill sample、文档引用、成果区导出、平台证据模板 | local model、mobile companion、Skill sample、document citation、template demo path 均有候选版本证据 |

## 建议执行顺序

1. 先修 P0 gate 与 packaging runnable check，让发布流程真的拦得住坏包。
2. 再做首屏任务模板、QR 配对、远控状态栏，把演示闭环打顺。
3. 然后清理命名、文档、竞品叙事，避免“像仿品”的第一印象。
4. 最后补诊断包、指标、更新/版本展示，把产品从 demo 推向 beta。
