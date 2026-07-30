from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from app.agents.path_detection import find_explicit_path
from app.llm.registry import get_effective_settings

DELETE_TERMS = ("delete", "remove", "trash", "删除", "删掉", "移除", "清理")
UNINSTALL_TERMS = ("uninstall", "卸载")
SYSTEM_CHECK_TERMS = (
    "检查电脑状态",
    "检查这台电脑",
    "电脑状态",
    "系统体检",
    "运行状态",
    "关键进程",
    "本地 ai",
    "本地ai",
    "computer status",
    "system status",
    "diagnostics",
)
DRIVE_CLEANUP_RE = re.compile(r"(?P<drive>[a-zA-Z])\s*盘")
OPEN_APP_EXCLUDE_TERMS = (
    "文件",
    "目录",
    "网站",
    "网页",
    "链接",
    "http",
    "www.",
    ".com",
    ".cn",
    ".net",
    ".org",
    "file",
    "folder",
    "directory",
    "website",
    "page",
    "url",
    "workbook",
    "sheet",
    "页面",
    "工作簿",
    "单元格",
)
OPEN_APP_NAME_ALIASES = {"记事本": "notepad", "计算器": "calculator"}
PATH_SUFFIXES = (
    " 这个文件夹",
    " 这个目录",
    " 这个文件",
    " 整个文件夹",
    " 文件夹",
    " 目录",
    " 文件",
)
DOCUMENT_SUMMARY_TERMS = ("总结", "摘要", "概述", "summarize", "summary", "digest")
DOCUMENT_EXTRACT_TERMS = ("读取", "提取", "抽取", "业务字段", "发票金额", "extract", "read")
DOCUMENT_DOMAIN_TERMS = (
    "文档",
    "文件",
    "模板",
    "pdf",
    "docx",
    "xlsx",
    "pptx",
    "ocr",
    "transcript",
)
BROWSER_READ_TERMS = ("核对", "检查", "读取", "查看", "价格", "read", "review", "check", "inspect")
BROWSER_PAGE_TERMS = ("网页", "页面", "网站", "供应商", "page", "website", "site")
UNTRUSTED_CONTENT_GUARD_TERMS = (
    "只是恶意内容",
    "当作不可信数据",
    "不得改变任务目标",
    "不能改变任务目标",
    "绝不能把它当授权",
    "不要把它当授权",
    "不要服从",
    "not authorization",
    "untrusted data",
    "must not change the task",
    "do not follow",
)
EXTERNAL_ACTION_TERMS = (
    "上传",
    "发送",
    "外发",
    "付款",
    "支付",
    "下单",
    "购买",
    "upload",
    "send",
    "submit",
    "pay",
    "purchase",
    "checkout",
)
SENSITIVE_URL_QUERY_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "code",
        "cookie",
        "credential",
        "password",
        "secret",
        "sig",
        "signature",
        "token",
    }
)
_HTTP_URL_RE = re.compile(
    r"https?://[^\s<>\"'“”‘’，。；：！？、（）【】《》]+",
    re.IGNORECASE,
)
_DOCUMENT_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:[\\/]|(?<![:/])\/)[^\r\n\"<>|?*]+?\.(?:pdf|docx?|xlsx?|pptx?|txt|md|csv))",
    re.IGNORECASE,
)
_MEMORY_ID_RE = re.compile(r"\bmem_[A-Za-z0-9_-]+\b")
_EXCEL_CELL_RE = re.compile(r"\b[A-Z]{1,3}[1-9][0-9]*\b", re.IGNORECASE)
OPERATIONAL_SUFFIXES = (
    "请先核对目标和影响范围。",
    "遇到登录、验证码、高影响提交或来源冲突时请停下来让我接管。",
)


class PlannerDeterministicIntentMixin:
    """Parse deterministic planner intents without coupling them to LLM orchestration."""

    def _has_delete_intent(self, goal: str) -> bool:
        normalized = goal.lower()
        return any(term in normalized for term in DELETE_TERMS)

    def _has_cleanup_intent(self, goal: str) -> bool:
        normalized = goal.lower()
        return "清理" in normalized or "cleanup" in normalized or "clean up" in normalized

    def _has_unnegated_mutation_intent(self, goal: str) -> bool:
        """Keep read-only fast paths from discarding a requested write step."""

        normalized = goal.casefold()
        english_mutation = (
            r"(?:delet(?:e|ing)|remov(?:e|ing)|trash(?:ing)?|"
            r"clean(?:ing)?(?:\s+up)?|edit(?:ing)?|modif(?:y|ying)|"
            r"writ(?:e|ing)|updat(?:e|ing)|replac(?:e|ing)|"
            r"sav(?:e|ing)|export(?:ing)?|creat(?:e|ing))"
        )
        normalized = re.sub(
            r"(?:不要|无需|不需|不能|不可|不)\s*(?:执行|进行|实际)?\s*"
            r"(?:删除|删掉|移除|清理|修改|写入|更新|替换|保存|导出|创建)",
            " ",
            normalized,
        )
        normalized = re.sub(r"清理\s*(?:建议|意见|方案)", " ", normalized)
        normalized = re.sub(
            rf"\b(?:do\s+not|don't|without|never)\s+{english_mutation}\b",
            " ",
            normalized,
        )
        normalized = re.sub(r"\b(?:cleanup|clean-up)\s+(?:advice|suggestions?|plan)\b", " ", normalized)
        if any(
            term in normalized
            for term in ("删除", "删掉", "移除", "清理", "修改", "写入", "更新", "替换", "保存", "导出", "创建")
        ):
            return True
        return re.search(rf"\b(?:{english_mutation}|cleanup)\b", normalized) is not None

    def _has_large_files_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        return (
            "大文件" in goal
            or "占空间最大的文件" in goal
            or "占用空间最大的文件" in goal
            or re.search(r"\b(largest|large|biggest)\s+files?\b", normalized) is not None
        )

    def _has_duplicate_search_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        return bool(
            ("重复文件" in goal or re.search(r"\bduplicate\s+files?\b", normalized))
            and not self._has_unnegated_mutation_intent(goal)
        )

    def _extract_write_text_request(self, goal: str) -> tuple[str, str] | None:
        normalized = goal.casefold()
        if not (
            ("新建" in goal or "创建" in goal or re.search(r"\b(?:create|write)\b", normalized))
            and ("写入" in goal or "内容" in goal or "content" in normalized)
            and ("预览" in goal or "preview" in normalized)
        ):
            return None
        path = self._extract_document_path(goal)
        if not path:
            return None
        match = re.search(
            r"(?:写入|write)\s*(?:以下)?(?:完整)?(?:内容|content).*?[：:](?P<text>.+)$",
            goal,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return None
        text = self._strip_operational_suffix(match.group("text")).strip()
        if not text or len(text) > 50_000:
            return None
        return path, text

    def _extract_edit_text_request(self, goal: str) -> tuple[str, str, str] | None:
        normalized = goal.casefold()
        if not (
            ("替换" in goal or "replace" in normalized)
            and ("差异" in goal or "预览" in goal or "diff" in normalized or "preview" in normalized)
        ):
            return None
        path = self._extract_document_path(goal)
        if not path:
            return None
        match = re.search(
            r"(?:精确文本)?[“\"](?P<old>.*?)[”\"]\s*(?:替换为|replace\s+with)\s*[“\"](?P<new>.*?)[”\"]",
            goal,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return None
        old_string = match.group("old")
        new_string = match.group("new")
        if not old_string or len(old_string) > 20_000 or len(new_string) > 50_000:
            return None
        return path, old_string, new_string

    def _extract_create_folder_path(self, goal: str) -> str | None:
        normalized = goal.casefold()
        if not (
            ("创建" in goal or "新建" in goal or re.search(r"\bcreate\b", normalized))
            and ("文件夹" in goal or "目录" in goal or "folder" in normalized or "directory" in normalized)
            and ("确认" in goal or "预览" in goal or "confirm" in normalized or "preview" in normalized)
        ):
            return None
        match = re.search(
            r"(?:创建|新建|create)\s+(?P<path>(?:[A-Za-z]:[\\/]|/).+?)"
            r"(?=\s+(?:归档)?(?:文件夹|目录)|\s+(?:folder|directory)\b)",
            goal,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        path = self._clean_path_candidate(match.group("path"))
        return path if path else None

    def _extract_excel_write_request(self, goal: str) -> tuple[str, str, str, str] | None:
        normalized = goal.casefold()
        if not (
            ("工作簿" in goal or "excel" in normalized or ".xlsx" in normalized)
            and ("更新为" in goal or "写入" in goal or "update" in normalized)
            and ("预览" in goal or "preview" in normalized)
        ):
            return None
        path = self._extract_document_path(goal)
        cell_match = _EXCEL_CELL_RE.search(goal)
        sheet_match = re.search(r"工作簿的(?P<sheet>[^，,\s]{1,40})页", goal)
        value_match = re.search(r"(?:更新为|写入)\s*(?P<value>[^，,\r\n。]+)", goal)
        if not path or not cell_match or not sheet_match or not value_match:
            return None
        value = value_match.group("value").strip()
        if not value or len(value) > 1_000:
            return None
        return path, sheet_match.group("sheet").strip(), cell_match.group(0).upper(), value

    def _has_batch_organize_preview_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        return bool(
            ("整理" in goal or "organize" in normalized)
            and ("发票" in goal or "invoice" in normalized)
            and ("按月份" in goal or "by month" in normalized)
            and ("预览" in goal or "preview" in normalized)
            and not self._has_unnegated_delete_intent(goal)
        )

    def _has_unnegated_delete_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        normalized = re.sub(
            r"(?:不要|不得|不能|不可|绝不|无需)\s*(?:执行|实际)?\s*(?:删除|删掉|移除)",
            " ",
            normalized,
        )
        normalized = re.sub(
            r"\b(?:do\s+not|don't|never|without)\s+(?:delete|remove|trash)\b",
            " ",
            normalized,
        )
        return (
            any(term in normalized for term in ("删除", "删掉", "移除"))
            or re.search(r"\b(?:delete|remove|trash)\b", normalized) is not None
        )

    def _cleanup_roots(self, goal: str) -> list[str]:
        settings_roots = [str(path) for path in get_effective_settings().allowed_directories or []]
        drive = self._extract_drive_root(goal)
        if drive:
            normalized_drive = drive.casefold().rstrip("\\/")
            matching_roots = [
                root for root in settings_roots if str(Path(root).drive).casefold().rstrip("\\/") == normalized_drive
            ]
            return matching_roots or settings_roots
        return settings_roots

    def _extract_drive_root(self, goal: str) -> str | None:
        match = DRIVE_CLEANUP_RE.search(goal)
        if not match:
            return None
        return f"{match.group('drive').upper()}:"

    def _has_uninstall_intent(self, goal: str) -> bool:
        normalized = goal.lower()
        return any(term in normalized for term in UNINSTALL_TERMS)

    def _has_open_app_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        has_open_verb = "打开" in goal or "启动" in goal or re.search(r"\b(open|launch)\b", normalized) is not None
        if not has_open_verb:
            return False
        return not any(term in normalized for term in OPEN_APP_EXCLUDE_TERMS)

    def _extract_http_url(self, goal: str) -> str | None:
        match = _HTTP_URL_RE.search(goal)
        if not match:
            return None
        candidate = match.group(0).rstrip(".,;:!?。，；：！？)]}）")
        try:
            parsed = urlsplit(candidate)
        except ValueError:
            return None
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username or parsed.password:
            return None
        try:
            query_keys = {key.casefold() for key, _value in parse_qsl(parsed.query, keep_blank_values=True)}
        except ValueError:
            return None
        if query_keys & SENSITIVE_URL_QUERY_KEYS:
            return None
        return candidate

    def _http_origin(self, url: str) -> str | None:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            return None
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return None
        if port is None:
            port = 443 if parsed.scheme.casefold() == "https" else 80
        hostname = parsed.hostname.casefold().rstrip(".")
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        return f"{parsed.scheme.casefold()}://{hostname}:{port}"

    def _has_untrusted_external_action(self, goal: str) -> bool:
        normalized = goal.casefold()
        action_positions = [
            match.start() for term in EXTERNAL_ACTION_TERMS for match in re.finditer(re.escape(term), normalized)
        ]
        if not action_positions:
            return False
        guard_positions = [
            match.start()
            for term in UNTRUSTED_CONTENT_GUARD_TERMS
            for match in re.finditer(re.escape(term.casefold()), normalized)
        ]
        if not guard_positions:
            return True
        # The safe deterministic read may ignore an external-action phrase only
        # when the user subsequently labels that phrase as untrusted content.
        # A new send/upload/submit request after the guard must fall back to the
        # full planner and policy pipeline rather than being silently dropped.
        first_guard = min(guard_positions)
        return any(position > first_guard for position in action_positions)

    def _has_browser_submit_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        if not self._extract_http_url(goal) or not ("表单" in goal or "form" in normalized):
            return False
        submit_positions = [
            match.start() for term in ("提交", "submit") for match in re.finditer(re.escape(term), normalized)
        ]
        guard_positions = [
            match.start()
            for term in UNTRUSTED_CONTENT_GUARD_TERMS
            for match in re.finditer(re.escape(term.casefold()), normalized)
        ]
        if (
            submit_positions
            and guard_positions
            and all(position < min(guard_positions) for position in submit_positions)
        ):
            return False
        if re.search(
            r"(?:提交前|before\s+submitt?ing).{0,16}(?:停下|停止|stop|pause)",
            normalized,
            flags=re.IGNORECASE,
        ):
            return False
        if re.search(
            r"(?:不要|不得|不能|不可|绝不|无需).{0,16}(?:提交|submit)",
            normalized,
            flags=re.IGNORECASE,
        ):
            return False
        return "准备提交" in goal or "提交" in goal or re.search(r"\bsubmit\b", normalized) is not None

    def _extract_browser_fill_fields(self, goal: str) -> dict[str, str]:
        fields: dict[str, str] = {}
        for match in re.finditer(
            r"(?P<name>公司名称|联系人|姓名|部门|company\s+name|contact)"
            r"\s*[：:]?\s*[“\"](?P<value>[^”\"]{1,500})[”\"]",
            goal,
            flags=re.IGNORECASE,
        ):
            name = re.sub(r"\s+", "_", match.group("name").strip().casefold())
            fields[name] = match.group("value")
        return fields

    def _has_browser_fill_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        return bool(
            self._extract_http_url(goal)
            and ("填写" in goal or re.search(r"\bfill\b", normalized))
            and ("网站" in goal or "表单" in goal or "site" in normalized or "form" in normalized)
            and ("提交前停下" in goal or "不要提交" in goal or "before submitting" in normalized)
            and self._extract_browser_fill_fields(goal)
            and not self._has_browser_submit_intent(goal)
        )

    def _has_browser_read_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        if not self._extract_http_url(goal) or self._has_browser_submit_intent(goal):
            return False
        if not any(term.casefold() in normalized for term in BROWSER_READ_TERMS):
            return False
        if not any(term.casefold() in normalized for term in BROWSER_PAGE_TERMS):
            return False
        return not self._has_untrusted_external_action(goal)

    def _extract_document_path(self, goal: str) -> str | None:
        match = _DOCUMENT_PATH_RE.search(goal)
        if match:
            return self._clean_path_candidate(match.group("path"))
        return self._extract_windows_path(goal)

    def _has_document_summary_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        return bool(
            self._extract_document_path(goal)
            and any(term.casefold() in normalized for term in DOCUMENT_SUMMARY_TERMS)
            and not self._has_unnegated_mutation_intent(goal)
            and not self._has_untrusted_external_action(goal)
        )

    def _has_document_extract_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        return bool(
            self._extract_document_path(goal)
            and any(term.casefold() in normalized for term in DOCUMENT_EXTRACT_TERMS)
            and not self._has_unnegated_mutation_intent(goal)
            and not self._has_untrusted_external_action(goal)
        )

    def _extract_document_question(self, goal: str) -> str:
        match = re.search(
            r"(?:回答|answer)\s*(?P<question>.+?)(?:，|,|并给出|with\s+(?:a\s+)?citation|$)",
            goal,
            flags=re.IGNORECASE,
        )
        if not match:
            return ""
        return match.group("question").strip(" ：:，,。.?？")

    def _has_document_qa_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        return bool(
            self._extract_document_path(goal)
            and self._extract_document_question(goal)
            and ("引用" in goal or "来源位置" in goal or "citation" in normalized)
            and ("问答" in goal or "回答" in goal or "question" in normalized or "answer" in normalized)
            and not self._has_unnegated_mutation_intent(goal)
            and not self._has_untrusted_external_action(goal)
        )

    def _extract_memory_preference_content(self, goal: str) -> str:
        match = re.search(
            r"(?:用户)?偏好\s*[“\"](?P<content>[^”\"]{1,2000})[”\"]",
            goal,
        )
        return match.group("content").strip() if match else ""

    def _extract_memory_id(self, goal: str) -> str:
        match = _MEMORY_ID_RE.search(goal)
        return match.group(0) if match else ""

    def _has_git_status_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        return bool(
            ("git" in normalized and ("状态" in goal or "status" in normalized))
            and not self._has_unnegated_mutation_intent(goal)
        )

    def _has_pytest_inventory_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        return bool(
            "pytest" in normalized
            and ("列出" in goal or "盘点" in goal or "inventory" in normalized or "list" in normalized)
            and ("测试" in goal or "test" in normalized)
            and not self._has_unnegated_mutation_intent(goal)
        )

    def _strip_operational_suffix(self, value: str) -> str:
        result = value
        positions = [result.find(suffix) for suffix in OPERATIONAL_SUFFIXES if suffix in result]
        if positions:
            result = result[: min(positions)]
        return result.rstrip()

    def _extract_open_app_query(self, goal: str) -> str:
        query = goal.strip()
        for term in ("帮我", "请", "麻烦", "一下", "这个", "应用", "软件", "程序"):
            query = query.replace(term, "")
        for term in ("打开", "启动"):
            query = query.replace(term, "")
        query = re.sub(r"\b(open|launch|the|app|application)\b", "", query, flags=re.IGNORECASE)
        query = query.strip(" ：:，,。.!！?？\"'“”‘’")
        return OPEN_APP_NAME_ALIASES.get(query.casefold(), query)

    def _has_file_search_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        if re.search(r"\b(find|search|locate)\b.*\bfiles?\b", normalized):
            return True
        if re.search(r"\bfiles?\b.*\b(named|called)\b", normalized):
            return True
        return "文件" in goal and ("找" in goal or "搜" in goal)

    def _has_developer_search_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        has_search = (
            "搜索" in goal
            or "查找" in goal
            or "检索" in goal
            or re.search(r"\b(search|find|grep|locate)\b", normalized) is not None
        )
        developer_scope = (
            "代码" in goal
            or "源码" in goal
            or "仓库" in goal
            or "行号" in goal
            or any(
                re.search(rf"\b{re.escape(term)}\b", normalized) is not None
                for term in ("code", "codebase", "repository", "repo", "source")
            )
        )
        return bool(has_search and developer_scope)

    def _has_full_text_search_intent(self, goal: str) -> bool:
        if self._has_developer_search_intent(goal):
            return False
        normalized = goal.casefold()
        has_search = (
            "搜索" in goal
            or "查找" in goal
            or "检索" in goal
            or re.search(r"\b(search|find|grep|locate)\b", normalized) is not None
        )
        content_scope = (
            "全文" in goal
            or "内容" in goal
            or "文本" in goal
            or "包含" in goal
            or any(term in normalized for term in ("full text", "full-text", "contents", "containing"))
        )
        return bool(has_search and content_scope)

    def _extract_developer_search_query(self, goal: str) -> str:
        quoted = re.search(r"[`'\"“](?P<q>[^`'\"”]+)[`'\"”]", goal)
        if quoted:
            return quoted.group("q").strip()
        symbol = re.search(
            r"(?:搜索|查找|检索)\s+(?P<q>[A-Za-z_][A-Za-z0-9_.:-]*)\s+(?:的)?(?:使用位置|定义|引用|调用)",
            goal,
        )
        if symbol:
            return symbol.group("q").strip()
        english = re.search(
            r"\b(?:search|find|grep|locate)(?:\s+(?:the|code|codebase|repository|repo))?\s+(?:for\s+)?(?P<q>[A-Za-z_][A-Za-z0-9_.:-]*)",
            goal,
            flags=re.IGNORECASE,
        )
        if english:
            return english.group("q").strip()
        identifiers = re.findall(r"\b[A-Za-z_][A-Za-z0-9_.:-]*\b", goal)
        ignored = {"search", "find", "grep", "locate", "code", "codebase", "repo", "repository"}
        return next((item for item in identifiers if item.casefold() not in ignored), "")

    def _extract_full_text_search_query(self, goal: str) -> str:
        quoted = re.search(r"[`'\"“](?P<q>[^`'\"”]+)[`'\"”]", goal)
        if quoted:
            return quoted.group("q").strip()
        contained = re.search(r"包含\s*(?P<q>.+?)\s*的(?:内容|文本|文件|位置)", goal)
        if contained:
            return contained.group("q").strip(" ：:，,。.")
        english = re.search(
            r"\b(?:containing|for)\s+(?P<q>.+?)(?:\s+in\s+(?:the\s+)?(?:file|files|documents?)|[,.;]|$)",
            goal,
            flags=re.IGNORECASE,
        )
        if english:
            return english.group("q").strip(" :,.\"'")
        return self._extract_search_query(goal)

    def _extract_search_query(self, goal: str) -> str:
        text = goal.strip()
        colon_match = re.search(r"[:：](?P<q>.+)$", text)
        if colon_match:
            candidate = colon_match.group("q")
        else:
            candidate = text
            for term in ("帮我", "请", "麻烦", "一下", "所有", "相关"):
                candidate = candidate.replace(term, "")
            for term in ("查找", "搜索", "找到", "寻找", "搜", "找"):
                candidate = candidate.replace(term, "")
            candidate = re.sub(
                r"\b(find|search( for)?|locate|named|called|files?|the)\b", "", candidate, flags=re.IGNORECASE
            )
            candidate = candidate.replace("文件名", "").replace("文件", "")
        return candidate.strip(" ：:，,。.\"'“”‘’")

    def _has_system_check_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        if any(term.casefold() in normalized for term in SYSTEM_CHECK_TERMS):
            return True
        return (
            "检查" in goal
            and ("电脑" in goal or "系统" in goal)
            and any(term in goal for term in ("状态", "磁盘", "内存", "进程", "可用性"))
        )

    def _extract_uninstall_query(self, goal: str) -> str:
        query = goal.strip()
        for term in ("帮我", "请", "一下", "应用", "软件", "程序"):
            query = query.replace(term, "")
        for term in ("卸载", "uninstall"):
            query = re.sub(re.escape(term), "", query, flags=re.IGNORECASE)
        return query.strip(" ：:，,。.")

    def _extract_windows_path(self, goal: str) -> str | None:
        quoted = re.search(r"[\"“](?P<path>[A-Za-z]:[\\/][^\"”]+)[\"”]", goal)
        if quoted:
            return self._clean_path_candidate(quoted.group("path"))

        match = find_explicit_path(goal)
        if not match:
            return None
        return self._clean_path_candidate(match)

    def _clean_path_candidate(self, value: str) -> str:
        candidate = value.strip().strip("`'\"“”‘’")
        candidate = candidate.rstrip("。.,，;；、)]}）")
        for suffix in PATH_SUFFIXES:
            if candidate.endswith(suffix):
                candidate = candidate[: -len(suffix)].rstrip()

        if Path(candidate).exists():
            return str(Path(candidate).resolve(strict=False))

        parts = candidate.split()
        while len(parts) > 1:
            shortened = " ".join(parts[:-1]).rstrip("。.,，;；、)]}）")
            if Path(shortened).exists():
                return str(Path(shortened).resolve(strict=False))
            parts = parts[:-1]
        return candidate
