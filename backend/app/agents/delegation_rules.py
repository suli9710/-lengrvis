"""Single source of truth for conversation → worker-agent keyword routing.

R7-A4 / 逻辑#4: the "which keywords route to which agent" rules used to live in
four divergent copies (SupervisorAgent heuristic, infer_supervisor_agent_hint,
MockProvider supervisor decision, task_service overrides). All four now consume
the vocabulary below; adding a capability or a trigger word is a one-place edit.

Matching styles:
- Supervisor heuristic / MockProvider keep plain substring matching (legacy
  behaviour, exercised by golden tasks).
- ``contains_any`` adds word-boundary matching for pure-ASCII terms so that
  English verbs like "move" do not fire inside "movie" / "remove" inside
  "removed" is still fine.
"""

from __future__ import annotations

import re

# Shared with SupervisorAgent and task_service explicit-path overrides.
WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\r\n\"<>|?*]+")

_ASCII_WORD_RE = re.compile(r"^[a-z][a-z ]*$")


def contains_term(text_lower: str, term: str) -> bool:
    """Substring match, upgraded to word-boundary match for ASCII words."""
    if _ASCII_WORD_RE.fullmatch(term):
        return re.search(rf"\b{re.escape(term)}\b", text_lower) is not None
    return term in text_lower


def contains_any(text_lower: str, terms: tuple[str, ...]) -> bool:
    return any(contains_term(text_lower, term) for term in terms)


# --- Base vocabulary (Chinese + English) -----------------------------------

COMPUTER_DOMAIN_TERMS: tuple[str, ...] = (
    "电脑",
    "配置",
    "系统",
    "cpu",
    "memory",
    "内存",
    "磁盘",
    "进程",
    "启动项",
    "设置",
)
COMPUTER_ACTION_TERMS: tuple[str, ...] = ("查", "看", "读取", "获取", "诊断", "检测", "列出")

FILE_DOMAIN_TERMS: tuple[str, ...] = (
    "文件",
    "文档",
    "目录",
    "文件夹",
    "重复",
    "发票",
    "合同",
    "素材",
    ".txt",
    ".pdf",
    ".docx",
    "file",
    "folder",
    "directory",
    "duplicate",
)
FILE_ACTION_TERMS: tuple[str, ...] = (
    "查",
    "找",
    "搜索",
    "整理",
    "复制",
    "移动",
    "重命名",
    "编辑",
    "修改",
    "替换",
    "写入",
    "新建",
    "创建",
    "删除",
    "删掉",
    "移除",
    "清理",
    "读取",
    "列出",
    "打开",
    "delete",
    "remove",
    "trash",
    "copy",
    "move",
    "rename",
    "open",
    "edit",
    "organize",
    "search",
    "find",
    "list",
    "read",
)
# Strong file verbs/domains that alone imply FileAgent (catch-all fallback).
FILE_FALLBACK_TERMS: tuple[str, ...] = (
    "清理",
    "删除",
    "移动",
    "复制",
    "文件",
    "目录",
    "文件夹",
    "delete",
    "remove",
    "copy",
    "move",
    "rename",
    "trash",
    "cleanup",
    "file",
    "folder",
    "directory",
)

BROWSER_DOMAIN_TERMS: tuple[str, ...] = ("网页", "浏览器", "网址", "url", "页面", "链接")
BROWSER_ACTION_TERMS: tuple[str, ...] = ("打开", "读取", "截图", "提取", "登录", "访问")
URL_TERMS: tuple[str, ...] = ("http://", "https://", "www.")
# Used by infer fallback: URL-ish or explicit browser mention.
BROWSER_HINT_TERMS: tuple[str, ...] = ("网页", "浏览器", "网址", "链接", "browser", "webpage")

SEARCH_DOMAIN_TERMS: tuple[str, ...] = ("搜索", "查询", "最新", "新闻", "资料", "信息")
SEARCH_ACTION_TERMS: tuple[str, ...] = ("搜索", "查询", "查", "找")
SEARCH_HINT_TERMS: tuple[str, ...] = ("搜索", "查询", "最新", "新闻", "search", "news", "latest")

APP_DOMAIN_TERMS: tuple[str, ...] = ("应用", "软件", "程序", "app", "notepad", "记事本")
APP_ACTION_TERMS: tuple[str, ...] = (
    "打开",
    "启动",
    "运行",
    "卸载",
    "移除",
    "删除",
    "uninstall",
    "remove",
    "open",
    "launch",
)
APP_HINT_TERMS: tuple[str, ...] = (
    "卸载",
    "打开应用",
    "启动应用",
    "uninstall",
    "open app",
    "launch",
)

DOCUMENT_DOMAIN_TERMS: tuple[str, ...] = ("pdf", "word", "docx", "pptx", "表格", "文档")
DOCUMENT_ACTION_TERMS: tuple[str, ...] = ("总结", "解析", "读取", "提取")
DOCUMENT_HINT_TERMS: tuple[str, ...] = (
    "发票",
    "合同",
    "文档",
    "pdf",
    "docx",
    "pptx",
    "总结",
    "摘要",
    "summarize",
)

UNINSTALL_TERMS: tuple[str, ...] = ("卸载", "uninstall")
CLEANUP_TERMS: tuple[str, ...] = ("清理", "cleanup", "clean up")
ORGANIZE_TERMS: tuple[str, ...] = ("整理", "organize", "归档")
FILE_TARGET_TERMS: tuple[str, ...] = ("文件", "目录", "文件夹", "file", "folder", "directory")

# --- Composed rule table (Supervisor heuristic order matters) ---------------

DELEGATION_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("ComputerAgent", COMPUTER_DOMAIN_TERMS, COMPUTER_ACTION_TERMS),
    ("FileAgent", FILE_DOMAIN_TERMS, FILE_ACTION_TERMS),
    ("BrowserAgent", BROWSER_DOMAIN_TERMS + URL_TERMS, BROWSER_ACTION_TERMS),
    ("SearchAgent", SEARCH_DOMAIN_TERMS, SEARCH_ACTION_TERMS),
    ("AppAgent", APP_DOMAIN_TERMS, APP_ACTION_TERMS),
    ("DocumentAgent", DOCUMENT_DOMAIN_TERMS, DOCUMENT_ACTION_TERMS),
)

# --- Engine routing (os vs developer) ---------------------------------------

DEVELOPER_READ_GOAL_TERMS: tuple[str, ...] = (
    "inspect",
    "analyze",
    "analyse",
    "review",
    "explain",
    "summarize",
    "summarise",
    "code",
    "repo",
    "repository",
    "git",
    "diff",
    "bug",
    "debug",
    "test",
    "tests",
    "pytest",
    "lint",
    "typecheck",
    "build",
    "compile",
    "api",
    "backend",
    "frontend",
    "database",
    "migration",
    "function",
    "class",
    "module",
    "package",
    "dependency",
    "import",
    "stacktrace",
    "traceback",
    "pr",
    "pull request",
    "代码",
    "仓库",
    "项目",
    "程序",
    "接口",
    "后端",
    "前端",
    "测试",
    "编译",
    "构建",
    "报错",
    "异常",
    "堆栈",
    "依赖",
    "模块",
    "函数",
    "迁移",
    "提交",
    "分支",
    "分析",
    "解释",
    "总结",
    "审查",
)

DEVELOPER_WRITE_INTENT_TERMS: tuple[str, ...] = (
    "fix",
    "patch",
    "repair",
    "resolve",
    "refactor",
    "implement",
    "change",
    "modify",
    "edit",
    "write",
    "add",
    "remove",
    "delete",
    "create",
    "generate",
    "scaffold",
    "update",
    "upgrade",
    "migrate",
    "replace",
    "improve",
    "rename",
    "address",
    "failing",
    "failed",
    "broken",
    "pass",
    "修复",
    "修改",
    "重构",
    "实现",
    "添加",
    "删掉",
    "删除",
    "创建",
    "更新",
    "替换",
    "改进",
    "重命名",
    "编写",
)

OS_GOAL_TERMS: tuple[str, ...] = (
    "open",
    "click",
    "browser",
    "website",
    "web page",
    "app",
    "window",
    "desktop",
    "screen",
    "screenshot",
    "folder",
    "file manager",
    "finder",
    "explorer",
    "document",
    "spreadsheet",
    "presentation",
    "word",
    "excel",
    "powerpoint",
    "calendar",
    "email",
    "remote",
    "ui",
    "mouse",
    "keyboard",
    "打开",
    "点击",
    "浏览器",
    "网页",
    "应用",
    "软件",
    "窗口",
    "桌面",
    "屏幕",
    "截图",
    "文件夹",
    "目录",
    "文件管理",
    "资源管理器",
    "文档",
    "表格",
    "演示",
    "卸载",
    "记事本",
    "计算器",
    "远程",
    "鼠标",
    "键盘",
    "下载",
)

_SYSTEM_DIAGNOSTICS_RE = re.compile(
    r"("
    r"\b(?:system|computer|machine|pc|device)\s+(?:diagnostics?|checkup|health|status|inspection)\b|"
    r"\b(?:diagnose|check|inspect)\s+(?:this\s+)?(?:system|computer|machine|pc|device)\b|"
    r"(?:\u5e2e\u6211)?(?:\u68c0\u67e5|\u67e5\u770b|\u770b\u4e00\u4e0b|\u68c0\u6d4b|\u8bca\u65ad|\u4f53\u68c0)"
    r".*(?:\u8fd9\u53f0\u7535\u8111|\u7535\u8111|\u7cfb\u7edf|\u672c\u673a|CPU|\u5185\u5b58|\u78c1\u76d8)|"
    r"(?:\u8fd9\u53f0\u7535\u8111|\u7535\u8111|\u7cfb\u7edf|\u672c\u673a|CPU|\u5185\u5b58|\u78c1\u76d8)"
    r".*(?:\u68c0\u67e5|\u67e5\u770b|\u770b\u4e00\u4e0b|\u68c0\u6d4b|\u8bca\u65ad|\u4f53\u68c0)"
    r")",
    re.IGNORECASE,
)
_CHINESE_SYSTEM_DIAGNOSTICS_RE = re.compile(
    r"(?=.*(?:检查|查看|看一下|查|检测|诊断|体检|状态))"
    r"(?=.*(?:这台电脑|电脑|磁盘|内存|进程|本地\s*AI|CPU|系统(?:状态|诊断|体检|信息|配置|运行)))",
    re.IGNORECASE,
)


def goal_has_developer_read_intent(text: str) -> bool:
    lower = text.strip().lower()
    return bool(lower) and contains_any(lower, DEVELOPER_READ_GOAL_TERMS)


def goal_has_developer_write_intent(text: str) -> bool:
    lower = text.strip().lower()
    return bool(lower) and contains_any(lower, DEVELOPER_WRITE_INTENT_TERMS)


def goal_has_os_intent(text: str) -> bool:
    lower = text.strip().lower()
    return bool(lower) and contains_any(lower, OS_GOAL_TERMS)


def goal_is_system_diagnostics(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    return bool(_SYSTEM_DIAGNOSTICS_RE.search(normalized) or _CHINESE_SYSTEM_DIAGNOSTICS_RE.search(normalized))
