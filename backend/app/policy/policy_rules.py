from __future__ import annotations

FORBIDDEN_TERMS = {
    "password",
    "密码",
    "口令",
    "cookie",
    "token",
    "credential",
    "credentials",
    "private key",
    "密钥",
    "pay",
    "payment",
    "支付",
    "付款",
    "order",
    "下单",
    "bypass",
    "disable security",
}

# Goal-level narrated security boundaries. Each rule is a conjunction: every
# signal must be present before the goal is denied. This keeps boundary notices
# deterministic without turning words such as "memory" or "upload" into broad
# deny-list entries.
GOAL_UNTRUSTED_AUTHORIZATION_MEMORY_SIGNALS = (
    r"(?:网页|\bweb(?:page|site)?\b|(?:另一个|其他|跨)\s*(?:agent|智能体))",
    r"(?:声称|消息说|\b(?:claims?|says?)\b)",
    r"(?:永久|始终|\b(?:permanent(?:ly)?|always)\b).{0,40}(?:允许|批准|\b(?:allow(?:ed|s|ing)?|approv(?:e|ed|es|al|ing))\b)",
    r"(?:上传|发送|\b(?:uploads?|uploading|sends?|sending)\b)",
    r"(?:不要|不得|禁止|\b(?:do\s+not|don't|must\s+not)\b).{0,80}(?:写入|保存|晋升|\b(?:store|save|promote)\b).{0,50}(?:记忆|\bmemory\b)",
)
GOAL_MISSING_TRUSTED_BIOMETRIC_STEP_UP_SIGNALS = (
    r"(?:没有|无(?!需)|缺少|\bwithout\b|\bno\b).{0,30}(?:可信\s*生物识别|trusted\s+biometric).{0,30}(?:step[-\s]?up|二次验证)",
    r"(?:拒绝|禁止|\b(?:deny|reject|refuse)\b)",
    r"(?:高影响|high[-\s]?impact)",
    r"(?:发送|表单.{0,12}提交|\b(?:send(?:ing)?|form.{0,20}submit|approv(?:e|ed|es|al|ing))\b)",
)
GOAL_UNSANDBOXED_GENERATED_CODE_SIGNALS = (
    r"(?:没有|无(?!需)|缺少|\bwithout\b|\bno\b).{0,30}(?:os\s*(?:沙箱|sandbox)|appcontainer|受限令牌|restricted\s+token|job\s+object\s+broker|broker)",
    r"(?:不要|不得|禁止|\b(?:do\s+not|don't|must\s+not)\b).{0,30}(?:执行|运行|\b(?:execute|run)\b)",
    r"(?:生成(?:的)?.{0,80}代码|\bgenerated\s+code\b)",
)
GOAL_REVOKED_MOBILE_DEVICE_ACCESS_SIGNALS = (
    r"(?:(?:手机|移动)\s*设备.{0,16}(?:已经|已经被|已被|已)\s*(?:撤销|吊销)|\brevoked\s+mobile\s+device\b)",
    r"(?:(?:旧|原有|previous|old).{0,12}(?:token|令牌))",
    r"(?:不得|不能|拒绝|禁止|\b(?:do\s+not|don't|must\s+not|deny|reject)\b).{0,40}(?:继续)?.{0,12}(?:访问|使用|\baccess\b).{0,16}(?:任务|审批|\b(?:task|approval)\b)",
)

# Words that signal a safety-system boundary notice (a denial being explained,
# an approval being requested, a read-only alternative being offered).
BOUNDARY_TERMS = (
    "approval",
    "approve",
    "blocked",
    "deny",
    "denied",
    "forbidden",
    "handoff",
    "never",
    "read-only",
    "restricted",
    "safe alternative",
    "supervision",
)

# A forbidden term is only exempt when a boundary term occurs within this many
# characters of that occurrence (see PolicyEngine._unprotected_forbidden_hits).
BOUNDARY_CONTEXT_WINDOW = 120


SENSITIVE_FIELD_NAMES = {
    "password",
    "pwd",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "credentials",
    "cvv",
    "cvc",
    "card_number",
    "cardnumber",
    "otp",
    "2fa",
    "passcode",
    "payment",
    "pay",
    "order",
    "ssn",
    "口令",
    "密码",
}


BROWSER_WRITE_TOOLS = {
    "browser.act",
    "browser.click_element",
    "browser.cua",
    "browser.cua_run",
    "browser.fill_form",
    "browser.submit_form",
}
UI_AUTOMATION_WRITE_TOOLS = {
    "ui_automation.click",
    "ui_automation.type_text",
    "ui_automation.click_at",
    "ui_automation.drag",
    "ui_automation.key_press",
    "ui_automation.hotkey",
}
BROWSER_ACTIVITY_READ_KINDS = {
    "open",
    "navigate",
    "wait",
    "screenshot",
    "observe",
}
BROWSER_ACTIVITY_MUTATING_KINDS = {
    "click",
    "fill",
    "submit",
    "scroll",
    "cua",
}
BROWSER_ACTIVITY_TOOL_KIND_MAP = {
    "browser.act": "observe",
    "browser.open_url": "open",
    "browser.navigate": "navigate",
    "browser.read_page": "observe",
    "browser.summarize_page": "observe",
    "browser.extract_links": "observe",
    "browser.search_web_via_provider": "observe",
    "browser.screenshot": "screenshot",
    "browser.wait_for_selector": "wait",
    "browser.click_element": "click",
    "browser.fill_form": "fill",
    "browser.submit_form": "submit",
    "browser.cua": "cua",
    "browser.cua_run": "cua",
}
BROWSER_ACTIVITY_HANDOFF_TERMS = {
    "2fa",
    "authenticator",
    "checkout",
    "cookie",
    "credential",
    "credentials",
    "cvv",
    "delete",
    "download",
    "login",
    "message",
    "order",
    "otp",
    "passcode",
    "password",
    "payment",
    "purchase",
    "send",
    "token",
    "upload",
    "密码",
    "支付",
    "下单",
}
BROWSER_PROMPT_INJECTION_PATTERNS = {
    r"ignore\s+(all\s+)?(previous|prior|system|developer)\s+instructions",
    r"disregard\s+(all\s+)?(previous|prior|system|developer)\s+instructions",
    r"reveal\s+(the\s+)?(system|developer)\s+prompt",
    r"send\s+(your\s+)?(cookies|tokens|credentials|api\s*keys)",
    r"disable\s+(safety|security|policy)",
    r"you\s+are\s+now\s+(in\s+)?developer\s+mode",
}
BROWSER_CONTENT_TRUST = "untrusted_browser_content"
BROWSER_CONTENT_PROMPT_INJECTION_WARNING = "prompt_injection_like_browser_content"
CLEANUP_READ_TOOLS = {"file.cleanup_scan", "file.cleanup_plan", "file.dedupe_plan"}
CLEANUP_WRITE_TOOLS = {"file.cleanup_execute", "file.cleanup_rollback"}

FAST_PATH_ALLOWED_EFFECTS = {"read", "observe", "list", "open", "launch", "reveal", "navigate", "search", "inspect"}
FAST_PATH_FORBIDDEN_EFFECTS = {
    "write",
    "delete",
    "move",
    "send",
    "submit",
    "type",
    "shell",
    "credential",
    "payment",
    "external_post",
    "browser_write",
}
FAST_PATH_TRUST_TIERS = {"builtin", "core", "first_party"}
FAST_PATH_BLOCKED_TRUST_TIERS = {"unknown", "advisory", "third_party", "untrusted"}
PATH_ARG_KEYS = {
    "path",
    "paths",
    "source",
    "sources",
    "destination",
    "destinations",
    "target",
    "target_path",
    "target_folder",
    "folder",
    "directory",
    "output_path",
    "file",
    "files",
}
SYSTEM_PATH_PREFIXES = (
    "c:/windows",
    "c:/program files",
    "c:/program files (x86)",
    "c:/programdata",
    "/windows",
    "/program files",
    "/programdata",
    "/etc",
    "/bin",
    "/sbin",
    "/usr",
    "/var",
    "/system",
    "/library",
)

# MCP tool prefix matching should be case-insensitive.
MCP_PREFIXES = ("mcp.", "mcp_", "mcp-", "mcp:")
