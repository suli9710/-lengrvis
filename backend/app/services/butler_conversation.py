"""Direct user conversation, preferences and matter revisions."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from app.core import butler_store as store
from app.core import db
from app.core.audit import record
from app.core.butler_models import (
    TERMINAL_MATTER_STATUSES,
    ButlerMatter,
    ButlerMessage,
    MatterInteraction,
    MatterResponse,
    MatterStatus,
)
from app.core.context_egress import planner_context_allowed
from app.core.local_data_lifecycle import tracks_local_data
from app.core.schemas import ChatMessage, OpenAIMessageRole, now_iso
from app.services.butler_coordination import update_arrangement
from app.services.butler_scopes import enabled_scopes
from app.services.butler_state import (
    _credential_answers,
    _parse_time,
    public_matter,
)

_REVISION = re.compile(
    r"^(?:改成|改为|换成|换到|改一下|把刚才|把之前|接着|继续刚才|再加|补充|其实|change |instead |make (?:it|that) )",
    re.I,
)
_NEW_REQUEST = re.compile(
    r"^(?:另外|另一个任务|新任务|还有一件|顺便|先帮我|先处理|another task|new task)\s*[:：]?", re.I
)
_REQUEST_VERB = re.compile(
    r"^(?:帮我|请|整理|打开|将|把|创建|生成|查找|搜索|读取|处理|订|send\b|create\b|open\b)", re.I
)
_RESERVED_ANSWERS = frozenset(
    {"verification_feedback", "completed_execution_evidence", "user_replies", "resolved_wait", "current_local_time"}
)
_REMEMBER = re.compile(r"^(?:记住(?:我的偏好)?\s*[:：]|remember my preference\s*:)\s*(.+)$", re.I | re.S)


class ButlerConversation:
    @tracks_local_data
    async def message(self, request: ButlerMessage) -> dict:
        from app.policy.sensitive_values import BLOCKED_REDACTION_PATTERNS

        if any(pattern.search(request.message) for pattern in BLOCKED_REDACTION_PATTERNS):
            raise ValueError("请通过受保护的设置或认证界面提供秘密信息。")
        db.init_db()
        user = ChatMessage(role=OpenAIMessageRole.USER, author="你", content=request.message)
        db.upsert_model("chat_messages", user)
        result = await self._handle_message(request)
        assistant = ChatMessage(role=OpenAIMessageRole.ASSISTANT, author="Lengrvis", content=result["reply"])
        db.upsert_model("chat_messages", assistant)
        return {
            **result,
            "user_message": user.model_dump(mode="json"),
            "assistant_message": assistant.model_dump(mode="json"),
        }

    async def _handle_message(self, request: ButlerMessage) -> dict:
        text = request.message.strip()
        if text in {"你好", "您好", "嗨", "谢谢", "谢谢你"}:
            return {"reply": "我在。你可以交代一件事，也可以查看管家正在处理的事项。"}
        if text in {"你在做什么", "你在忙什么", "还有什么没完成", "查看管家进度"}:
            active = [
                item
                for item in store.list_records(ButlerMatter, limit=None)
                if item.status not in TERMINAL_MATTER_STATUSES
            ]
            return {
                "reply": "当前没有待处理事项。"
                if not active
                else "\n".join(f"{item.goal[:100]}：{item.progress}" for item in active[:10])
            }
        from app.policy.sensitive_values import BLOCKED_REDACTION_PATTERNS

        if any(pattern.search(text) for pattern in BLOCKED_REDACTION_PATTERNS):
            raise ValueError("请通过受保护的设置或认证界面提供秘密信息。")
        control = {"暂停管家": True, "停止主动管理": True, "恢复管家": False, "继续主动管理": False}
        if text in control:
            state = await self.pause(control[text])
            return {"reply": "管家已暂停。" if control[text] else "管家已恢复。", **state}
        if text in {"关闭自动记忆", "停止自动记忆", "开启自动记忆", "恢复自动记忆"}:
            enabled = text in {"开启自动记忆", "恢复自动记忆"}
            state = store.set_control(auto_memory_enabled=enabled)
            return {
                "reply": "已开启自动记忆重要信息。" if enabled else "已关闭自动记忆，已有记忆仍可查看或删除。",
                **state,
            }
        preference = _REMEMBER.fullmatch(text)
        if preference and not request.matter_id:
            return await self.remember_preference(preference.group(1), request.mode)
        if store.control_state()["coordination_enabled"]:
            linked = self._dependency_message(request)
            if linked is not None:
                return linked
        active = [
            item
            for item in store.list_records(ButlerMatter, limit=None)
            if item.status not in {MatterStatus.CANCELLED, MatterStatus.FAILED}
        ]
        target = store.get(ButlerMatter, request.matter_id) if request.matter_id else None
        if request.matter_id and target is None:
            raise KeyError(request.matter_id)
        if _NEW_REQUEST.search(text):
            target = None
        is_control = text in {"暂停", "继续", "恢复", "取消", "停止"}
        waiting = [
            item
            for item in active
            if item.status == MatterStatus.WAITING_INPUT
            and item.interaction
            and item.interaction.kind not in {"choose_matter", "choose_dependency"}
        ]
        is_answer = bool(waiting and not _NEW_REQUEST.search(text) and not _REQUEST_VERB.search(text))
        if target is None and (_REVISION.search(text) or is_control or is_answer):
            candidates = (
                waiting
                if is_answer and not (_REVISION.search(text) or is_control)
                else [item for item in active if item.status != MatterStatus.COMPLETED]
            )
            if not candidates:
                candidates = active[:5]
            if len(candidates) == 1:
                target = candidates[0]
            elif candidates:
                interaction = MatterInteraction(
                    kind="choose_matter",
                    message="这句话要应用到哪件事？",
                    candidates=[item.id for item in candidates[:20]],
                )
                matter = store.save(
                    ButlerMatter(
                        goal=text,
                        mode=request.mode,
                        status=MatterStatus.WAITING_INPUT,
                        interaction=interaction,
                        progress=interaction.message,
                    )
                )
                return {"matter": public_matter(matter), "reply": interaction.message}
        if target:
            if target.interaction and target.interaction.kind != "approval" and not is_control:
                return await self.respond(
                    target.id,
                    MatterResponse(version=target.version, interaction_id=target.interaction.id, message=text),
                )
            if is_control:
                action = "cancel" if text in {"取消", "停止"} else "resume" if text in {"继续", "恢复"} else "pause"
                matter = await self.control_matter(target.id, action)
                return {"matter": public_matter(matter), "reply": matter.progress}
            matter = await self.revise(target, text)
        else:
            matter = store.save(
                ButlerMatter(
                    goal=text,
                    mode=request.mode,
                    scope_versions={s.id: s.version for s in enabled_scopes()},
                    memory_inputs=[text] if store.control_state()["auto_memory_enabled"] else [],
                )
            )
        record("butler.matter_requested", "user", {"matter_id": matter.id, "version": matter.version})
        self._spawn(matter.id)
        return {"matter": public_matter(matter), "reply": "这件事已加入管家，进度会持续更新。"}

    def _dependency_message(self, request: ButlerMessage) -> dict | None:
        match = re.fullmatch(
            r"(?:请|帮我)?(?:等)?(?:完成|做完)(.+?)后[，,\s]*(?:再)?(.+)", request.message.strip(), re.S
        )
        if not match:
            return None
        parent_goal, child_goal = (value.strip(" \t\n“”\"'。") for value in match.groups())
        if not parent_goal or not child_goal:
            raise ValueError("请说明前置事项和后续事项。")
        records = store.list_records(ButlerMatter, limit=None)

        def matches(goal):
            exact = [item for item in records if item.goal == goal or item.id == goal]
            return exact or [
                item for item in records if goal in item.goal and item.status not in TERMINAL_MATTER_STATUSES
            ]

        parents, children = matches(parent_goal), matches(child_goal)
        if len(parents) > 1 or len(children) > 1:
            options = {item.id: item.version for item in [*parents, *children]}
            matter = store.save(
                ButlerMatter(
                    goal=request.message,
                    mode=request.mode,
                    status=MatterStatus.WAITING_INPUT,
                    interaction=MatterInteraction(
                        kind="choose_dependency",
                        message="请一次选定前置事项和后续事项；未匹配的事项将按原话新建。",
                        candidates=list(options)[:20],
                    ),
                    constraints={
                        "coordination_request": {
                            "parent_goal": parent_goal,
                            "child_goal": child_goal,
                            "parents": [item.id for item in parents],
                            "children": [item.id for item in children],
                            "versions": options,
                        }
                    },
                )
            )
            return {"matter": public_matter(matter), "reply": matter.interaction.message}
        child = self._link_request(
            parent_goal, child_goal, request.mode, parents[0] if parents else None, children[0] if children else None
        )
        return {"matter": public_matter(child), "reply": "已安排：前置事项完成核验后，再处理这件事。"}

    def _link_request(self, parent_goal, child_goal, mode, parent, child):
        from app.core.butler_models import MatterArrangement

        if child and (store.execution_started(child) or child.status in TERMINAL_MATTER_STATUSES):
            raise store.ButlerConflict("后续事项已开始或已结束，不能追加前置关系。")
        with db.connect() as conn:
            db._begin_immediate_transaction(conn)
            scopes = {scope.id: scope.version for scope in enabled_scopes()}
            remember = store.control_state()["auto_memory_enabled"]
            parent = parent or store.save(
                ButlerMatter(
                    goal=parent_goal,
                    mode=mode,
                    scope_versions=scopes,
                    memory_inputs=[parent_goal] if remember else [],
                )
            )
            if child:
                child = update_arrangement(
                    child.id, MatterArrangement(version=child.version, depends_on=[*child.depends_on, parent.id])
                )
            else:
                child = store.save(
                    ButlerMatter(
                        goal=child_goal,
                        mode=mode,
                        scope_versions=scopes,
                        depends_on=[parent.id],
                        memory_inputs=[child_goal] if remember else [],
                    )
                )
        self._spawn(parent.id)
        self._spawn(child.id)
        return child

    async def remember_preference(self, text: str, mode: str) -> dict:
        from app.agents.memory_agent import MemoryAgent
        from app.core.session_context import get_session_context_store
        from app.policy.sensitive_values import looks_sensitive_value

        # This path is reached only by a direct, explicit user memory request.
        # Source events and model decisions never call it.
        if looks_sensitive_value(text):
            raise ValueError("请通过设置管理秘密信息，不要将密钥或密码记为偏好。")
        matter = store.save(ButlerMatter(goal="记住偏好：" + text, mode=mode))
        memory = await MemoryAgent().remember(
            text,
            task_id=matter.id,
            mode=matter.mode,
            kind="preference",
            source="user",
            user_confirmed=True,
            tags=["butler", "preference"],
        )
        get_session_context_store().learn_preference("butler:" + memory.id, {"memory_id": memory.id})

        def saved(item: ButlerMatter) -> None:
            item.status = MatterStatus.COMPLETED
            item.progress = "已记住这项偏好；实际操作仍遵循授权范围。"
            item.completion_evidence = [{"kind": "memory", "memory_id": memory.id, "verified_at": now_iso()}]

        matter = store.mutate(ButlerMatter, matter.id, saved, expected_version=matter.version)
        return {"matter": public_matter(matter), "reply": matter.progress}

    async def preference_context(self, matter: ButlerMatter) -> dict:
        from app.agents.memory_agent import MemoryAgent
        from app.core.session_context import get_session_context_store

        agent = MemoryAgent()
        memories = await agent.recall(matter.goal, k=12, mode=matter.mode)
        confirmed = [
            {"memory_id": item.id, "content": item.content, "source": item.source}
            for item in memories
            if item.user_confirmed and item.kind == "preference"
        ]
        facts = [
            {"memory_id": item.id, "content": item.content, "source": item.source}
            for item in memories
            if item.user_confirmed and item.kind == "fact"
        ]
        # Inferences need source and expiry; they are never permissions.
        # Pointer-only caches cannot outlive a
        # memory revoke/delete because every confirmed value comes from recall.
        candidates = {}
        for key, value in get_session_context_store().current.learned_preferences.items():
            if key.startswith("butler:") or not isinstance(value, dict) or "memory_id" in value:
                continue
            expires = _parse_time(value.get("expires_at", ""))
            if (
                planner_context_allowed(
                    str(value.get("source_mode") or ""),
                    matter.mode,
                    cloud_allowed=value.get("allow_cloud_context") is True,
                )
                and value.get("source")
                and _parse_time(value.get("observed_at", ""))
                and expires
                and expires > datetime.now(UTC)
            ):
                candidates[key] = {
                    "value": value.get("value"),
                    "source": value["source"],
                    "observed_at": value["observed_at"],
                    "expires_at": value["expires_at"],
                    "confirmed": False,
                }
        return {"confirmed": confirmed, "facts": facts, "candidates": candidates}

    async def revise(self, matter: ButlerMatter, message: str) -> ButlerMatter:
        await self._pause_run(matter)

        def change(item: ButlerMatter) -> None:
            item.outcome_revision += 1
            item.outcome_revoked = False
            item.goal += "\n用户更新：" + message
            if store.control_state()["auto_memory_enabled"]:
                item.memory_inputs.append(message)
            item.execution_ticket = ""
            item.active_run_id = ""
            item.active_task_id = ""
            item.status = MatterStatus.QUEUED
            item.interaction = None
            item.waiting = None
            item.completion_conditions = []
            item.no_progress_count = 0
            item.continuation_count = 0
            item.last_progress_key = ""
            item.progress = "已保存新要求，准备接续处理"

        return store.mutate(ButlerMatter, matter.id, change, expected_version=matter.version)

    async def respond(self, matter_id: str, response: MatterResponse) -> dict:
        from app.policy.sensitive_values import BLOCKED_REDACTION_PATTERNS

        content = json.dumps(response.answers, ensure_ascii=False) + "\n" + response.message
        if _credential_answers(response.answers) or any(
            pattern.search(content) for pattern in BLOCKED_REDACTION_PATTERNS
        ):
            raise ValueError("请在受保护的设置或原应用中输入密钥、密码和认证信息。")
        current = store.get(ButlerMatter, matter_id)
        if current is None:
            raise KeyError(matter_id)
        if current.status in TERMINAL_MATTER_STATUSES:
            raise store.ButlerConflict("事项已结束，不能再提交补充信息。")
        if (
            current.version != response.version
            or not current.interaction
            or current.interaction.id != response.interaction_id
        ):
            raise store.ButlerConflict("此回答已过期，请查看当前需要处理的信息。")
        if current.interaction.kind == "approval":
            raise ValueError("请使用具体操作的审批入口作出决定。")
        if current.interaction.kind == "choose_dependency":
            pending = current.constraints["coordination_request"]
            selected = []
            for role, key in (("parent", "parents"), ("child", "children")):
                choices = pending[key]
                identity = str(response.answers.get(role) or (choices[0] if len(choices) == 1 else ""))
                if choices and identity not in choices:
                    raise ValueError("请从候选列表选择前置事项和后续事项。")
                item = store.get(ButlerMatter, identity) if identity else None
                if identity and (item is None or item.version != pending["versions"][identity]):
                    raise store.ButlerConflict("候选事项已变化，请重新提出关联请求。")
                selected.append(item)
            child = self._link_request(pending["parent_goal"], pending["child_goal"], current.mode, *selected)
            store.mutate(
                ButlerMatter,
                current.id,
                lambda item: (setattr(item, "status", MatterStatus.CANCELLED), setattr(item, "interaction", None)),
                expected_version=current.version,
            )
            return {"matter": public_matter(child), "reply": "已保存前置关系，完成核验后自动接续。"}
        if current.interaction.kind == "choose_matter":
            target_id = str(response.answers.get("matter_id") or "")
            if target_id not in current.interaction.candidates:
                raise ValueError("请选择列表中的事项。")
            store.mutate(
                ButlerMatter,
                current.id,
                lambda item: setattr(item, "status", MatterStatus.CANCELLED),
                expected_version=current.version,
            )
            # Routing an earlier utterance is not a new chat turn.
            return await self._handle_message(
                ButlerMessage(message=current.goal, mode=current.mode, matter_id=target_id)
            )

        def update(item: ButlerMatter) -> None:
            allowed = set(item.interaction.fields)
            if set(response.answers) & _RESERVED_ANSWERS:
                raise ValueError("回答不能替代运行时核验记录。")
            if allowed and set(response.answers) - allowed:
                raise ValueError("回答包含未请求的信息项。")
            if item.interaction.kind == "reconcile" and item.active_run_id:
                from app.services import run_service

                if str(run_service.get_run(item.active_run_id).phase) in {"failed", "denied", "cancelled", "completed"}:
                    item.active_run_id = ""
                    item.active_task_id = ""
                    item.execution_ticket = ""
            item.answers.update(response.answers)
            if response.message:
                item.answers.setdefault("user_replies", []).append(response.message)
            if store.control_state()["auto_memory_enabled"]:
                if response.message:
                    item.memory_inputs.append(response.message)
                if response.answers:
                    item.memory_inputs.append(json.dumps(response.answers, ensure_ascii=False))
            item.interaction = None
            item.waiting = None
            item.status = MatterStatus.QUEUED
            item.progress = "已收到补充信息，正在接续"

        matter = store.mutate(ButlerMatter, matter_id, update, expected_version=response.version)
        self._spawn(matter.id)
        return {"matter": public_matter(matter), "reply": matter.progress}
