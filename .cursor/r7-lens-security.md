# Round 7 安全审计报告 — Security 视角（只读）

审计对象：`mavris` 本地 OS Agent 助手当前工作树中近期修改/新增的安全敏感模块。结论：**未发现 Critical/High 级别漏洞**。R5/R6 的 SSRF、LAN guard、desktop token、mobile pairing、MCP client 修复全部确认未回退，实现稳健。下面是按严重度排序的发现清单。

---

## 发现清单

### 1. 【Medium】Webhook 适配器缺少 IP-pinning，存在 DNS-rebinding TOCTOU
**文件：`backend/app/adapters/webhook.py:39`**

LLM (`openai_compatible.py:166`) 与 MCP (`mcp/client.py:98`) 都使用 `pin_outbound_http_url()` 把连接目标钉死到已校验的 IP（防 DNS-rebinding），而 webhook 只调用了不做钉定的 `validate_outbound_http_url`：

```39:41:backend/app/adapters/webhook.py
url = validate_outbound_http_url(url, allow_private=False)
except ValueError as exc:
    return {"ok": False, "adapter": self.config.service_name, "error": str(exc)}
```

校验通过后把原始 `url`（含主机名）交给注入的 `self.client.post()`，客户端会重新做一次 DNS 解析——攻击者控制的域名可在校验后把解析结果翻转到 `169.254.169.254`/内网，绕过 SSRF 防护。此外 redirect 跟随策略取决于未知的注入 client，可能放大 SSRF。

**缓解现状**：默认无注入 `client`（`adapters/tools.py` 默认 `webhook_client=None`），非 dry-run 时 `connect()` 直接返回错误，因此默认部署下该路径未激活，故定为 Medium 而非 High。

**修复建议**：改用 `pin_outbound_http_url()`（与 LLM/MCP 对齐），并要求 webhook client 强制 `follow_redirects=False`。

---

### 2. 【Low】`198.18.0.0/15` fake-IP 段被显式放行为合法 pin 目标
**文件：`backend/app/core/outbound_url.py:12, 102-105, 139-140`**

```102:105:backend/app/core/outbound_url.py
if isinstance(ip, ipaddress.IPv4Address) and ip in _FAKE_IP_NETWORK:
    # Local tunneling proxy fake-IP: connecting to it is the intended
    # behavior, so it is a safe pin target.
    return str(ip)
```

若攻击者能让某域名解析进 `198.18/15`，校验与 pin 都会放行。该段是 RFC 2544 基准测试保留地址、通常不可路由，实际可利用性很低，且为隧道代理的有意设计。**建议**：将放行限定为 `LENGRVIS_*` 显式开关启用时，而非默认无条件放行。

---

### 3. 【Low / Informational】`is_loopback_host` 在生产中间件里硬编码接受 `"testclient"`
**文件：`backend/app/security/lan.py:21`**

```21:22:backend/app/security/lan.py
if normalized in {"localhost", "testclient"}:
    return True
```

`lan_api_guard`/`desktop_api_token_guard` 用它判断是否本机豁免。远程 TCP 对端的 `request.client.host` 是真实 IP，无法被设置为字符串 `"testclient"`，因此远程不可利用；但测试用值混入生产鉴权路径是异味。**建议**：仅在测试环境（与 `_is_test_environment()` 一致）识别该别名。

---

### 4. 【Low】HTTP skill 仅按字面量判断 loopback，不解析主机名
**文件：`backend/app/skills/sandbox.py:265-268`**

`is_loopback_http_url` 只接受 `localhost`/`127.*`/`::1` 字面量，未解析主机名，配合 `follow_redirects=False`，绕过面很小。但若 hosts 文件被污染使 `localhost` 指向非环回地址则存在理论风险。可接受，记录备查。

---

## 已验证良好（修复未回退、实现稳健）

| 模块 | 验证点 |
|---|---|
| `core/outbound_url.py` | `validate` + `pin` 双层；IPv4/IPv6 经 `ipaddress` 规范化（含 `%zone` 剥离）；metadata 主机阻断；rebind 后**fail-closed**抛错（:111）；IPv6 netloc 加方括号正确 |
| `llm/openai_compatible.py` | 每次 attempt **重新 pin**（:163-166）防 TOCTOU；`follow_redirects=False`（:50）；本地 provider 才允许 private base（:116-119）；`Authorization` 仅在需要时加 |
| `mcp/client.py` | pin 连接（:98）；`follow_redirects=False`（:105）；`_auth_required` 门控；非 http(s) transport 不发起请求 |
| `security/desktop_api.py` | `hmac.compare_digest` 恒时比较（:53,61,126）；token-optional 仅测试环境生效、生产降级并告警（:147-154）；签名资源 HMAC 覆盖 method+path+payload+expires 且 TTL 上限 10 分钟（:123） |
| `security/mobile_jwt.py` | HS256 + `require:[exp,iat,aud,iss]`（:172）防无声跳过；aud/iss 强校验；设备 active 校验；remote-input grant 与 device/grant_id 绑定（:205-233） |
| `security/local_secret.py` | DPAPI 加密 + `O_EXCL\|0o600` 原子写（:54-60）；JWT secret 由随机 32 字节本地密钥生成（`config.py:119-129`），无静态默认 |
| `services/mobile_pairing_service.py` | 配对码 `secrets.token_hex`；`BEGIN IMMEDIATE` + `rowcount==1` 保证**单次兑换**（:161-174）；按 IP + 全局双桶限流（:1054-1065）；设备名/payload 经 `redact_*` 脱敏；pairing 要求 LAN TLS 就绪 |
| `api/routes_pair.py` | pydantic `pattern=^[a-f0-9]{8}$` 严格校验；写操作经 desktop token 门控，仅 `/pair`、`/pair/confirm` 豁免 |
| `policy/permissions.py` | deny 优先于 allow；存在 allow 规则时**默认拒绝**（allow-list）（:243-249）；fnmatch 大小写归一 |
| `core/paths.py` | `..` 阻断、系统/敏感路径阻断、NTFS ADS 阻断、`resolve()` 预解析中间符号链接后再做 `is_relative_to` 容器校验、最终符号链接逃逸校验 |
| `tools/developer_tools.py` | 命令白名单 + `shell=False`；写类 token/元字符拒绝（:339,441）；路径参数授权校验；git config guards 禁外部 diff/hooks；pytest 写文件 flag 拒绝 |
| `tools/system_tools.py` | 全部 R0 只读；`open_settings_uri` 限 `ms-settings:` 前缀；诊断输出经 `redact_value` + URL/路径脱敏 |
| `skills/sandbox.py` | 路径遍历多重校验（绝对路径/`..`/`relative_to`/符号链接 strict 解析）；`shell=False`；env 剥离含 `api/auth/secret/token` 等敏感键；本地 python/shell 执行**默认禁用** |
| `api/routes_runs.py` + `agents/*` | `agent_hint` 经 `normalize_supervisor_agent_hint` 收敛到**固定白名单**（非白名单→空），无注入到元数据/prompt 的路径 |
| `llm/mock_provider.py` | 仅在 `allow_mock_fallback=True`（默认 False）时由 `registry._fallback_or_raise` 返回，生产路径不会被意外启用 |
| `services/ollama_service.py` | 子进程全部列表参数 + `shell=False`；API 硬编码 `127.0.0.1`；模型名白名单 `normalize_install_model`；错误文本经 URL/路径脱敏 |
| `main.py` 中间件链 | LAN guard → mobile JWT guard → desktop token guard 顺序合理；远程 LAN 仅放行 `/api/pair[/confirm]` 与 `/api/mobile/*`，其余默认拒绝；`/api/mobile/*` 强制安全传输 + Bearer |

---

## 安全分：**88 / 100**

一句话总评：**前几轮的 SSRF/认证/路径/命令执行加固在 R7 工作树中全部保持稳健、未见回退，无 Critical/High 风险；唯一需要跟进的是 webhook 适配器未与 LLM/MCP 统一采用 IP-pinning 的 DNS-rebinding TOCTOU 不一致（且默认未激活），属可控的 Medium 项。**