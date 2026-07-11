from __future__ import annotations

import asyncio
import hashlib
from contextlib import suppress
from pathlib import Path
from typing import Any

from app.automation import run_budget, store
from app.automation.models import (
    AutomationRun,
    AutomationRunItem,
    AutomationStatus,
    AutomationTrigger,
    AutomationTriggerEvent,
    TriggerEventStatus,
)
from app.core import audit
from app.core.content_provenance import create_content_envelope_from_hash, stable_content_hash
from app.core.errors import SecurityError
from app.core.paths import resolve_authorized
from app.core.schemas import now_iso
from app.services import notification_service

_MAX_TRIGGER_ATTEMPTS = 3
_HASH_CHUNK_BYTES = 1024 * 1024


class AutomationFileTriggerService:
    """Turns stable file changes into idempotent draft automation runs.

    A trigger never authorizes execution. It only creates an inbox item and a
    default budget; ToolRuntime still requires a short-lived IntentCapsule.
    """

    def __init__(self, *, allowed_directories: list[str] | None = None) -> None:
        self.allowed_directories = list(allowed_directories or [])
        self._watcher: Any | None = None
        self._tasks: dict[tuple[str, str], asyncio.Task[Any]] = {}

    async def start(self, watcher: Any) -> None:
        if self._watcher is watcher:
            return
        self._watcher = watcher
        watcher.subscribe_changes(self.handle_change)
        await self.recover_pending()

    async def stop(self) -> None:
        watcher = self._watcher
        self._watcher = None
        if watcher is not None:
            watcher.unsubscribe_changes(self.handle_change)
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def handle_change(self, path: str, action: str) -> None:
        if str(action or "").lower() not in {"upsert", "created", "modified", "moved"}:
            return
        for trigger in store.list_triggers(limit=500):
            if not _trigger_matches(trigger, path, action):
                continue
            key = (trigger.id, _normalized_path(path).casefold())
            previous = self._tasks.pop(key, None)
            if previous is not None:
                previous.cancel()
            task = asyncio.create_task(
                self._process_trigger(trigger, path),
                name=f"automation-file-trigger:{trigger.id}",
            )
            self._tasks[key] = task
            task.add_done_callback(lambda completed, task_key=key: self._remove_completed(task_key, completed))

    async def process_change(self, path: str, action: str = "upsert") -> list[AutomationTriggerEvent]:
        """Synchronous test/admin entrypoint that waits for matching triggers."""

        results: list[AutomationTriggerEvent] = []
        for trigger in store.list_triggers(limit=500):
            if _trigger_matches(trigger, path, action):
                event = await self._process_trigger(trigger, path)
                if event is not None:
                    results.append(event)
        return results

    async def recover_pending(self) -> list[str]:
        recovered: list[str] = []
        events = store.list_trigger_events(statuses={TriggerEventStatus.OBSERVED}, limit=1000)
        for event in events:
            if not Path(event.path).is_file():
                store.update_trigger_event(
                    event.id,
                    status=TriggerEventStatus.FAILED,
                    last_error_code="source_missing",
                    increment_attempts=True,
                )
                continue
            try:
                run = self._create_run_for_event(event)
            except (KeyError, OSError, ValueError, RuntimeError) as exc:
                store.update_trigger_event(
                    event.id,
                    status=TriggerEventStatus.FAILED,
                    last_error_code="recovery_failed",
                    increment_attempts=True,
                )
                self._audit_failure_for_event(event, "recovery_failed", exc)
                continue
            if run is not None:
                recovered.append(run.id)
        if recovered:
            audit.record(
                "automation.file_trigger.recovered",
                "AutomationFileTriggerService",
                {"run_ids": recovered, "count": len(recovered)},
            )
        return recovered

    async def _process_trigger(
        self,
        trigger: AutomationTrigger,
        raw_path: str,
    ) -> AutomationTriggerEvent | None:
        path = _normalized_path(raw_path)
        if self.allowed_directories:
            try:
                path = str(resolve_authorized(path, self.allowed_directories))
            except (SecurityError, OSError, ValueError) as exc:
                self._audit_failure(trigger, path, "path_not_authorized", exc)
                return None
        last_event: AutomationTriggerEvent | None = None
        for attempt in range(1, _MAX_TRIGGER_ATTEMPTS + 1):
            try:
                await _wait_for_stable_file(Path(path), stable_seconds=trigger.stable_seconds)
                content_hash = await asyncio.to_thread(_file_sha256, Path(path))
                event_key = stable_content_hash(
                    {"trigger_id": trigger.id, "path": path.casefold(), "content_hash": content_hash}
                )
                candidate = AutomationTriggerEvent(
                    trigger_id=trigger.id,
                    path=path,
                    action="upsert",
                    content_hash=content_hash,
                    event_key=event_key,
                    stable_at=now_iso(),
                )
                event, _created = store.create_or_get_trigger_event(candidate)
                last_event = event
                if event.status == TriggerEventStatus.RUN_CREATED:
                    return event
                run = self._create_run_for_event(event)
                return store.get_trigger_event(event.id) if run is not None else None
            except asyncio.CancelledError:
                raise
            except (OSError, ValueError, RuntimeError) as exc:
                if attempt >= _MAX_TRIGGER_ATTEMPTS:
                    if last_event is not None:
                        store.update_trigger_event(
                            last_event.id,
                            status=TriggerEventStatus.FAILED,
                            last_error_code="processing_failed",
                            increment_attempts=True,
                        )
                    self._audit_failure(trigger, path, "file_not_stable", exc, attempts=attempt)
                    return None
                await asyncio.sleep(min(2.0, 0.25 * (2 ** (attempt - 1))))
        return None

    def _remove_completed(self, key: tuple[str, str], completed: asyncio.Task[Any]) -> None:
        if self._tasks.get(key) is completed:
            self._tasks.pop(key, None)

    def _create_run_for_event(self, event: AutomationTriggerEvent) -> AutomationRun | None:
        trigger = next((item for item in store.list_triggers(limit=500) if item.id == event.trigger_id), None)
        if trigger is None or not trigger.enabled:
            store.update_trigger_event(
                event.id,
                status=TriggerEventStatus.FAILED,
                last_error_code="trigger_unavailable",
                increment_attempts=True,
            )
            return None
        template = store.get_template(trigger.template_id)
        if template is None or not template.enabled:
            store.update_trigger_event(
                event.id,
                status=TriggerEventStatus.FAILED,
                last_error_code="template_unavailable",
                increment_attempts=True,
            )
            return None
        run = store.create_automation_run(
            AutomationRun(
                template_id=template.id,
                template_version=template.current_version,
                trigger_id=trigger.id,
                idempotency_key=f"file-trigger:{event.event_key}",
                input_values={
                    "file": event.path,
                    "content_hash": event.content_hash,
                    "trigger_event_id": event.id,
                },
                status=AutomationStatus.DRAFT,
            )
        )
        if run_budget.get_run_budget(run.id) is None:
            run_budget.create_run_budget(run.id)
        envelope = create_content_envelope_from_hash(
            source_kind="file",
            source_id=event.id,
            origin=event.path,
            content_hash=event.content_hash,
            trust_level="untrusted",
            taint_flags=["external_content", "file_content"],
            task_scope=run.id,
        )
        store.upsert_run_item(
            AutomationRunItem(
                run_id=run.id,
                item_key=event.event_key,
                status=AutomationStatus.DRAFT,
                source=envelope,
                input_values={"file": event.path, "content_hash": event.content_hash},
            )
        )
        store.update_trigger_event(
            event.id,
            status=TriggerEventStatus.RUN_CREATED,
            run_id=run.id,
            stable_at=event.stable_at or now_iso(),
            increment_attempts=True,
        )
        self._notify_inbox(run, event)
        return run

    def _notify_inbox(self, run: AutomationRun, event: AutomationTriggerEvent) -> None:
        try:
            notification_service.notify(
                "自动化任务待确认",
                f"检测到 {Path(event.path).name}，已加入任务收件箱。",
                task_id=run.task_id or run.id,
                severity="info",
            )
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary; notification cannot change trigger idempotency.
            audit.record(
                "automation.file_trigger.notification_failed",
                "AutomationFileTriggerService",
                {"run_id": run.id, "error_type": type(exc).__name__},
            )
        audit.record(
            "automation.file_trigger.run_created",
            "AutomationFileTriggerService",
            {
                "trigger_id": event.trigger_id,
                "event_id": event.id,
                "run_id": run.id,
                "path_hash": stable_content_hash(event.path),
                "content_hash": event.content_hash,
                "execution_authorized": False,
            },
        )

    @staticmethod
    def _audit_failure(
        trigger: AutomationTrigger,
        path: str,
        code: str,
        exc: Exception,
        *,
        attempts: int = 1,
    ) -> None:
        audit.record(
            "automation.file_trigger.failed",
            "AutomationFileTriggerService",
            {
                "trigger_id": trigger.id,
                "path_hash": stable_content_hash(path),
                "error_code": code,
                "error_type": type(exc).__name__,
                "attempts": attempts,
            },
        )

    @staticmethod
    def _audit_failure_for_event(event: AutomationTriggerEvent, code: str, exc: Exception) -> None:
        audit.record(
            "automation.file_trigger.failed",
            "AutomationFileTriggerService",
            {
                "trigger_id": event.trigger_id,
                "event_id": event.id,
                "path_hash": stable_content_hash(event.path),
                "error_code": code,
                "error_type": type(exc).__name__,
                "attempts": event.attempts + 1,
            },
        )


async def _wait_for_stable_file(path: Path, *, stable_seconds: float) -> None:
    stable_for = 0.0
    last_signature: tuple[int, int] | None = None
    poll_seconds = max(0.1, min(0.5, stable_seconds / 4))
    deadline = asyncio.get_running_loop().time() + max(10.0, stable_seconds * 10)
    while asyncio.get_running_loop().time() < deadline:
        stat = await asyncio.to_thread(path.stat)
        signature = (int(stat.st_size), int(stat.st_mtime_ns))
        with path.open("rb") as handle:
            handle.read(1)
        if signature == last_signature:
            stable_for += poll_seconds
            if stable_for >= stable_seconds:
                return
        else:
            last_signature = signature
            stable_for = 0.0
        await asyncio.sleep(poll_seconds)
    raise RuntimeError("file did not become stable before the trigger deadline")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _trigger_matches(trigger: AutomationTrigger, raw_path: str, action: str) -> bool:
    if not trigger.enabled:
        return False
    normalized_action = str(action or "").lower()
    if normalized_action == "upsert" and not set(trigger.events).intersection({"created", "modified", "moved"}):
        return False
    if normalized_action not in {"upsert", *trigger.events}:
        return False
    candidate = Path(raw_path).expanduser().resolve(strict=False)
    directory = Path(trigger.directory).expanduser().resolve(strict=False)
    try:
        candidate.relative_to(directory)
    except ValueError:
        return False
    return candidate.suffix.lower() in set(trigger.suffixes)


def _normalized_path(path: str) -> str:
    return str(Path(path).expanduser().resolve(strict=False))
