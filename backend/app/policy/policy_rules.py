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
