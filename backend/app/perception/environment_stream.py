from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field

from app.core.schemas import new_id, now_iso
from app.indexer.file_watcher import DirectoryChangeWatcher, FileChangeCallback
from app.core.schemas import MessageType
from app.orchestration.agent_bus import GLOBAL_TASK_ID
from app.orchestration.dispatcher import EventDispatcher
from app.orchestration.events import Event
from app.perception.app_context import get_current_app_context
from app.perception.schemas import AppContext, PerceptionEvent, ScreenState
from app.perception.screen_monitor import ScreenMonitor, ScreenMonitorConfig

logger = logging.getLogger(__name__)


class EnvironmentEventType(StrEnum):
    SCREEN_CHANGED = "screen_changed"
    APP_SWITCHED = "app_switched"
    FILE_CHANGED = "file_changed"
    NETWORK_CHANGED = "network_changed"
    USB_CONNECTED = "usb_connected"
    SESSION_LOCKED = "session_locked"


class EnvironmentEvent(Event):
    event_type: str = "environment.event"
    task_id: str = GLOBAL_TASK_ID
    source_agent: str = "EnvironmentStream"
    environment_type: EnvironmentEventType
    subject: str = ""
    summary_text: str = ""
    app_context: AppContext | None = None
    screen_state: ScreenState | None = None
    path: str = ""
    action: str = ""
    status: str = ""
    device_name: str = ""
    locked: bool | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def summary(self) -> str:
        if self.summary_text:
            return self.summary_text
        detail = _event_detail(self) or (f" subject={self.subject}" if self.subject else "")
        return f"Environment event: {self.environment_type.value}{detail}"

    def environment_payload(self) -> dict[str, Any]:
        details = {
            **dict(self.details or {}),
            **({"path": self.path} if self.path else {}),
            **({"action": self.action} if self.action else {}),
            **({"status": self.status} if self.status else {}),
            **({"device_name": self.device_name} if self.device_name else {}),
            **({"locked": self.locked} if self.locked is not None else {}),
        }
        return {
            "id": self.id,
            "event_type": self.event_type,
            "environment_type": self.environment_type.value,
            "subject": self.subject,
            "summary": self.summary(),
            "details": details,
            "metadata": dict(self.metadata or {}),
            "timestamp": self.timestamp,
            "source_agent": self.source_agent,
        }


class ProactiveSuggestion(Event):
    event_type: str = "environment.proactive_suggestion"
    task_id: str = GLOBAL_TASK_ID
    source_agent: str = "EnvironmentStream"
    rule_id: str
    title: str = ""
    body: str = ""
    severity: str = "info"
    matched_event_ids: list[str] = Field(default_factory=list)
    matched_event_types: list[EnvironmentEventType] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)

    def summary(self) -> str:
        return self.body or self.title or f"Proactive suggestion: {self.rule_id}"


@dataclass(slots=True)
class EnvironmentRule:
    id: str = ""
    event_pattern: list[EnvironmentEventType] = field(default_factory=list)
    title: str = ""
    body: str = ""
    severity: str = "info"
    enabled: bool = True
    window_events: int = 25
    metadata_matches: dict[str, Any] = field(default_factory=dict)
    event_type: EnvironmentEventType | None = None
    subject_contains: str = ""
    suggestion: str = ""

    def __post_init__(self) -> None:
        if self.event_type is not None:
            self.event_type = EnvironmentEventType(self.event_type)
        self.event_pattern = [EnvironmentEventType(item) for item in self.event_pattern]
        if self.event_type is not None and not self.event_pattern:
            self.event_pattern = [self.event_type]
        if self.event_type is None and len(self.event_pattern) == 1:
            self.event_type = self.event_pattern[0]
        if self.suggestion and not self.body:
            self.body = self.suggestion
        if self.body and not self.suggestion:
            self.suggestion = self.body
        if not self.title:
            self.title = "Proactive suggestion"
        if not self.id:
            pattern = ".".join(item.value for item in self.event_pattern) or str(self.event_type or "environment")
            hint = self.subject_contains or self.suggestion or self.title
            self.id = f"{pattern}:{hint}".lower()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EnvironmentRule":
        event_pattern = raw.get("event_pattern") or raw.get("events") or []
        event_type = raw.get("event_type")
        return cls(
            id=str(raw.get("id") or ""),
            event_pattern=[EnvironmentEventType(item) for item in event_pattern],
            title=str(raw.get("title") or ""),
            body=str(raw.get("body") or raw.get("message") or ""),
            severity=str(raw.get("severity") or "info"),
            enabled=bool(raw.get("enabled", True)),
            window_events=max(1, int(raw.get("window_events") or 25)),
            metadata_matches=dict(raw.get("metadata_matches") or {}),
            event_type=EnvironmentEventType(event_type) if event_type else None,
            subject_contains=str(raw.get("subject_contains") or ""),
            suggestion=str(raw.get("suggestion") or ""),
        )

    def matches(self, event: EnvironmentEvent) -> bool:
        if not self.enabled:
            return False
        expected = self.event_type or (self.event_pattern[0] if len(self.event_pattern) == 1 else None)
        if expected is not None and event.environment_type != expected:
            return False
        if self.subject_contains and self.subject_contains.lower() not in event.subject.lower():
            return False
        return _metadata_matches(event, self.metadata_matches)


class EnvironmentRuleEngine:
    def __init__(
        self,
        rules: Iterable[EnvironmentRule | dict[str, Any]] | None = None,
        *,
        history_size: int = 100,
        task_id: str = GLOBAL_TASK_ID,
    ) -> None:
        self._rules = [_coerce_rule(rule) for rule in (rules if rules is not None else default_environment_rules())]
        self._history: deque[EnvironmentEvent] = deque(maxlen=max(1, history_size))
        self._triggered_signatures: set[tuple[str, tuple[str, ...]]] = set()
        self.task_id = task_id

    @property
    def rules(self) -> list[EnvironmentRule]:
        return list(self._rules)

    @property
    def history(self) -> list[EnvironmentEvent]:
        return list(self._history)

    def evaluate(self, event: EnvironmentEvent) -> list[ProactiveSuggestion]:
        self._history.append(event)
        suggestions: list[ProactiveSuggestion] = []
        for rule in self._rules:
            if not rule.enabled:
                continue
            matched = self._match(rule, event)
            if not matched:
                continue
            signature = (rule.id, tuple(item.id for item in matched))
            if signature in self._triggered_signatures:
                continue
            self._triggered_signatures.add(signature)
            suggestions.append(
                ProactiveSuggestion(
                    task_id=event.task_id or self.task_id,
                    rule_id=rule.id,
                    title=rule.title,
                    body=rule.suggestion or rule.body,
                    severity=rule.severity,
                    matched_event_ids=[item.id for item in matched],
                    matched_event_types=[item.environment_type for item in matched],
                    payload={
                        "kind": "proactive_suggestion",
                        "rule_id": rule.id,
                        "severity": rule.severity,
                        "suggestion": rule.suggestion or rule.body,
                        "matched_events": [item.model_dump() for item in matched],
                    },
                )
            )
        return suggestions

    def _match(self, rule: EnvironmentRule, event: EnvironmentEvent) -> list[EnvironmentEvent]:
        if len(rule.event_pattern) <= 1:
            return [event] if rule.matches(event) else []
        if not rule.event_pattern:
            return []
        search_space = list(self._history)[-rule.window_events :]
        matches: list[EnvironmentEvent] = []
        cursor = 0
        for event in search_space:
            if cursor >= len(rule.event_pattern):
                break
            if event.environment_type != rule.event_pattern[cursor]:
                continue
            if cursor == len(rule.event_pattern) - 1 and not rule.matches(event):
                continue
            matches.append(event)
            cursor += 1
        return matches if cursor == len(rule.event_pattern) else []


EnvironmentSink = Callable[[EnvironmentEvent], Any]


class EnvironmentStream:
    def __init__(
        self,
        *,
        dispatcher: EventDispatcher | None = None,
        bus: Any | None = None,
        rule_engine: EnvironmentRuleEngine | None = None,
        app_context_fn: Callable[[], AppContext] | None = None,
        screen_monitor: ScreenMonitor | None = None,
        file_source: DirectoryChangeWatcher | None = None,
        app_context_interval_seconds: float = 0.0,
        task_id: str = GLOBAL_TASK_ID,
    ) -> None:
        self.dispatcher = dispatcher or EventDispatcher(bus)
        self.bus = bus if bus is not None else getattr(self.dispatcher, "_bus", None)
        self.rule_engine = rule_engine or EnvironmentRuleEngine(task_id=task_id)
        self.app_context_fn = app_context_fn or get_current_app_context
        self.screen_monitor = screen_monitor
        self.file_source = file_source
        self.app_context_interval_seconds = max(0.0, float(app_context_interval_seconds or 0.0))
        self.task_id = task_id
        self._loop: asyncio.AbstractEventLoop | None = None
        self._app_context_task: asyncio.Task[None] | None = None
        self._started = False
        self._last_app_signature: tuple[str, str, int | None] | None = None
        self._lock = threading.RLock()
        self._sinks: list[EnvironmentSink] = []

    @property
    def started(self) -> bool:
        return self._started

    async def start(self, file_directories: Iterable[str | Path] | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        self._started = True
        if self.file_source is None and file_directories:
            self.file_source = DirectoryChangeWatcher(self.file_change_sink(), allowed_directories=[str(item) for item in file_directories])
        if self.file_source is not None and file_directories:
            self.file_source.start(list(file_directories))
        if self.screen_monitor is not None:
            self.screen_monitor.start()
        if self.app_context_interval_seconds > 0 and self._app_context_task is None:
            self._app_context_task = asyncio.create_task(
                self._poll_app_context_loop(),
                name="mavris-environment-app-context",
            )

    async def stop(self) -> None:
        self._started = False
        if self._app_context_task is not None:
            self._app_context_task.cancel()
            try:
                await self._app_context_task
            except asyncio.CancelledError:
                pass
            self._app_context_task = None
        if self.screen_monitor is not None:
            self.screen_monitor.stop()
        if self.file_source is not None:
            self.file_source.stop()
        self._loop = None

    async def _poll_app_context_loop(self) -> None:
        while self._started:
            try:
                await self.poll_app_context_once()
            except Exception:
                logger.exception("Failed to poll app context")
            await asyncio.sleep(max(0.25, self.app_context_interval_seconds))

    async def emit(self, event: EnvironmentEvent) -> list[ProactiveSuggestion]:
        if not event.task_id:
            event.task_id = self.task_id
        await self._notify_sinks(event)
        await self.dispatcher.dispatch(event)
        suggestions = self.rule_engine.evaluate(event)
        for suggestion in suggestions:
            await self.dispatcher.dispatch(suggestion)
            self._publish_suggestion(event, suggestion)
        return suggestions

    def subscribe(self, sink: EnvironmentSink) -> None:
        if sink not in self._sinks:
            self._sinks.append(sink)

    def unsubscribe(self, sink: EnvironmentSink) -> None:
        self._sinks = [item for item in self._sinks if item is not sink]

    async def _notify_sinks(self, event: EnvironmentEvent) -> None:
        for sink in list(self._sinks):
            try:
                result = sink(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("Environment stream sink failed for %s", event.environment_type)

    def submit(self, event: EnvironmentEvent) -> None:
        self._schedule(self.emit(event))

    def submit_perception_event(self, event: PerceptionEvent) -> None:
        self._schedule(self.handle_perception_event(event))

    def _schedule(self, coroutine: Any) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(lambda: asyncio.create_task(coroutine))
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coroutine)
        else:
            asyncio.create_task(coroutine)

    async def handle_perception_event(self, event: PerceptionEvent) -> list[ProactiveSuggestion]:
        state = event.screen_state
        suggestions = await self.emit(self.from_screen_state(state, task_id=event.task_id))
        if state.app_context is not None:
            app_event = self.from_app_context(state.app_context, task_id=event.task_id)
            if app_event is not None:
                suggestions.extend(await self.emit(app_event))
        return suggestions

    async def poll_app_context_once(self) -> EnvironmentEvent | None:
        event = self.from_app_context(self.app_context_fn(), task_id=self.task_id)
        if event is None:
            return None
        await self.emit(event)
        return event

    async def screen_changed(self, subject: str | ScreenState, **details: Any) -> EnvironmentEvent:
        if isinstance(subject, ScreenState):
            event = self.from_screen_state(subject, task_id=self.task_id)
        else:
            event = EnvironmentEvent(
                task_id=self.task_id,
                environment_type=EnvironmentEventType.SCREEN_CHANGED,
                subject=str(subject),
                summary_text=f"Screen changed: {subject}",
                details=dict(details),
                metadata=dict(details),
            )
        await self.emit(event)
        return event

    async def app_switched(
        self,
        process_name: str,
        active_window_title: str = "",
        **details: Any,
    ) -> EnvironmentEvent:
        context = AppContext(
            available=True,
            process_name=process_name,
            active_window_title=active_window_title,
            metadata=dict(details),
        )
        event = EnvironmentEvent(
            task_id=self.task_id,
            environment_type=EnvironmentEventType.APP_SWITCHED,
            subject=" ".join(part for part in [process_name, active_window_title] if part),
            app_context=context,
            summary_text=f"App switched: {' '.join(part for part in [process_name, active_window_title] if part)}",
            details={"process_name": process_name, "active_window_title": active_window_title, **dict(details)},
            metadata={"process_name": process_name, "active_window_title": active_window_title, **dict(details)},
        )
        await self.emit(event)
        return event

    async def file_changed(self, path: str | Path, action: str) -> EnvironmentEvent:
        event = file_changed_event(path, action, task_id=self.task_id)
        await self.emit(event)
        return event

    async def network_changed(self, status: str, **metadata: Any) -> EnvironmentEvent:
        event = system_environment_event(
            EnvironmentEventType.NETWORK_CHANGED,
            status=status,
            metadata=metadata,
            task_id=self.task_id,
        )
        await self.emit(event)
        return event

    async def usb_connected(self, device_name: str, **metadata: Any) -> EnvironmentEvent:
        event = system_environment_event(
            EnvironmentEventType.USB_CONNECTED,
            device_name=device_name,
            metadata=metadata,
            task_id=self.task_id,
        )
        await self.emit(event)
        return event

    async def session_locked(self, locked: bool = True, **metadata: Any) -> EnvironmentEvent:
        event = system_environment_event(
            EnvironmentEventType.SESSION_LOCKED,
            locked=locked,
            status="locked" if locked else "unlocked",
            metadata=metadata,
            task_id=self.task_id,
        )
        await self.emit(event)
        return event

    def from_screen_state(self, state: ScreenState, *, task_id: str | None = None) -> EnvironmentEvent:
        detail = state.description or state.id
        return EnvironmentEvent(
            task_id=task_id or self.task_id,
            environment_type=EnvironmentEventType.SCREEN_CHANGED,
            subject=detail,
            screen_state=state,
            app_context=state.app_context,
            summary_text=f"Screen changed: {detail[:120]}",
            details={
                "screen_state_id": state.id,
                "tags": list(state.tags),
                "structured_labels": dict(state.structured_labels),
            },
            metadata={
                "screen_state_id": state.id,
                "tags": list(state.tags),
                "structured_labels": dict(state.structured_labels),
            },
        )

    def from_app_context(self, context: AppContext, *, task_id: str | None = None) -> EnvironmentEvent | None:
        if not (context.available or context.active_window_title or context.process_name):
            return None
        signature = (context.active_window_title, context.process_name, context.process_id)
        with self._lock:
            if signature == self._last_app_signature:
                return None
            self._last_app_signature = signature
        return EnvironmentEvent(
            task_id=task_id or self.task_id,
            environment_type=EnvironmentEventType.APP_SWITCHED,
            subject=" ".join(part for part in [context.process_name, context.active_window_title] if part),
            app_context=context,
            summary_text=f"App switched: {context.process_name or 'unknown'} {context.active_window_title}".strip(),
            details={
                "active_window_title": context.active_window_title,
                "process_name": context.process_name,
                "process_id": context.process_id,
            },
            metadata={
                "active_window_title": context.active_window_title,
                "process_name": context.process_name,
                "process_id": context.process_id,
                **dict(context.metadata or {}),
            },
        )

    def _publish_suggestion(self, event: EnvironmentEvent, suggestion: ProactiveSuggestion) -> None:
        if self.bus is None:
            return
        payload = {
            "event_type": suggestion.event_type,
            "rule_id": suggestion.rule_id,
            "title": suggestion.title,
            "suggestion": suggestion.body or suggestion.title,
            "severity": suggestion.severity,
            "environment_event": event.environment_payload(),
            "matched_event_ids": list(suggestion.matched_event_ids),
            "matched_event_types": [item.value for item in suggestion.matched_event_types],
        }
        try:
            if hasattr(self.bus, "publish_cross_task"):
                self.bus.publish_cross_task(
                    suggestion.source_agent,
                    suggestion.body or suggestion.title,
                    event_type=suggestion.event_type,
                    message_type=MessageType.NOTIFICATION,
                    structured_payload=payload,
                )
            else:
                self.bus.publish_text(
                    suggestion.task_id or self.task_id,
                    suggestion.source_agent,
                    suggestion.body or suggestion.title,
                    message_type=MessageType.NOTIFICATION,
                    structured_payload=payload,
                )
        except Exception:
            logger.exception("Failed to publish proactive suggestion %s", suggestion.rule_id)

    def file_change_sink(self) -> FileChangeCallback:
        def _sink(path: str, action: str) -> None:
            self.submit(file_changed_event(path, action, task_id=self.task_id))

        return _sink

    def dispatch_system_event(
        self,
        environment_type: EnvironmentEventType | str,
        *,
        status: str = "",
        device_name: str = "",
        locked: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.submit(
            system_environment_event(
                environment_type,
                status=status,
                device_name=device_name,
                locked=locked,
                metadata=metadata,
                task_id=self.task_id,
            )
        )

def file_changed_event(path: str | Path, action: str, *, task_id: str = GLOBAL_TASK_ID) -> EnvironmentEvent:
    raw_path = str(path)
    normalized_action = str(action or "changed")
    return EnvironmentEvent(
        task_id=task_id,
        environment_type=EnvironmentEventType.FILE_CHANGED,
        subject=raw_path,
        path=raw_path,
        action=normalized_action,
        summary_text=f"File changed: {raw_path}",
        details={"path": raw_path, "action": normalized_action},
        metadata={"path": raw_path, "action": normalized_action},
    )


def system_environment_event(
    environment_type: EnvironmentEventType | str,
    *,
    status: str = "",
    device_name: str = "",
    locked: bool | None = None,
    metadata: dict[str, Any] | None = None,
    task_id: str = GLOBAL_TASK_ID,
) -> EnvironmentEvent:
    event_type = EnvironmentEventType(environment_type)
    body = dict(metadata or {})
    return EnvironmentEvent(
        task_id=task_id,
        environment_type=event_type,
        subject=status or device_name or event_type.value,
        status=status,
        device_name=device_name,
        locked=locked,
        summary_text=f"{event_type.value}: {status or device_name}".rstrip(": "),
        details={
            **body,
            **({"status": status} if status else {}),
            **({"device_name": device_name} if device_name else {}),
            **({"locked": locked} if locked is not None else {}),
        },
        metadata=body,
    )


def default_environment_rules() -> list[EnvironmentRule]:
    return [
        EnvironmentRule(
            id="resume_after_app_switch",
            event_pattern=[EnvironmentEventType.APP_SWITCHED],
            title="Context changed",
            body="Active app changed; review the visible context before continuing automation.",
            severity="info",
        ),
        EnvironmentRule(
            id="file_change_after_app_switch",
            event_pattern=[EnvironmentEventType.APP_SWITCHED, EnvironmentEventType.FILE_CHANGED],
            title="File changed in active workflow",
            body="A file changed shortly after an app switch; consider refreshing context before planning the next step.",
            severity="info",
        ),
        EnvironmentRule(
            id="session_locked_pause",
            event_pattern=[EnvironmentEventType.SESSION_LOCKED],
            title="Session locked",
            body="The desktop session appears locked; pause user-visible automation until the session is available.",
            severity="warning",
        ),
        EnvironmentRule(
            id="network_change_review",
            event_pattern=[EnvironmentEventType.NETWORK_CHANGED],
            title="Network changed",
            body="Network status changed; verify cloud or browser-dependent steps before continuing.",
            severity="info",
        ),
        EnvironmentRule(
            id="usb_connected_review",
            event_pattern=[EnvironmentEventType.USB_CONNECTED],
            title="USB device connected",
            body="A USB device was connected; confirm whether any new files or drives should be included.",
            severity="info",
        ),
    ]


def build_environment_stream(
    *,
    dispatcher: EventDispatcher | None = None,
    bus: Any | None = None,
    settings: Any | None = None,
    rules: Iterable[EnvironmentRule | dict[str, Any]] | None = None,
    task_id: str = GLOBAL_TASK_ID,
) -> EnvironmentStream:
    effective_rules = rules if rules is not None else _rules_from_settings(settings)
    stream = EnvironmentStream(
        dispatcher=dispatcher,
        bus=bus,
        rule_engine=EnvironmentRuleEngine(effective_rules, task_id=task_id),
        app_context_interval_seconds=_app_context_interval_from_settings(settings),
        task_id=task_id,
    )
    if settings is not None:
        stream.screen_monitor = _screen_monitor_from_settings(settings, stream.submit_perception_event)
    stream.dispatcher.register("perception.screen_state", stream.handle_perception_event)
    return stream


_instance: EnvironmentStream | None = None


def get_environment_stream(
    *,
    dispatcher: EventDispatcher | None = None,
    bus: Any | None = None,
    settings: Any | None = None,
    rules: Iterable[EnvironmentRule | dict[str, Any]] | None = None,
    reset: bool = False,
) -> EnvironmentStream:
    global _instance
    if reset or _instance is None:
        _instance = build_environment_stream(dispatcher=dispatcher, bus=bus, settings=settings, rules=rules)
    return _instance


def load_rules_from_env(env_var: str = "MAVRIS_ENVIRONMENT_RULES") -> list[EnvironmentRule]:
    raw = os.getenv(env_var) or os.getenv("MARVIS_ENVIRONMENT_RULES") or ""
    if not raw.strip():
        return []
    text = raw
    path = Path(raw).expanduser()
    if path.exists():
        text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, list):
        return []
    return [EnvironmentRule.from_dict(item) for item in data if isinstance(item, dict)]


def reset_environment_stream() -> None:
    global _instance
    _instance = None


def _coerce_rule(rule: EnvironmentRule | dict[str, Any]) -> EnvironmentRule:
    if isinstance(rule, EnvironmentRule):
        return rule
    return EnvironmentRule.from_dict(rule)


def _rules_from_settings(settings: Any | None) -> list[EnvironmentRule]:
    raw_rules = getattr(settings, "environment_rules", None) if settings is not None else None
    if not raw_rules:
        env_rules = load_rules_from_env()
        return env_rules or default_environment_rules()
    try:
        return [EnvironmentRule.from_dict(raw) if isinstance(raw, dict) else raw for raw in raw_rules]
    except Exception:
        logger.exception("Invalid environment rules in settings; using defaults")
        return default_environment_rules()


def _screen_monitor_from_settings(settings: Any, event_publisher: Callable[[PerceptionEvent], Any]) -> ScreenMonitor | None:
    config = ScreenMonitorConfig.from_settings(settings)
    if not config.enabled:
        return None
    config.publish_events = True
    monitor = ScreenMonitor(config, event_publisher=event_publisher)
    return monitor


def _app_context_interval_from_settings(settings: Any | None) -> float:
    if settings is None:
        return 0.0
    return float(getattr(settings, "environment_app_context_interval_seconds", 2.0) or 0.0)


def _metadata_matches(event: EnvironmentEvent, expected: dict[str, Any]) -> bool:
    if not expected:
        return True
    data = {
        **dict(event.metadata or {}),
        "path": event.path,
        "action": event.action,
        "status": event.status,
        "device_name": event.device_name,
        "locked": event.locked,
    }
    for key, value in expected.items():
        if data.get(key) != value:
            return False
    return True


def _event_detail(event: EnvironmentEvent) -> str:
    if event.path:
        return f" path={event.path}"
    if event.device_name:
        return f" device={event.device_name}"
    if event.status:
        return f" status={event.status}"
    if event.app_context is not None and event.app_context.active_window_title:
        return f" app={event.app_context.active_window_title}"
    return ""
