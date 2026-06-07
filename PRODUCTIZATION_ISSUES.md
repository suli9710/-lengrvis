# Lengrvis 产品化问题清单

> 来源：2026-06-07 多 agent 代码审计与竞品对比。安全硬化项暂缓处理，本清单优先收敛“能否像一个成熟产品交付、安装、演示、留存、跨端使用”的问题。

## 目标

- 把 Lengrvis 从“能跑的工程原型”推进到“可交付、可演示、可回归、可解释失败原因”的产品状态。
- 优先解决影响首日体验、发布可信度、跨端闭环、竞品叙事的缺口。
- 安全问题不在本轮展开，但涉及发布阻断的安全门禁缺口会保留为产品化门禁项。

## P0 发布门禁与验收

- [x] **补齐 P0 gate：mobile remote-input grant smoke 必须进入统一门禁。**
  - 证据：`docs/qa/e2e-acceptance-matrix.md` 把 `npm --prefix mobile run smoke:remote-input-grant` 列为 E2E-006 P0；`scripts/run_tests.ps1` 和 `docs/qa/release-gate.md` 已纳入 mobile typecheck、`smoke:token` 与 `smoke:remote-input-grant`。
  - 影响：发布流程会漏掉“远控输入授权边界”这类核心跨端能力，门禁名义上严肃，实际像纸糊的。
  - 验收：`.\scripts\run_tests.ps1`、根目录 `qa:gate`、`docs/qa/release-gate.md` 的 expanded commands 三处一致包含 mobile remote-input grant smoke。

- [ ] **把 regex smoke 升级为行为级 smoke。**
  - 证据：`mobile/scripts/mobile-token-smoke.cjs` 与 `mobile/scripts/remote-input-grant-smoke.cjs` 主要靠源码/纯函数检查，不能证明真实 HTTP/WS 流程可用。
  - 影响：CI 能绿，但演示现场才发现 token、grant、device 绑定不通。
  - 验收：增加最小后端测试桩或本地测试服务，覆盖配对 token、claim grant、WS protocol、grant revoke/expire 的真实调用路径。

- [x] **建立“一条命令发布前验证”而不是散落脚本。**
  - 证据：曾经 `scripts/run_tests.ps1`、`scripts/build_all.ps1`、`scripts/verify_packaging.ps1`、docs release gate 存在口径差；现已由根目录 `release:check` 收敛，`release:gate` 仅保留兼容别名。
  - 影响：开发者不知道哪个才是准发布标准，产品质量靠记忆力。
  - 验收：根目录提供 `release:check`，`release:gate` 作为兼容别名，发布门禁文档引用 `release:check`。

- [x] **把平台卖点证据纳入 release/demo gate。**
  - 证据：`docs/qa/release-gate.md`、`docs/qa/e2e-acceptance-matrix.md` 和 `docs/demo-script.md` 已要求记录 local model readiness/smoke、mobile companion flow、Skill Product Manifest sample、document citation 和 template demo path。
  - 影响：避免演示和 release notes 只讲“本地模型、Skill、文档库、移动伴侣、模板路径”这些卖点，却没有同候选版本绑定的证据。
  - 验收：自动门禁继续使用既有 `qa:gate` / `release:check`；未自动化的平台卖点必须进入人工 gate、waiver 或 residual risk。

- [x] **补齐依赖锁验证入口与验收口径。**
  - 证据：根目录 `deps:verify` 入口验证 `desktop/package-lock.json`、`mobile/package-lock.json` 与后端 direct lock；`docs/qa/release-gate.md` 已把 `npm run deps:verify` 列为依赖变更时必须记录的 preflight evidence。
  - 剩余风险：`backend/requirements-lock.txt` 只锁直接依赖，不是完整解析后的 Python 传递依赖锁；后续仍应迁移到 uv/pip-tools 等完整 lock workflow。
  - 验收：依赖清单、lockfile 或后端 requirements 变更时，QA handoff 必须记录 `npm run deps:verify` 的命令、日期、提交和结果。

- [x] **补齐 LAN TLS readiness 的证据口径。**
  - 证据：`docs/qa/release-gate.md` 和 `docs/qa/e2e-acceptance-matrix.md` 已要求移动/LAN 演示记录 `http/ws` 或 `https/wss` scheme、证书来源、设备侧显式信任路径。
  - 剩余风险：这只是 readiness/configuration/manual evidence，不代表系统级证书信任链已经完成；HTTP LAN 仍只能算 dev/test-only。
  - 验收：任何 demo/release 文案提到 LAN TLS、HTTPS/WSS 或证书信任时，必须附候选版本手工证据；否则只能记为 residual risk。

- [x] **补齐 Skill 样本迁移的自动/人工验收口径。**
  - 证据：`docs/qa/e2e-acceptance-matrix.md` 已把 E2E-019 写成可复制的 pytest 命令，并要求记录迁移样本 id/source、import path 和 Product Manifest 卡片证据。
  - 剩余风险：这证明样本迁移和 manifest 风险表达有验收口径，不代表 Skill 生态已有足够多真实生产样本。
  - 验收：每个 release candidate 至少导入或展示一个非私有迁移样本；样本不得回退到 `legacy.unspecified` 权限表达。

## P0 安装、打包、分发

- [ ] **部分完成：打包验证从“文件存在”升级为“可运行”。**
  - 证据：`release:smoke` 和 `scripts\build_all.ps1 -VerifyOnly -RunExecutableSmoke` 已提供 release runnable smoke；`docs/qa/release-gate.md` 与 E2E-012 已要求记录 structural verification、runnable smoke 和 `.tmp\packaging-smoke` 失败诊断。
  - 剩余缺口：当前自动化主要证明后端可执行文件能短命令退出或在隔离 loopback 上响应 `/health`；portable GUI 启动到首屏仍保留为人工 P1 sign-off。
  - 验收：Windows release candidate 必须跑 `npm run release:smoke` 或等价命令；portable 首屏和读只诊断任务必须另行记录人工证据。

- [x] **明确发布产物矩阵：Windows、macOS、Android 分别到什么完成度。**
  - 证据：`desktop/package.json` 有 mac dist 脚本，根目录脚本和启动文档偏 Windows/PowerShell；移动端是 Expo app，但分发状态不清。
  - 影响：对外承诺跨平台，实际交付像 Windows-only 内测包。
  - 验收：README 顶部已给出平台支持表，标注 `Supported / Preview / Planned`，并写明当前交付与已知限制。

- [ ] **部分完成：停止在正式启动脚本里现场安装依赖。**
  - 证据：`scripts/start_app.ps1` 带有 npm/pip 安装路径，后端依赖存在 `>=` 风格漂移风险。
  - 已补闭环：依赖锁验证已有 `deps:verify` 入口和 direct backend lock 证据，可在发布前阻断明显 drift。
  - 影响：用户第一次启动时网络、registry、依赖新版本都可能把体验炸掉。
  - 验收：正式启动脚本只启动已锁定产物；开发环境安装迁到 `setup` 脚本；依赖使用 lock/constraints 固定。

- [ ] **补齐桌面自动更新/版本展示/故障日志入口。**
  - 证据：当前更像开发者启动器，用户侧缺少“当前版本、检查更新、导出诊断包”的明确闭环。
  - 影响：真实用户遇到问题只能截图喊救命，不像产品。
  - 验收：设置页展示版本、构建时间、后端状态、日志目录、导出诊断包按钮。

## P1 首次体验与核心闭环

- [x] **缩短 time-to-first-win：首屏必须引导用户完成一个真实任务。**
  - 证据：产品能力很多，但 README/桌面 UI 更偏能力陈列；竞品 Marvis/Copilot/ChatGPT Agent 都强调“马上替你做事”。
  - 影响：用户打开后不知道第一件事该让它做什么。
  - 验收：首屏已提供 5 个任务模板：整理下载目录、总结本地文档、查找大文件、检查电脑状态、文档问答；每个模板展示本机处理、云端边界、审批、回滚、预计耗时。

- [ ] **把本地模型能力做成可完成路径，而不是配置谜题。**
  - 证据：设置页已经把快速、隐私、智能混合三档做成用户可理解的 Model Boundary Profile，并展示推荐模型、大小、硬件、速度预估和修复动作。
  - 剩余缺口：真实“一键安装/启动推荐模型”、local model smoke、失败修复按钮仍需接入后端能力；隐私模式失败不得静默回退云端。
  - 影响：本地隐私是核心卖点，若只能看懂不能完成，仍然会被 Marvis 的本地模式包装按着打。
  - 验收：断网时隐私模式至少完成文件搜索、简单摘要、系统查询中的两类；安装失败展示修复路径，不允许自动降级成云端处理。

- [ ] **移动端配对流程产品化。**
  - 证据：移动端目前要求手填电脑地址和 6 位配对码，placeholder 是裸 IP。
  - 影响：演示可以，普通用户会被 IP、端口、同网段这些词劝退。
  - 验收：桌面端展示 QR code；移动端扫码配对；失败页区分“不在同一网络 / 后端未启动 / code 过期 / 权限不足”。

- [x] **远程桌面/远控做成明确的模式切换。**
  - 证据：移动端已有 RemoteScreen 和 remote input grant，但产品语义仍散在审批事件里。
  - 影响：用户不知道现在是只读、可接管、已过期，容易恐慌或误操作。
  - 验收：移动端远程屏幕固定显示 `只读观看 / 已授权输入 / 授权剩余时间 / 结束接管` 状态与按钮；远程输入仍需短期授权。

- [x] **通知内容默认隐私保护。**
  - 证据：`mobile/src/notifications.ts` 已不再把 approval message 放到高优先级通知正文。
  - 影响：即便安全问题稍后处理，这也是产品信任问题；锁屏泄露任务内容会让用户立刻卸载。
  - 验收：默认通知只显示“有任务等待审批”，详情进入 App 后展示；设置中可选择是否显示敏感摘要。

## P1 文档、品牌、竞品叙事

- [ ] **清理 Mavris/Marvis/Lengrvis 命名残留。**
  - 证据：工作树中存在大量 Mavris/Marvis 到 Lengrvis 的重命名痕迹，仍有旧文件删除/新增并存。
  - 影响：竞品叫 Marvis，自己还残留 Marvis/Mavris，会显得像贴牌仿品。
  - 验收：代码、文档、启动脚本、截图、产物名、vendor manifest 全量 grep 无非兼容必要的旧名。

- [ ] **更新过时的 parity 文档。**
  - 证据：`docs/LENGRVIS_PARITY.md` 部分描述仍称 file watcher、手机远控为占位，但代码已实现相关能力。
  - 影响：内部路线图不可信，外部读者会低估或误解产品。
  - 验收：文档按当前实现重写为 `已实现 / 可演示 / 需要硬化 / 未开始` 四栏。

- [ ] **重写竞品对比：别喊“杀手”，讲清差异化。**
  - 竞品事实：腾讯 Marvis 已有 Win/macOS/Android、本地模式、手机接管电脑；微软有 OS 原生入口和 agent workspace；OpenAI/Anthropic 强在模型与工具生态。
  - 当前定位：Lengrvis 应打“本机 OS agent + 可审计 + 可扩展 + 自托管”，不要硬碰平台分发和大模型品牌。
  - 验收：README/官网文案避免“全面领先”“替代 Marvis”等空话，改成具体场景对比和限制说明。

### 竞品差距 Checklist

| 对标产品 | 对方强项 | Lengrvis 当前差距 | 90 天动作 | 验收证据 |
| --- | --- | --- | --- | --- |
| 腾讯 Marvis | Win/macOS/Android 分发、本地模式、手机接管、AI 图库/文档库 | 跨端分发弱，本地模型不是开箱即用，图库/文档库消费体验不够顺 | Windows + Android demo path；隐私模式一键安装；手机审批/只读查看任务 | demo script 录屏、local model smoke、mobile companion flow |
| Microsoft Copilot+ PC | OS 原生入口、agent workspace、硬件/安全叙事 | 没有用户一眼看懂的任务隔离空间，Windows 入口不够产品化 | Task Workspace、Manifest、时间线回放、文件右键/通知轻入口 | workspace 截图、审计事件、Explorer 入口 smoke |
| ChatGPT Agent / Operator | 云端虚拟电脑、connectors、可暂停/接管、成果产出 | 任务运行中协作弱，结果区不像交付物，connector 生态弱 | 成果区、下一步按钮、Skill sample、浏览器/文档 demo | template demo path、Skill sample、document citation |
| Claude Code / Computer Use | 开发者工作流、移动路由、权限/差异预览 | 手机不能完整续写任务，审批缺规则记忆/替代建议 | 手机发起/续写、审批 preview、follow-up、暂停/取消 | mobile task create/follow-up tests、approval replay |
| Manus / Genspark | 模板工作台、Slides/Sheets/Docs 成果包装 | 首页模板仍需向导化，产出库和导出成果不足 | 5 个任务向导、清理计划/摘要/表格成果区、导出路径说明 | browser smoke、demo-script、成果区截图 |

- [x] **补齐截图、录屏、演示脚本。**
  - 证据：桌面有 smoke screenshot 资源，但还没有稳定的一分钟产品演示路径。
  - 影响：没有 demo，产品价值只能靠讲，讲得越多越像没做完。
  - 验收：`docs/demo-script.md` 已包含 60 秒、3 分钟、10 分钟三档演示脚本；每档有准备数据、失败兜底、预期画面。

## P2 工程交付卫生

- [ ] **源代码 map 策略产品化。**
  - 证据：`desktop/tsconfig.node.json` 开启 source map，打包配置可能包含 dist map。
  - 影响：调试方便，但发布包体积、内部实现暴露、崩溃定位策略混在一起。
  - 验收：开发包保留 map；公开发布包默认去除或单独上传符号文件。

- [ ] **进程管理从模糊 kill 变成受控生命周期。**
  - 证据：`scripts/start_app.ps1` 存在多处 `Stop-Process`，匹配策略偏宽。
  - 影响：用户机器上误杀同类进程，产品观感会非常差。
  - 验收：只管理本产品启动并记录 PID 的进程；停止前展示目标；日志记录原因。

- [ ] **统一配置入口和错误文案。**
  - 证据：环境变量、config yaml、桌面设置、启动脚本多处可配置同一类内容。
  - 影响：问题排查复杂，用户不知道该改哪里。
  - 验收：设置页为主入口；高级配置保留文件方式；启动失败给出“去哪里改”的精确提示。

- [ ] **建立产品指标而不是只看测试绿灯。**
  - 指标建议：首次启动成功率、配对成功率、首个任务成功率、任务平均完成时长、审批响应率、模型安装成功率。
  - 验收：至少在本地诊断包中输出这些匿名/本地统计，便于 dogfood。

## 暂缓处理的安全硬化项

这些不在本轮产品化清单中展开，但不能忘：

- [ ] 移动端 LAN 明文 token 与 `ws://` 传输；当前仅补齐 TLS readiness/configuration/manual evidence，尚未完成系统级证书信任链。
- [ ] 桌面 preload 通用 API 代理扩大 renderer XSS 影响面。
- [ ] backend URL 任意 origin 携带桌面 token。
- [ ] Developer Engine 的 `Edit/Write` 接入统一审批绑定。
- [ ] Electron/electron-builder/tar/tmp 安全升级。
- [ ] BrowserHost 远程 action 桌面侧二次 grant/approval 校验。
- [ ] 审计链 HMAC secret 存储强度与宣传口径对齐。

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
