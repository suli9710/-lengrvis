"""Resident scheduling, scope revocation, recovery and source subscriptions."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from app.core import butler_store as store
from app.core import db
from app.core.audit import record
from app.core.butler_models import (
    TERMINAL_MATTER_STATUSES,
    AutonomyScope,
    ButlerMatter,
    MatterInteraction,
    MatterStatus,
)
from app.core.local_data_lifecycle import data_generation, local_data_operation, tracks_local_data
from app.core.schemas import AgentMessage, MessageType, now_iso
from app.orchestration.butler_runtime import desktop_ready
from app.services.butler_coordination import coordination_view
from app.services.butler_scopes import ButlerScopeRevoked, enabled_scopes, matter_scopes
from app.services.butler_state import (
    _after_seconds,
    _due,
    _file_wait_state,
    _priority,
    _set_status,
)

logger = logging.getLogger(__name__)


class ButlerResident:
    async def start(self) -> None:
        if self._loop_task and not self._loop_task.done():
            return
        await self.recover()
        self._loop_task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._source_job:
            self._source_job.cancel()
            await asyncio.gather(self._source_job, return_exceptions=True)
        if self._loop_task:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
        for task in list(self._jobs.values()):
            task.cancel()
        await asyncio.gather(*self._jobs.values(), return_exceptions=True)
        self._jobs.clear()
        if self._watcher:
            await asyncio.to_thread(self._watcher.stop)
            self._watcher = None
        if self._event_sink:
            from app.perception.environment_stream import get_environment_stream

            get_environment_stream().unsubscribe(self._event_sink)
            self._event_sink = None

    async def _loop(self) -> None:
        while True:
            try:
                with db.using_data_dir(self.data_dir):
                    await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: retry durable state, log only type.
                logger.warning("Butler tick failed: %s", type(exc).__name__)
            await asyncio.sleep(1)

    def _spawn(self, matter_id: str) -> None:
        # All callers share dispatch's durable dependency gate. Coordinated work
        # is admitted on the next tick so a chat request cannot bypass ordering.
        if store.control_state()["coordination_enabled"]:
            return
        self._spawn_now(matter_id)

    def _spawn_now(self, matter_id: str) -> None:
        if matter_id in self._jobs and not self._jobs[matter_id].done():
            return
        job = asyncio.create_task(self.dispatch(matter_id))
        self._jobs[matter_id] = job
        job.add_done_callback(
            lambda done: self._jobs.pop(matter_id, None) if self._jobs.get(matter_id) is done else None
        )

    async def tick(self) -> None:
        if self.generation != data_generation():
            if self._watcher:
                await asyncio.to_thread(self._watcher.stop)
                self._watcher = None
            if self._source_job and not self._source_job.done():
                self._source_job.cancel()
            self.generation = data_generation()
            self._watch_roots = ()
        control = store.control_state()
        await self.refresh_sources(control)
        if control["coordination_enabled"]:
            self._check_dependency_outcomes()
        matters = store.list_records(ButlerMatter, limit=None)
        views = coordination_view(matters, enabled=control["coordination_enabled"])
        if control["coordination_enabled"]:
            from app.services.butler_coordination import deadline_band
            from app.services.butler_notifications import flush_notifications

            for item in matters:
                view = views[item.id]
                if view["needs_decision"] and item.status not in {MatterStatus.CANCELLED, MatterStatus.FAILED}:
                    self.notify_once(
                        item,
                        "coordination:" + store.source_key(view["arrangement_reason"]),
                        message=view["arrangement_reason"],
                        urgent=True,
                    )
                if item.status not in TERMINAL_MATTER_STATUSES and deadline_band(item, datetime.now(UTC)) < 2:
                    self.notify_once(
                        item, "deadline:" + item.due_at, message="事项即将到期或已逾期：" + item.goal, urgent=True
                    )
            flush_notifications()
            matters = store.list_records(ButlerMatter, limit=None)
        for matter in matters:
            try:
                if matter.status not in TERMINAL_MATTER_STATUSES and matter.status != MatterStatus.PAUSED:
                    matter_scopes(matter)
                if matter.active_run_id and matter.status in {MatterStatus.RUNNING, MatterStatus.WAITING_APPROVAL}:
                    await self.sync_run(matter)
                elif matter.status == MatterStatus.WAITING_EVENT and not control["paused"]:
                    if await self.wait_satisfied(matter):
                        resolved = matter.waiting.model_dump(mode="json") if matter.waiting else {}

                        def resume_wait(item: ButlerMatter, resolved=resolved) -> None:
                            item.answers["resolved_wait"] = {**resolved, "resolved_at": now_iso()}
                            item.waiting = None
                            _set_status(item, MatterStatus.QUEUED, "等待条件已满足，准备继续")

                        store.mutate(
                            ButlerMatter,
                            matter.id,
                            resume_wait,
                            expected_version=matter.version,
                        )
            except ButlerScopeRevoked:
                await self.control_matter(matter.id, "pause")
            except store.ButlerConflict:
                continue
            except (KeyError, OSError, ValueError):
                # One missing resource must not starve every other resident matter.
                latest = store.get(ButlerMatter, matter.id)
                if latest:
                    self.require_reconciliation(latest, "当前现场需要重新核对，其他事项仍会继续。")
        if control["paused"]:
            return
        if control["enabled"]:
            from app.services.butler_events import flush_events

            flush_events()
        matters = store.list_records(ButlerMatter, limit=None)
        views = coordination_view(matters, enabled=control["coordination_enabled"])
        background_active = sum(
            item.source != "user" and item.status in {MatterStatus.PLANNING, MatterStatus.RUNNING} for item in matters
        )
        coordination_active = sum(item.status in {MatterStatus.PLANNING, MatterStatus.RUNNING} for item in matters)
        for matter in sorted(
            matters, key=(lambda item: views[item.id]["rank"]) if control["coordination_enabled"] else _priority
        ):
            if matter.status != MatterStatus.QUEUED:
                continue
            if views[matter.id]["blocked"]:
                continue
            if control["coordination_enabled"] and coordination_active >= 2:
                break
            if matter.source != "user":
                if not control["enabled"] or background_active >= 2:
                    continue
                background_active += 1
            if control["coordination_enabled"]:
                coordination_active += 1
                self._spawn_now(matter.id)
            else:
                self._spawn(matter.id)

    def _check_dependency_outcomes(self) -> None:
        import time

        from app.services.butler_evidence import verify_execution

        if time.monotonic() - self._last_coordination_check < 30:
            return
        self._last_coordination_check = time.monotonic()
        matters = store.list_records(ButlerMatter, limit=None)
        referenced = {
            identity for item in matters if item.status != MatterStatus.CANCELLED for identity in item.depends_on
        }
        for item in matters:
            if (
                item.id not in referenced
                or item.status != MatterStatus.COMPLETED
                or not item.task_ids
                or item.outcome_revoked
            ):
                continue
            try:
                valid, _, _ = verify_execution(item)
            except (OSError, ValueError):
                valid = False
            if valid:
                continue

            def changed(record: ButlerMatter) -> None:
                record.outcome_revoked = True
                record.outcome_revision += 1
                record.progress = "已有成果发生变化，请核对关联事项。"

            try:
                store.mutate(ButlerMatter, item.id, changed, expected_version=item.version)
            except store.ButlerConflict:
                continue

    async def wait_satisfied(self, matter: ButlerMatter) -> bool:
        wait = matter.waiting
        if wait is None:
            return False
        matter_scopes(matter)
        if wait.kind in {"time", "provider"}:
            return _due(wait.due_at)
        if wait.kind == "desktop_idle":
            return desktop_ready()
        if wait.kind in {"file_exists", "file_changed"}:
            state = _file_wait_state(matter, wait)
            if state is None:
                return False
            if wait.kind == "file_exists":
                return state != "absent"
            if not wait.baseline:
                # Older waits may lack a baseline. Establish one before waking.
                store.mutate(
                    ButlerMatter,
                    matter.id,
                    lambda item: setattr(item.waiting, "baseline", state),
                    expected_version=matter.version,
                )
                return False
            if state == "absent":
                return False
            # Preserve waits saved with the original mtime-only representation.
            return (state.split(":", 1)[0] if wait.baseline.isdecimal() else state) != wait.baseline
        if wait.kind == "browser_change":
            from app.services.butler_evidence import browser_observation, browser_progress_key
            from app.services.desktop_browser_adapter import request_task_observation

            if wait.due_at and not _due(wait.due_at):
                return False
            observation = browser_observation(matter, wait.subject)
            if observation is None:
                return False
            observation = await request_task_observation(matter, observation["session_id"])
            key = browser_progress_key(observation)
            if wait.baseline and key != wait.baseline:
                return True

            def next_observation(item: ButlerMatter) -> None:
                item.waiting.baseline = key
                item.waiting.due_at = _after_seconds(30)

            store.mutate(ButlerMatter, matter.id, next_observation, expected_version=matter.version)
            return False
        return False

    async def _pause_run(self, matter: ButlerMatter) -> None:
        if matter.active_run_id:
            from app.services import run_service

            run_service.pause_run(matter.active_run_id)

    async def control_matter(self, matter_id: str, action: str, *, global_pause: bool = False) -> ButlerMatter:
        matter = store.get(ButlerMatter, matter_id)
        if matter is None:
            raise KeyError(matter_id)
        if action not in {"pause", "resume", "cancel"}:
            raise ValueError("Unknown matter control")
        if matter.status in TERMINAL_MATTER_STATUSES:
            return matter
        if action == "resume":
            if matter.status in TERMINAL_MATTER_STATUSES:
                return matter
            if matter.interaction and matter.interaction.kind == "choose_dependency":
                return matter
            current_scopes = [store.get(AutonomyScope, scope_id) for scope_id in matter.scope_versions]
            if any(scope is None or not scope.enabled for scope in current_scopes):
                raise ButlerScopeRevoked("授权范围已撤销，请先重新授权。")
            versions = {scope.id: scope.version for scope in current_scopes}

            def resume(item: ButlerMatter) -> None:
                if item.scope_versions != versions:
                    # Explicit resume after a user-confirmed scope edit starts
                    # a fresh plan; old approvals/tickets must remain unusable.
                    item.scope_versions = versions
                    item.active_run_id = ""
                    item.active_task_id = ""
                    item.execution_ticket = ""
                item.resume_after_global_pause = False
                _set_status(item, MatterStatus.QUEUED, "准备恢复")

            matter = store.mutate(
                ButlerMatter,
                matter.id,
                resume,
                expected_version=matter.version,
            )
            self._spawn(matter.id)
            return matter
        status = MatterStatus.CANCELLED if action == "cancel" else MatterStatus.PAUSED

        def pause_item(item: ButlerMatter) -> None:
            item.resume_after_global_pause = global_pause
            _set_status(item, status, "已取消" if action == "cancel" else "已暂停")
            if action == "cancel":
                item.interaction = None
                item.waiting = None

        matter = store.mutate(
            ButlerMatter,
            matter.id,
            pause_item,
            expected_version=matter.version,
        )
        job = self._jobs.get(matter_id)
        if job and job is not asyncio.current_task():
            job.cancel()
        if action == "cancel" and matter.active_task_id:
            from app.services.task_service import cancel_task

            await cancel_task(matter.active_task_id, strict=False)
        else:
            await self._pause_run(matter)
        return matter

    async def pause(self, paused: bool) -> dict:
        state = store.set_control(paused=paused)
        if paused:
            for matter in store.list_records(ButlerMatter, limit=None):
                if matter.status in {
                    MatterStatus.RUNNING,
                    MatterStatus.PLANNING,
                    MatterStatus.QUEUED,
                    MatterStatus.WAITING_APPROVAL,
                }:
                    await self.control_matter(matter.id, "pause", global_pause=True)
        else:
            for matter in store.list_records(ButlerMatter, limit=None):
                if matter.status == MatterStatus.PAUSED and matter.resume_after_global_pause:
                    try:
                        await self.control_matter(matter.id, "resume")
                    except ValueError:
                        continue
        return state

    async def revoke_scope(self, scope_id: str, version: int) -> AutonomyScope:
        scope = store.mutate(
            AutonomyScope, scope_id, lambda item: setattr(item, "enabled", False), expected_version=version
        )
        await self.invalidate_scope_work(scope_id)
        record("butler.scope_revoked", "user", {"scope_id": scope.id, "version": scope.version})
        return scope

    async def invalidate_scope_work(self, scope_id: str) -> None:
        if self._source_job and not self._source_job.done():
            self._source_job.cancel()
            await asyncio.gather(self._source_job, return_exceptions=True)
        with db.connect() as conn:
            conn.execute("DELETE FROM butler_events WHERE scope_id=?", (scope_id,))
        for matter in store.list_records(ButlerMatter, limit=None):
            if scope_id in matter.scope_versions and matter.status not in TERMINAL_MATTER_STATUSES:
                await self.control_matter(matter.id, "pause")
        from app.services.browser_host_bridge_service import browser_host_bridge_hub

        await browser_host_bridge_hub.request_scope_cancel(scope_id)
        await self.refresh_sources(store.control_state())

    async def recover(self) -> None:
        from app.services.butler_notifications import flush_notifications

        flush_notifications(force=True)
        from app.services import run_service
        from app.services.butler_evidence import resume_readiness

        for matter in store.list_records(ButlerMatter, limit=None):
            if matter.status in {MatterStatus.RUNNING, MatterStatus.PLANNING}:
                try:
                    matter_scopes(matter)
                    if matter.active_run_id:
                        run = run_service.get_run(matter.active_run_id)
                        if str(run.phase) in {"completed", "failed", "denied", "cancelled", "awaiting_approval"}:
                            await self.sync_run(matter)
                            continue
                    ready, reason = resume_readiness(matter)
                except ButlerScopeRevoked:
                    await self.control_matter(matter.id, "pause")
                    continue
                except (KeyError, ValueError, OSError):
                    ready, reason = False, "原执行记录无法核对，请查看已有结果。"

                def update(item: ButlerMatter, ready=ready, reason=reason) -> None:
                    item.status = MatterStatus.QUEUED if ready else MatterStatus.WAITING_INPUT
                    item.progress = "已核验检查点，准备接续原执行。" if ready else reason
                    if not ready:
                        item.interaction = MatterInteraction(kind="reconcile", message=item.progress)

                store.mutate(ButlerMatter, matter.id, update, expected_version=matter.version)

    @tracks_local_data
    def notify_once(self, matter: ButlerMatter, key: str, *, message: str = "", urgent: bool = False) -> None:
        current = store.get(ButlerMatter, matter.id)
        if current is None:
            return
        coordinated = store.control_state()["coordination_enabled"]
        if coordinated:
            key = f"{key}:{current.status.value}:{current.outcome_revision}"
        if key in current.notified_keys or current.last_notified_key == key:
            return
        from app.services.notification_service import notify

        def mark(item: ButlerMatter) -> None:
            item.last_notified_key = key
            item.notified_keys = [*item.notified_keys[-63:], key]
            if coordinated and not immediate:
                item.pending_notifications[key] = {"message": message or item.progress, "created_at": now_iso()}

        immediate = (
            urgent
            or bool(current.interaction)
            or current.status in {MatterStatus.FAILED, MatterStatus.WAITING_INPUT, MatterStatus.WAITING_APPROVAL}
        )

        durable_message = None
        try:
            with db.connect() as conn:
                db._begin_immediate_transaction(conn)
                claimed = store.mutate(ButlerMatter, matter.id, mark, expected_version=current.version)
                if coordinated and immediate:
                    # The live bus is asynchronous; commit the inbox and dedup marker together.
                    body = message or claimed.progress
                    durable_message = AgentMessage(
                        id="msg_butler_alert_" + store.source_key([claimed.id, key]),
                        task_id=claimed.active_task_id or "__system__",
                        from_agent="NotificationService",
                        message_type=MessageType.NOTIFICATION,
                        content=body,
                        structured_payload={"title": "AI 管家", "body": body, "severity": "info"},
                    )
                    db.upsert_model("agent_messages", durable_message)
        except store.ButlerConflict:
            return
        if claimed.active_run_id and db.fetch_one("runs", claimed.active_run_id):
            from app.orchestration.run_event_bus import run_event_bus

            run_event_bus.publish(
                claimed.active_run_id,
                "butler.matter_updated",
                {
                    "matter_id": claimed.id,
                    "matter_version": claimed.version,
                    "status": claimed.status.value,
                    "waiting_reason": claimed.progress if claimed.waiting or claimed.interaction else "",
                    "completion_evidence": claimed.completion_evidence
                    if claimed.status == MatterStatus.COMPLETED
                    else [],
                },
            )
        if durable_message is not None:
            from app.services.notification_service import _get_bus

            _get_bus().publish(durable_message)
        elif key not in claimed.pending_notifications:
            notify("AI 管家", message or claimed.progress, task_id=claimed.active_task_id or None)

    async def refresh_sources(self, control: dict) -> None:
        import time

        from app.services.butler_events import enqueue_event

        roots = (
            tuple(sorted({path for scope in enabled_scopes() for path in scope.directories}))
            if control["enabled"] and not control["paused"]
            else ()
        )
        if roots != self._watch_roots:
            if self._watcher:
                await asyncio.to_thread(self._watcher.stop)
                self._watcher = None
            self._watch_roots = roots
            if roots:
                from app.indexer.file_watcher import DirectoryChangeWatcher

                generation = self.generation

                def changed(path: str, action: str) -> None:
                    with (
                        db.using_data_dir(self.data_dir),
                        local_data_operation(expected_generation=generation) as valid,
                    ):
                        if valid:
                            enqueue_event("file_changed", path, {"action": action})

                self._watcher = DirectoryChangeWatcher(changed, allowed_directories=list(roots))
                await asyncio.to_thread(self._watcher.start, list(roots))
        if self._event_sink is None:
            from app.perception.environment_stream import get_environment_stream

            def on_environment(event):
                with db.using_data_dir(self.data_dir):
                    if str(event.environment_type) == "app_switched":
                        return
                    enqueue_event(
                        str(event.environment_type),
                        event.path or event.subject,
                        {"status": event.status, "process_id": getattr(event.app_context, "process_id", None)},
                    )

            self._event_sink = on_environment
            get_environment_stream().subscribe(on_environment)
        if control["paused"] or not control["enabled"]:
            if self._source_job and not self._source_job.done():
                self._source_job.cancel()
            return
        if time.monotonic() - self._last_source_poll >= 30 and (self._source_job is None or self._source_job.done()):
            self._last_source_poll = time.monotonic()
            self._source_job = asyncio.create_task(self.poll_context_sources())

    async def poll_context_sources(self) -> None:
        """Inspect only granted sources. No model request occurs in this poller."""
        from app.services.browser_execution_grants import issue_scope_observation
        from app.services.browser_host_bridge_service import browser_host_bridge_hub
        from app.services.butler_events import enqueue_event

        with db.using_data_dir(self.data_dir), local_data_operation(expected_generation=self.generation) as valid:
            if not valid:
                return
            for scope in enabled_scopes():
                if "observe" not in scope.allowed_actions:
                    continue
                for origin in scope.origins:
                    if not browser_host_bridge_hub.status()["connected"]:
                        break
                    try:
                        await browser_host_bridge_hub.request_authorized_action(
                            issue_scope_observation(scope, origin, open_session=True)
                        )
                        reply = await browser_host_bridge_hub.request_authorized_action(
                            issue_scope_observation(scope, origin)
                        )
                        observation = reply.get("observation") or {}
                        if reply.get("ok") and observation.get("text"):
                            enqueue_event(
                                "browser_changed",
                                observation["url"],
                                {
                                    "version": store.source_key(observation.get("url"), observation.get("text")),
                                    "summary": str(observation["text"])[:1500],
                                    "truncated": bool(observation.get("truncated")) or len(observation["text"]) > 1500,
                                    "title": observation.get("title", ""),
                                },
                            )
                    except asyncio.CancelledError:
                        raise
                    except (ValueError, RuntimeError, TimeoutError):
                        continue
                if scope.applications:
                    await self.poll_app_scope(scope)

    async def poll_app_scope(self, scope: AutonomyScope) -> None:
        import os

        import psutil

        from app.perception.ui_automation import WindowsCOMUIAutomationTarget
        from app.policy.redaction import redact_public_text
        from app.services.butler_events import enqueue_event

        executables = {os.path.normcase(path) for path in scope.applications}
        candidates = []
        for process in psutil.process_iter(["exe", "pid"]):
            if process.info.get("exe") and os.path.normcase(process.info["exe"]) in executables:
                candidates.append(process.pid)
        target = WindowsCOMUIAutomationTarget()
        if not target.available:
            return
        for pid in candidates[:3]:
            try:
                observation = await asyncio.wait_for(
                    target.observe({"process_id": pid}, max_depth=1, max_elements=30), timeout=5
                )
                elements = [item for item in observation.get("elements", []) if item.get("process_id") == pid]
                if not elements:
                    continue
                summary = redact_public_text(
                    "\n".join(
                        str(item.get("name") or "") + " " + str(item.get("properties", {}).get("text") or "")
                        for item in elements
                    )
                )[:1500]
                enqueue_event(
                    "screen_changed",
                    f"application:{pid}",
                    {"process_id": pid, "summary": summary, "version": store.source_key(summary), "truncated": True},
                )
            except asyncio.CancelledError:
                raise
            except (ValueError, RuntimeError, TimeoutError, psutil.Error):
                continue
