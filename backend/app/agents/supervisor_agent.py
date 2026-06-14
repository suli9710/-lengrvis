from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from app.agents.delegation_rules import (
    DELEGATION_RULES,
    FILE_ACTION_TERMS,
    UNINSTALL_TERMS as APP_ACTION_TERMS,
    WINDOWS_PATH_RE,
    contains_any,
)
from app.agents.worker_agents import KNOWN_SUPERVISOR_WORKER_AGENTS, normalize_supervisor_agent_hint
from app.llm.local_provider import LocalBackendUnavailable
from app.llm.prompts import load_prompt, render_prompt
from app.llm.registry import get_provider
from app.core.session_context import SessionContext, get_session_context_store
from app.perception.context_store import latest_perception_context
from app.perception.intent_predictor import IntentPredictor, IntentSuggestion
from app.perception.schemas import AppContext, ScreenState
from app.perception.storage import is_sensitive_context


SUPERVISOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["delegate", "reply"],
    "properties": {
        "delegate": {"type": "boolean"},
        "reply": {"type": "string"},
        "agent_hint": {
            "type": "string",
            "description": (
                "One of "
                + ", ".join(sorted(KNOWN_SUPERVISOR_WORKER_AGENTS))
                + ", or empty."
            ),
        },
    },
}

SUPERVISOR_TIMEOUT_SECONDS = 20

CHAT_ONLY_HINTS = (
    "你好",
    "聊天",
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

@dataclass(frozen=True, slots=True)
class SupervisorDecision:
    delegate: bool
    reply: str
    agent_hint: str = ""


class SupervisorAgent:
    name = "SupervisorAgent"

    async def decide(self, message: str, mode: str) -> SupervisorDecision:
        fallback = self.quick_decision(message)

        try:
            provider = get_provider()
            payload = await asyncio.wait_for(
                provider.structured_chat(self._supervisor_messages(message, mode), SUPERVISOR_SCHEMA),
                timeout=SUPERVISOR_TIMEOUT_SECONDS,
            )
            decision = self._payload_to_decision(payload)
        except LocalBackendUnavailable:
            return fallback
        except Exception:
            return fallback

        if not decision.reply:
            decision = SupervisorDecision(
                delegate=decision.delegate,
                reply=fallback.reply,
                agent_hint=decision.agent_hint or fallback.agent_hint,
            )

        if not decision.delegate:
            if self._is_unhelpful_chat_reply(decision.reply):
                return SupervisorDecision(False, fallback.reply or self._chat_reply(message), "")
            return SupervisorDecision(False, decision.reply, "")

        agent_hint = decision.agent_hint if self._is_known_agent(decision.agent_hint) else fallback.agent_hint
        if not agent_hint:
            agent_hint = fallback.agent_hint
        if not agent_hint:
            # The model wanted to delegate but produced no routable agent and the
            # heuristic has none either. Fall back to a plain chat reply rather
            # than echoing the model's delegation-flavored text, which would tell
            # the user work was assigned while no task is ever created.
            return SupervisorDecision(False, self._chat_reply(message), "")
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
        if is_sensitive_context(screen_state=screen_state, app_context=app_context):
            return []
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
        return "我可以接着帮你做：" + " / ".join(prompts[:3])

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

    def _format_perception_context(self, perception_context: dict[str, Any] | None) -> str:
        if not perception_context:
            return ""
        lines: list[str] = []
        screen_state = perception_context.get("screen_state")
        app_context = perception_context.get("app_context")
        if app_context is None and screen_state is not None:
            app_context = _context_value(screen_state, "app_context", None)
        if is_sensitive_context(screen_state=screen_state, app_context=app_context):
            return ""
        if app_context is not None:
            title = str(_context_value(app_context, "active_window_title") or "").strip()
            process = str(_context_value(app_context, "process_name") or "").strip()
            if title or process:
                lines.append(f"Active app: {process or 'unknown'} / {title or 'untitled'}")
        if screen_state is not None:
            description = str(_context_value(screen_state, "description") or "").strip()
            if description:
                lines.append(f"Visible screen: {description[:200]}")
        if not lines:
            return ""
        return "[Perception context]\n" + "\n".join(lines) + "\n\n"

    def _payload_to_decision(self, payload: dict[str, Any]) -> SupervisorDecision:
        reply = self._sanitize_reply(str(payload.get("reply") or "").strip())
        return SupervisorDecision(
            delegate=bool(payload.get("delegate")),
            reply=reply,
            agent_hint=str(payload.get("agent_hint") or "").strip(),
        )

    def _sanitize_reply(self, reply: str) -> str:
        normalized = re.sub(r"[\s，。；：,.!！?？]+", "", reply)
        mojibake_template = "ä¸»ç®¡Agentå·²æ¶å°"
        if "主管Agent已收到" in normalized or "确认意图" in normalized or mojibake_template in normalized:
            return self._chat_reply("")
        return reply

    def _is_unhelpful_chat_reply(self, reply: str) -> bool:
        normalized = re.sub(r"[\s，。；：,.!！?？]+", "", reply)
        return any(
            pattern in normalized
            for pattern in (
                "没看懂",
                "不太明白",
                "再具体说一下",
                "请具体说明",
                "请明确",
            )
        )

    def _heuristic_decision(self, message: str) -> SupervisorDecision:
        text = message.strip()
        normalized = text.lower()
        if not text:
            return SupervisorDecision(False, "我在，直接告诉我你想做什么就行。")

        if any(hint in normalized for hint in CHAT_ONLY_HINTS):
            return SupervisorDecision(False, self._chat_reply(text))

        if WINDOWS_PATH_RE.search(text) and contains_any(normalized, FILE_ACTION_TERMS):
            return SupervisorDecision(
                delegate=True,
                reply=self._delegation_reply("FileAgent", normalized),
                agent_hint="FileAgent",
            )

        if contains_any(normalized, APP_ACTION_TERMS):
            return SupervisorDecision(
                delegate=True,
                reply=self._delegation_reply("AppAgent", normalized),
                agent_hint="AppAgent",
            )

        if "清理" in normalized and any(domain in normalized for domain in ("文件", "目录", "文件夹", "盘", "磁盘")):
            return SupervisorDecision(
                delegate=True,
                reply="收到，我会先生成清理预览，不会直接删除文件；需要执行清理时会再请你审批。",
                agent_hint="FileAgent",
            )

        for agent, domains, actions in DELEGATION_RULES:
            if contains_any(normalized, domains) and contains_any(normalized, actions):
                return SupervisorDecision(
                    delegate=True,
                    reply=self._delegation_reply(agent, normalized),
                    agent_hint=agent,
                )

        return SupervisorDecision(False, self._chat_reply(text))

    def _chat_reply(self, message: str) -> str:
        normalized = message.lower()
        if "你会" in normalized or normalized in {"会啊", "会吗"}:
            return "会啊。我可以正常和你聊天，也可以在你明确要我做事时再调对应 Agent 去处理。"
        if "真人" in normalized:
            return "不是真人，我是 Lengrvis 里的主管 Agent。你可以把我当成一个先陪你自然对话、再按需要调度其他 Agent 的 AI 助手。"
        if "模型" in normalized or "ai" in normalized or "人工智能" in normalized:
            return "我是 Lengrvis 的主管 Agent，底层可以接不同模型。对你来说，我会先和你自然对话，需要实际操作时再调其他 Agent。"
        if "不是说" in normalized or "自然对话" in normalized or "对话" in normalized:
            return "对，这里应该先自然对话。我会先接住你的话，真的需要操作电脑、文件或网页时，再安排对应 Agent。"
        if "聊天" in normalized:
            return (
                "当然可以聊天。刚才把一些普通消息说得太像任务确认了，这个体验不对。"
                "你可以直接问我问题、闲聊，只有你明确要我操作电脑、文件、浏览器或应用时，我才会启动执行任务。"
            )
        if "你好" in normalized:
            return "你好，我在。你可以直接和我聊天，也可以告诉我需要处理的电脑、文件或应用任务。"
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
        return normalize_supervisor_agent_hint(agent) != ""


def _typed_context(value: Any, expected_type: type[ScreenState] | type[AppContext]) -> Any:
    return value if isinstance(value, expected_type) else None


def _context_value(value: Any, key: str, default: Any = "") -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def coerce_supervisor_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise
