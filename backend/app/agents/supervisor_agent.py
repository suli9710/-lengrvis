from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from app.llm.local_provider import LocalBackendUnavailable
from app.llm.prompts import load_prompt, render_prompt
from app.llm.registry import get_provider
from app.core.session_context import SessionContext, get_session_context_store
from app.perception.context_store import latest_perception_context
from app.perception.intent_predictor import IntentPredictor, IntentSuggestion
from app.perception.schemas import AppContext, ScreenState


SUPERVISOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["delegate", "reply"],
    "properties": {
        "delegate": {"type": "boolean"},
        "reply": {"type": "string"},
        "agent_hint": {
            "type": "string",
            "description": "One of ComputerAgent, FileAgent, BrowserAgent, SearchAgent, AppAgent, DocumentAgent, or empty.",
        },
    },
}

SUPERVISOR_TIMEOUT_SECONDS = 20

DELEGATION_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "ComputerAgent",
        ("电脑", "配置", "系统", "cpu", "memory", "内存", "磁盘", "进程", "启动项", "设置"),
        ("查", "看", "读取", "获取", "诊断", "检测", "列出"),
    ),
    (
        "FileAgent",
        ("文件", "文档", "目录", "文件夹", "重复", "发票", "合同", "素材", ".txt", ".pdf", ".docx"),
        ("查", "找", "搜索", "整理", "复制", "移动", "重命名", "删除", "删掉", "移除", "清理", "读取", "列出"),
    ),
    (
        "BrowserAgent",
        ("网页", "浏览器", "网址", "url", "页面", "链接"),
        ("打开", "读取", "截图", "提取", "登录", "访问"),
    ),
    (
        "SearchAgent",
        ("搜索", "查询", "最新", "新闻", "资料", "信息"),
        ("搜索", "查询", "查", "找"),
    ),
    (
        "AppAgent",
        ("应用", "软件", "程序", "app", "notepad", "记事本"),
        ("打开", "启动", "运行", "卸载", "移除", "删除", "uninstall", "remove"),
    ),
    (
        "DocumentAgent",
        ("pdf", "word", "docx", "pptx", "表格", "文档"),
        ("总结", "解析", "读取", "提取"),
    ),
)

CHAT_ONLY_HINTS = (
    "你好",
    "在吗",
    "谢谢",
    "你是谁",
    "怎么工作",
    "什么意思",
    "为什么",
    "然后呢",
    "继续",
    "正常聊天",
    "旅途",
)

FILE_ACTION_TERMS = (
    "查",
    "找",
    "搜索",
    "整理",
    "复制",
    "移动",
    "重命名",
    "删除",
    "删掉",
    "移除",
    "清理",
    "读取",
    "列出",
    "delete",
    "remove",
    "trash",
    "copy",
    "move",
    "rename",
)

APP_ACTION_TERMS = ("卸载", "uninstall")

WINDOWS_PATH_RE = re.compile(r"[a-zA-Z]:[\\/][^\r\n\"<>|?*]+")


@dataclass(frozen=True, slots=True)
class SupervisorDecision:
    delegate: bool
    reply: str
    agent_hint: str = ""


class SupervisorAgent:
    name = "SupervisorAgent"

    async def decide(self, message: str, mode: str) -> SupervisorDecision:
        heuristic = self.quick_decision(message)

        try:
            provider = get_provider()
            payload = await asyncio.wait_for(
                provider.structured_chat(self._supervisor_messages(message, mode), SUPERVISOR_SCHEMA),
                timeout=SUPERVISOR_TIMEOUT_SECONDS,
            )
            decision = self._payload_to_decision(payload)
        except LocalBackendUnavailable:
            return heuristic
        except Exception:
            return heuristic

        if not decision.reply:
            decision = SupervisorDecision(
                delegate=decision.delegate,
                reply=heuristic.reply,
                agent_hint=decision.agent_hint or heuristic.agent_hint,
            )

        if not decision.delegate:
            if heuristic.delegate:
                return SupervisorDecision(True, decision.reply or heuristic.reply, heuristic.agent_hint)
            return SupervisorDecision(False, decision.reply, "")

        if not heuristic.delegate:
            return SupervisorDecision(
                delegate=False,
                reply=decision.reply or self._chat_reply(message),
                agent_hint="",
            )

        agent_hint = decision.agent_hint if self._is_known_agent(decision.agent_hint) else heuristic.agent_hint
        if not agent_hint:
            agent_hint = heuristic.agent_hint
        return SupervisorDecision(True, decision.reply, agent_hint)

    def proactive_suggestions(
        self,
        *,
        screen_state: ScreenState | None = None,
        app_context: AppContext | None = None,
        history: SessionContext | dict[str, Any] | list[Any] | None = None,
        predictor: IntentPredictor | None = None,
    ) -> list[IntentSuggestion]:
        if screen_state is None or app_context is None:
            perception_context = latest_perception_context()
            screen_state = screen_state or _typed_context(perception_context.get("screen_state"), ScreenState)
            app_context = app_context or _typed_context(perception_context.get("app_context"), AppContext)
            if app_context is None and screen_state is not None:
                app_context = screen_state.app_context
        if history is None:
            try:
                history = get_session_context_store().load_latest()
            except Exception:
                history = None
        return (predictor or IntentPredictor()).predict(
            screen_state=screen_state,
            app_context=app_context,
            history=history,
        )

    def proactive_reply(self, suggestions: list[IntentSuggestion]) -> str:
        prompts = [item.prompt for item in suggestions if item.confidence > 0.8]
        if not prompts:
            return ""
        return "I can help with this next: " + " / ".join(prompts[:3])

    def quick_decision(self, message: str) -> SupervisorDecision:
        return self._heuristic_decision(message)

    def _supervisor_messages(self, message: str, mode: str) -> list[dict[str, str]]:
        perception_hint = self._format_perception_context(latest_perception_context())
        return [
            {
                "role": "system",
                "content": load_prompt("supervisor_agent.md"),
            },
            {
                "role": "user",
                "content": render_prompt("supervisor_user.md", {"mode": mode, "message": f"{perception_hint}{message}"}),
            },
        ]
        return decision

    def _format_perception_context(self, perception_context: dict[str, Any] | None) -> str:
        if not perception_context:
            return ""
        lines: list[str] = []
        screen_state = perception_context.get("screen_state")
        app_context = perception_context.get("app_context")
        if app_context is None and screen_state is not None:
            app_context = getattr(screen_state, "app_context", None)
        if app_context is not None:
            title = str(getattr(app_context, "active_window_title", "") or "").strip()
            process = str(getattr(app_context, "process_name", "") or "").strip()
            if title or process:
                lines.append(f"Active app: {process or 'unknown'} / {title or 'untitled'}")
        if screen_state is not None:
            description = str(getattr(screen_state, "description", "") or "").strip()
            if description:
                lines.append(f"Visible screen: {description[:200]}")
        if not lines:
            return ""
        return "[Perception context]\n" + "\n".join(lines) + "\n\n"

    def _payload_to_decision(self, payload: dict[str, Any]) -> SupervisorDecision:
        return SupervisorDecision(
            delegate=bool(payload.get("delegate")),
            reply=str(payload.get("reply") or "").strip(),
            agent_hint=str(payload.get("agent_hint") or "").strip(),
        )

    def _heuristic_decision(self, message: str) -> SupervisorDecision:
        text = message.strip()
        normalized = text.lower()
        if not text:
            return SupervisorDecision(False, "我在，直接告诉我你想做什么就行。")

        if any(hint in normalized for hint in CHAT_ONLY_HINTS):
            return SupervisorDecision(False, self._chat_reply(text))

        if WINDOWS_PATH_RE.search(text) and any(action in normalized for action in FILE_ACTION_TERMS):
            return SupervisorDecision(
                delegate=True,
                reply=self._delegation_reply("FileAgent", normalized),
                agent_hint="FileAgent",
            )

        if any(action in normalized for action in APP_ACTION_TERMS):
            return SupervisorDecision(
                delegate=True,
                reply=self._delegation_reply("AppAgent", normalized),
                agent_hint="AppAgent",
            )

        for agent, domains, actions in DELEGATION_RULES:
            if any(domain in normalized for domain in domains) and any(action in normalized for action in actions):
                return SupervisorDecision(
                    delegate=True,
                    reply=self._delegation_reply(agent, normalized),
                    agent_hint=agent,
                )

        return SupervisorDecision(False, self._chat_reply(text))

    def _chat_reply(self, message: str) -> str:
        if "agent" in message.lower() or "工作" in message:
            return (
                "对，这里应该先由主管 Agent 和你对话、理解意图、判断风险。"
                "只有当你的话需要实际读取电脑、查文件、开网页或执行动作时，我才会分配给对应 Agent。"
            )
        return "我在。你可以正常和我说话；需要动用电脑、文件、浏览器或搜索能力时，我会先说明再分配给对应 Agent。"

    def _zh_agent(self, agent: str) -> str:
        labels = {
            "ComputerAgent": "电脑 Agent",
            "FileAgent": "文件 Agent",
            "BrowserAgent": "浏览器 Agent",
            "SearchAgent": "搜索 Agent",
            "AppAgent": "应用 Agent",
            "DocumentAgent": "文档 Agent",
        }
        return labels.get(agent, agent)

    def _delegation_reply(self, agent: str, normalized_message: str) -> str:
        topics = {
            "ComputerAgent": "电脑/系统",
            "FileAgent": "文件",
            "BrowserAgent": "浏览器",
            "SearchAgent": "搜索",
            "AppAgent": "应用",
            "DocumentAgent": "文档",
        }
        reply = f"好的，这个任务和{topics.get(agent, '执行')}有关，我将分配给{self._zh_agent(agent)}。"
        if agent == "FileAgent" and any(term in normalized_message for term in ("删除", "删掉", "移除", "清理")):
            reply += "涉及删除或清理时，我会先走安全审核和审批，不会直接动你的文件。"
        if agent == "AppAgent" and any(term in normalized_message for term in ("卸载", "uninstall")):
            reply += "涉及卸载应用时，我会先查找对应卸载项并走安全审批。"
        return reply

    def _is_known_agent(self, agent: str) -> bool:
        return agent in {
            "ComputerAgent",
            "FileAgent",
            "BrowserAgent",
            "SearchAgent",
            "AppAgent",
            "DocumentAgent",
        }


def _typed_context(value: Any, expected_type: type[ScreenState] | type[AppContext]) -> Any:
    return value if isinstance(value, expected_type) else None


def coerce_supervisor_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise
