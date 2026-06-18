# Round 4 安全审查报告 — Lengrvis/mavris

**审查对象:** 当前磁盘工作树(含未提交 R3 修复),非 git HEAD
**方法:** 逐项核实 R3 遗留风险 + 全栈新发现扫描(SSRF / 认证 / 配对 / 脱敏 / Electron / mobile)
**基线:** `.cursor/audit-r3-lens-security.md`(R3 得分 84)

---

## 一、R3 遗留项逐项核实

| R3 ID | 项目 | R4 状态 |
|-------|------|---------|
| R3-001/002/003 | Webhook/MCP/Cloud LLM SSRF 基础校验 | **FIXED** |
| R3-004/005/006 | Audit/Approvals/Diagnostics 脱敏 | **FIXED** |
| R3-007/018 | Desktop token DPAPI 存储 | **FIXED** |
| R3-011 | 配对码熵 + per-IP 限速 | **FIXED** |
| R3-012 | SSRF DNS TOCTOU | **OPEN(遗留)** |
| R3-013 | 全局配对 confirm 桶 LAN grief | **OPEN(遗留)** |
| P0-18 | 策略双轨 | **OPEN(遗留,降为 Medium)** |

---

## 二、按严重度的全部发现

### Critical
无 OPEN Critical。

### High
无 OPEN High。

### Medium

**R4-M1 — SSRF DNS TOCTOU:校验后连接时无 IP pin(R3遗留-OPEN)**
- 证据:`backend/app/core/outbound_url.py:57-72` 校验阶段仅 `socket.getaddrinfo` 解析一次;消费者连接时按 hostname 重新解析且不 pin:`backend/app/mcp/client.py:103-105`、`backend/app/llm/openai_compatible.py:163-168`(`_shared_http_client`)、`backend/app/adapters/webhook.py:39` 经注入 client。三处均 `follow_redirects=False`(好),但无 connect-time IP pin 或重解析重检。
- 攻击场景:攻击者控制 URL(webhook payload / MCP 配置 / cloud `base_url`)并在校验与连接窗口间令 DNS 改解析到 `169.254.169.254` 或内网,出站请求即可达内网/metadata。
- 状态:新发现确认为 R3-012 遗留,仍 OPEN。

**R4-M2 — 全局配对 confirm 失败桶可被任意 LAN 客户端灌满(R3遗留-OPEN)**
- 证据:`backend/app/services/mobile_pairing_service.py:1068-1074` `_record_pairing_failure` 对每次失败同时累加 per-IP 与全局 `__global__` 桶;`:1054-1065` 全局桶达 32/60s 即对**所有人**抛 429;成功仅 `_clear_pairing_failures(rate_key)`(`:109`)清 per-host,**从不清全局**。
- 关于 malformed:`routes_pair.py:15` 的 pydantic `pattern=r"^[a-f0-9]{8}$"` 会在到达服务前拦掉格式错误码(422),故 `:98-103` 的长度错误路径基本不可达;但攻击者用 **格式正确但错误** 的码走 401 路径(`:107`)仍计入全局桶。
- 攻击场景:任意 LAN HTTPS 客户端发 32 次格式合法的错误 confirm,即可阻塞全员移动配对约 60s,可循环 grief。
- 状态:R3-013 遗留,仍 OPEN。

**R4-M3 — 权限判定双轨(R3遗留 P0-18,安全视角降为 Medium)**
- 证据:`tool_runtime._check_permission`(`backend/app/orchestration/tool_runtime.py:875-886`)只评估工具自带 `permission_policy` callable + 路径授权(`_ensure_authorized_paths`),**不**调用用户配置的 `PermissionStore`;用户 deny 规则/时间窗口仅经 `orchestrator.safety.review_tool_call` → `policy_engine.py:974-985` `_review_permission_policy` → `permission_store.evaluate` 评估。两套判定面并存。
- 攻击场景:并非直接旁路(两条路径在 tool_runtime 内串行执行),但工具级与策略引擎级判定逻辑分裂,未来修改时易出现一侧放行/一侧拦截的不一致。
- 状态:遗留,功能上无 bypass,列为一致性/可维护性 Medium。

### Low

**R4-L1 — GET `/system/diagnostics` 仍返回完整本地路径**
- 证据:`backend/app/api/routes_system.py:284-324` 有意保留 `local_paths`(data_dir、db 路径、log_dirs)未脱敏(export 路径已脱敏)。
- 评估:仅在 desktop token(loopback 或 token)后可达,非 LAN 暴露;属可接受的本地 UI 设计。Low。

**R4-L2 — `198.18.0.0/15` benchmark 网段经 DNS 放行(R3遗留-OPEN)**
- 证据:`outbound_url.py:68-69` 对该网段例外。小众 SSRF 边缘,Low。

**R4-L3 — MCP 一律 `allow_private=False`**
- 证据:`mcp/client.py:96`。可能促使用户走公网 tunnel 自暴露本地 MCP(运营项),Low。

---

## 三、新发现扫描结论(无新增 High+)

- **LAN guard / 中间件顺序(`main.py:133-176`):** Starlette 反序执行,`desktop_api_token_guard` 最外层、`lan_api_guard` 最内层,整体 fail-closed。`is_loopback_host`(`lan.py:11-25`)对未知 host 失败关闭。未发现旁路。✅
- **WebSocket 认证:** `routes_chat.py:48` 每个 WS 经 `close_unauthorized_desktop_websocket`(loopback 或 token,`desktop_api.py:66-98`)。WS 不经 HTTP 中间件,认证在 handler 内完成,正确。✅
- **Desktop token 存储/传输:** `localSecret.ts` DPAPI 经 base64(无注入)+ 原子写 `wx 0o600`;`local_secret.py` 用 win32crypt DPAPI + `O_EXCL` 临时文件 + `os.replace`;token 经 env 传子进程,`backendProcess.ts:20-22,570` 对日志中 token/secret 模式做脱敏;经 header 仅发往 loopback(`assertLoopbackBackendUrl`)。✅ FIXED
- **命令注入/路径穿越:** `tool_runtime` 路径参数经 `resolve_authorized` + 写锁;DPAPI PowerShell 仅嵌 base64,无注入面。✅
- **Mobile 配对/token 存储(`mobile/src/store/auth.ts`):** token 仅入 `SecureStore`(Keychain/Keystore),元数据入 AsyncStorage,legacy 内嵌 token 迁移后擦除,`isInsecureLan` 基址 fail-closed 拒绝。✅
- **Electron 安全:** `main.ts:56-58` / `browserHost.ts:817-819` `contextIsolation:true`、`nodeIntegration:false`、`sandbox:true`;`index.html:6-7` CSP `default-src 'self'; script-src 'self'`(无脚本 unsafe-inline);`autoUpdater.ts:67-69` 打包构建默认 `verifyUpdateCodeSignature`、`autoDownload=false`。✅

---

## 四、安全透镜评分:**83 / 100**

| 维度 | 权重 | 分 | 说明 |
|------|------|----|------|
| 出站 SSRF | 30% | 23/30 | 统一校验+禁重定向;TOCTOU 未 pin IP(R4-M1) |
| 认证/秘密 | 25% | 23/25 | DPAPI token、mobile SecureStore、配对熵、中间件 fail-closed |
| 数据暴露/脱敏 | 20% | 18/20 | audit/approvals/diagnostics 脱敏完善;GET diagnostics 留路径(Low) |
| 执行控制面/策略 | 15% | 11/15 | 路径授权+写锁稳;权限双轨待统一(R4-M3) |
| 可用性/滥用 | 10% | 8/10 | 全局配对桶可被 LAN grief(R4-M2) |

**评分理由:** 严格规则下任何 OPEN High 封顶 70、OPEN Critical 封顶 50。本轮**无 OPEN High/Critical** —— R3 全部 High(MCP/LLM/Webhook SSRF 基础校验、Desktop token 明文、audit/诊断泄露)经代码核实确为 FIXED。封顶规则不触发。剩余三项均为 **Medium**(SSRF DNS TOCTOU、全局配对 DoS、策略双轨)加少量 Low,较 R3 的 84 微降 1 分,主要因本轮以独立透镜复核确认 R3-012/R3-013 仍未真正修复(仅基础校验到位,connect-time pin 与全局桶拆分均未落地)。

**优先修复建议:** ① R4-M1:自定义 httpx transport 在 connect 时 pin 已校验 IP,或每请求重解析重检;② R4-M2:全局桶改 per-subnet 预算 + 指数退避,confirm 失败成功都按窗口衰减,malformed 不计全局;③ R4-M3:approval 单轨化,tool_runtime 统一经 PolicyEngine 评估用户策略。