from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.agents.delegation_rules import contains_any
from app.agents.path_detection import find_explicit_path
from app.llm.base import LLMProvider
from app.llm.types import LLMResponse
from app.llm.usage import estimate_usage

_WEB_DOMAIN_HINT = re.compile(
    r"(?<![a-z0-9_@.])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?P<tld>[a-z]{2,63})"
    r"(?::\d{1,5})?(?:[/?:#][^\s]*)?",
    re.IGNORECASE,
)
_COMMON_WEB_TLDS = frozenset(
    {
        "ai",
        "app",
        "au",
        "biz",
        "ca",
        "cloud",
        "cn",
        "co",
        "com",
        "de",
        "dev",
        "edu",
        "fr",
        "gov",
        "hk",
        "info",
        "io",
        "jp",
        "me",
        "net",
        "online",
        "org",
        "site",
        "tech",
        "tw",
        "uk",
        "us",
        "xyz",
    }
)


def _contains_web_domain_hint(value: str) -> bool:
    return any(match.group("tld").casefold() in _COMMON_WEB_TLDS for match in _WEB_DOMAIN_HINT.finditer(value))


class MockProvider(LLMProvider):
    name = "mock"

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        return f"Mock response for: {user[:160]}"

    async def chat_result(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        content = await self.chat(messages, model=model, temperature=temperature, tools=tools)
        return LLMResponse(
            content=content,
            provider=self.name,
            model=model or "mock",
            usage=estimate_usage(messages, content),
            metadata={"degraded": True, "mock": True},
        )

    async def structured_chat(self, messages: list[dict[str, str]], output_schema: dict[str, Any]) -> dict[str, Any]:
        planner_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        supervisor_hint = self._extract_supervisor_routing_hint(planner_user)
        revision_feedback = self._extract_planner_revision_feedback(planner_user)
        raw_user = planner_user
        if "User goal:" in raw_user:
            raw_user = raw_user.split("User goal:", 1)[1]
        if self._is_supervisor_schema(output_schema):
            return self._supervisor_decision(raw_user)
        if self._is_agent_action_schema(output_schema):
            return self._agent_action(raw_user, messages)
        hint_plan = self._plan_from_supervisor_hint(
            supervisor_hint,
            raw_user,
            force_hint=bool(revision_feedback.strip()),
        )
        if hint_plan is not None:
            return hint_plan
        return self._build_goal_plan(raw_user)

    def _build_goal_plan(self, raw_user: str) -> dict[str, Any]:
        user = raw_user.lower()
        if any(term in user for term in ["password", "cookie", "token", "credential"]):
            tool = "security.forbidden"
            agent = "SafetyReviewAgent"
            risk = "R4_FORBIDDEN_OR_HANDOFF"
            description = "Deny access to credentials or browser secrets."
        elif any(term in user for term in ["duplicate", "重复"]):
            tool = "file.find_duplicates"
            agent = "FileAgent"
            risk = "R0_READ_ONLY"
            description = "Find duplicate files in authorized directories without deleting anything."
        elif any(term in user for term in ["system", "配置", "cpu", "memory", "电脑"]):
            tool = "system.get_info"
            agent = "ComputerAgent"
            risk = "R0_READ_ONLY"
            description = "Read basic local system information."
        elif contains_any(user, ("organize", "整理", "move", "移动", "invoice", "发票")):
            tool = "file.preview_batch_operation"
            agent = "FileAgent"
            risk = "R2_REVERSIBLE_MODIFY"
            description = "Preview a reversible file organization operation and request approval."
        elif any(term in user for term in ["清理", "cleanup", "clean up"]) and not self._extract_windows_path(user):
            tool = "file.cleanup_plan"
            agent = "FileAgent"
            risk = "R0_READ_ONLY"
            description = "Scan authorized directories and generate a cleanup preview without deleting files."
        elif any(term in user for term in ["delete", "remove", "trash", "删除", "删掉", "移除"]):
            tool = "file.trash"
            agent = "FileAgent"
            risk = "R3_DESTRUCTIVE_OR_SYSTEM"
            description = "Preview moving the requested file or folder to the recycle bin and request approval."
        else:
            tool = "file.search_by_name"
            agent = "FileAgent"
            risk = "R0_READ_ONLY"
            description = "Search authorized files by name."

        return {
            "goal": user or "mock task",
            "assumptions": ["Generated by MockProvider when no real provider is configured."],
            "steps": [
                {
                    "id": "step_1",
                    "agent_name": agent,
                    "tool_name": tool,
                    "description": description,
                    "args": self._args_for_tool(tool, user, raw_user),
                    "expected_observation": "Structured observation recorded in the task timeline.",
                    "risk_level": risk,
                    "requires_approval": risk.startswith("R2") or risk.startswith("R3"),
                    "depends_on": [],
                    "rollback_strategy": "No changes during dry-run; modifying execution must return rollback_info.",
                }
            ],
        }

    def _args_for_tool(self, tool: str, user: str, raw_user: str | None = None) -> dict[str, Any]:
        if tool == "file.trash":
            source = raw_user if raw_user is not None else user
            path = self._extract_windows_path(source)
            return {"path": path or source, "dry_run": True}
        if tool == "file.cleanup_plan":
            return {"threshold_mb": 50, "older_than_days": 30}
        return {"query": user, "dry_run": True}

    def _extract_windows_path(self, user: str) -> str | None:
        match = find_explicit_path(user)
        if not match:
            return None
        candidate = match.strip().rstrip("。.,，;；、)]}）")
        if Path(candidate).exists():
            return str(Path(candidate).resolve(strict=False))
        return candidate

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        return [[float(len(text) % 13), float(sum(map(ord, text)) % 17)] for text in texts]

    async def vision(self, image_path: str, prompt: str, model: str | None = None) -> str:
        from pathlib import Path

        name = Path(image_path).name
        return f"[mock-vision] {name}: {prompt[:60]} -> 这张图像看上去包含办公场景元素。"

    async def ocr(self, image_path: str) -> str:
        from pathlib import Path

        name = Path(image_path).name
        return f"[mock-ocr] {name}: 示例文字-001 sample text"

    def _is_supervisor_schema(self, output_schema: dict[str, Any]) -> bool:
        required = set(output_schema.get("required") or [])
        properties = set((output_schema.get("properties") or {}).keys())
        return {"delegate", "reply"}.issubset(required | properties)

    def _is_agent_action_schema(self, output_schema: dict[str, Any]) -> bool:
        required = set(output_schema.get("required") or [])
        properties = set((output_schema.get("properties") or {}).keys())
        if "kind" not in (required | properties):
            return False
        kind_prop = (output_schema.get("properties") or {}).get("kind") or {}
        enum = kind_prop.get("enum") or []
        return "propose_tool" in enum or "request_revision" in enum

    def _agent_action(self, raw_user: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        user = raw_user.lower()
        system_text = " ".join(m.get("content", "") for m in messages if m.get("role") == "system").lower()
        agent_name = self._active_agent_name(system_text)
        if agent_name == "fileagent":
            tool = "file.find_duplicates" if any(t in user for t in ["duplicate", "重复"]) else "file.search_by_name"
        elif agent_name == "documentagent":
            tool = "document.summarize"
        elif agent_name == "computeragent":
            tool = "system.get_info"
        elif agent_name == "appagent":
            tool = "app.list_installed"
        elif agent_name == "browseragent":
            tool = "browser.read_page"
        elif agent_name == "searchagent":
            tool = "search.query"
        else:
            tool = "file.search_by_name"
        return {
            "kind": "propose_tool",
            "tool_name": tool,
            "args": {"dry_run": True},
            "rationale": f"Mock subagent proposes {tool} based on the step context.",
            "follow_up_question": "",
        }

    def _active_agent_name(self, system_text: str) -> str:
        match = re.search(
            r"\byou\s+are\s+(fileagent|documentagent|computeragent|appagent|browseragent|searchagent)\b",
            system_text,
        )
        if match:
            return match.group(1)
        for name in ("fileagent", "documentagent", "computeragent", "appagent", "browseragent", "searchagent"):
            if name in system_text:
                return name
        return ""

    def _supervisor_decision(self, raw_user: str) -> dict[str, Any]:
        if "User message:" in raw_user:
            raw_user = raw_user.split("User message:", 1)[1]
        user = raw_user.strip().lower()
        chat_only = [
            "你好",
            "在吗",
            "谢谢",
            "你是谁",
            "你是什么",
            "什么模型",
            "是什么模型",
            "怎么工作",
            "什么意思",
            "为什么",
            "然后呢",
            "继续",
            "聊天",
        ]
        if any(term in user for term in chat_only):
            if "聊天" in user:
                reply = (
                    "当然可以聊天。你可以直接问我问题或跟我说想法；"
                    "只有你明确要我操作电脑、文件、浏览器或应用时，我才会启动执行任务。"
                )
            elif "什么模型" in user or "是什么模型" in user or "你是什么" in user or "你是谁" in user:
                reply = (
                    "我是 Lengrvis 里的主管 Agent。我的工作是先和你自然对话，理解你要什么；"
                    "如果需要查文件、操作电脑、打开网页或处理文档，我再把具体工作交给对应 Agent。"
                )
            elif "你好" in user:
                reply = "你好，我在。你可以直接和我聊天，也可以告诉我需要处理的电脑、文件或应用任务。"
            else:
                reply = "我在。你可以正常和我说话；需要实际操作时，我会先说明再分配给对应 Agent。"
            return {
                "delegate": False,
                "reply": reply,
                "agent_hint": "",
            }
        from app.agents.delegation_rules import (
            CLEANUP_TERMS,
            COMPUTER_ACTION_TERMS,
            COMPUTER_DOMAIN_TERMS,
            FILE_ACTION_TERMS,
            FILE_DOMAIN_TERMS,
            FILE_TARGET_TERMS,
            SEARCH_HINT_TERMS,
            contains_any,
        )

        if contains_any(user, COMPUTER_ACTION_TERMS) and contains_any(user, COMPUTER_DOMAIN_TERMS):
            return {
                "delegate": True,
                "reply": "收到，我会把这个执行请求交给电脑 Agent，后台处理并持续反馈进展。",
                "agent_hint": "ComputerAgent",
            }
        if contains_any(user, CLEANUP_TERMS) and contains_any(user, FILE_TARGET_TERMS + ("盘",)):
            return {
                "delegate": True,
                "reply": "收到，我会先生成清理预览，不会直接删除文件；需要执行清理时会再请你审批。",
                "agent_hint": "FileAgent",
            }
        if contains_any(user, FILE_ACTION_TERMS) and contains_any(user, FILE_DOMAIN_TERMS):
            return {
                "delegate": True,
                "reply": "收到，我会把这个执行请求交给文件 Agent，后台处理并持续反馈进展。",
                "agent_hint": "FileAgent",
            }
        has_web_term = any(term in user for term in ["网页", "浏览器", "网址", "链接", "http", "www."])
        if has_web_term or _contains_web_domain_hint(user):
            return {
                "delegate": True,
                "reply": "收到，我会把这个网页读取请求交给浏览器 Agent，后台处理并持续反馈进展。",
                "agent_hint": "BrowserAgent",
            }
        if contains_any(user, SEARCH_HINT_TERMS) and not contains_any(user, FILE_TARGET_TERMS):
            return {
                "delegate": True,
                "reply": "收到，我会把这个联网搜索请求交给搜索 Agent，后台处理并持续反馈进展。",
                "agent_hint": "SearchAgent",
            }
        return {
            "delegate": False,
            "reply": self._natural_chat_reply(user),
            "agent_hint": "",
        }

    def _extract_supervisor_routing_hint(self, raw_user: str) -> str:
        match = re.search(r"Supervisor routing hint:\s*([A-Za-z]+Agent)", raw_user)
        if match:
            return match.group(1)
        return ""

    def _extract_planner_revision_feedback(self, raw_user: str) -> str:
        match = re.search(r"Planner revision feedback:\n(.*?)(?:\n\nMode:|\Z)", raw_user, re.DOTALL)
        if not match:
            return ""
        return match.group(1).strip()

    def _plan_from_supervisor_hint(self, hint: str, user: str, *, force_hint: bool = False) -> dict[str, Any] | None:
        normalized = hint.strip()
        if not normalized:
            return None
        lowered = normalized.casefold()
        user_lower = user.lower()
        if not force_hint:
            goal_plan = self._build_goal_plan(user)
            step_agent = str(goal_plan["steps"][0]["agent_name"]).casefold()
            if step_agent == lowered:
                return goal_plan
        if lowered == "browseragent":
            url_match = re.search(r"https?://[^\s]+", user)
            return self._mock_plan_payload(
                user_lower,
                tool="browser.read_page",
                agent="BrowserAgent",
                description="Read the requested web page without submitting forms.",
                args={"url": url_match.group(0) if url_match else "https://example.com", "dry_run": True},
            )
        if lowered == "searchagent":
            return self._mock_plan_payload(
                user_lower,
                tool="search.query",
                agent="SearchAgent",
                description="Query the configured web search provider and return sourced results.",
                args={"query": user.strip() or "latest news", "dry_run": True},
            )
        if lowered == "documentagent":
            return self._mock_plan_payload(
                user_lower,
                tool="document.summarize",
                agent="DocumentAgent",
                description="Summarize the requested document.",
                args={"dry_run": True},
            )
        if lowered == "computeragent":
            return self._mock_plan_payload(
                user_lower,
                tool="system.get_info",
                agent="ComputerAgent",
                description="Read basic local system information.",
                args={},
            )
        if lowered == "appagent":
            return self._mock_plan_payload(
                user_lower,
                tool="app.list_installed",
                agent="AppAgent",
                description="Inspect installed applications relevant to the request.",
                args={},
            )
        if lowered == "fileagent":
            return self._mock_plan_payload(
                user_lower,
                tool="file.search_by_name",
                agent="FileAgent",
                description="Search authorized files by name.",
                args={"query": user.strip() or "search", "dry_run": True},
            )
        return None

    def _mock_plan_payload(
        self,
        user: str,
        *,
        tool: str,
        agent: str,
        description: str,
        args: dict[str, Any],
        risk: str = "R0_READ_ONLY",
    ) -> dict[str, Any]:
        return {
            "goal": user or "mock task",
            "assumptions": [f"Generated by MockProvider for supervisor hint {agent}."],
            "steps": [
                {
                    "id": "step_1",
                    "agent_name": agent,
                    "tool_name": tool,
                    "description": description,
                    "args": args,
                    "expected_observation": "Structured observation recorded in the task timeline.",
                    "risk_level": risk,
                    "requires_approval": risk.startswith("R2") or risk.startswith("R3"),
                    "depends_on": [],
                    "rollback_strategy": "No changes during dry-run; modifying execution must return rollback_info.",
                }
            ],
        }

    def _natural_chat_reply(self, user: str) -> str:
        if not user:
            return "我在，直接说就行。"
        if "笨" in user or "卡" in user or "不自然" in user:
            return (
                "你说得对，这里应该像正常聊天一样先理解你，而不是一上来抛模板。"
                "后面我会先用主管 Agent 和你对话，判断真的需要执行时再调对应 Agent。"
            )
        return "我在，咱们可以正常聊。你直接说想法或问题；需要实际处理电脑、文件、网页或文档时，我会再安排对应 Agent。"
