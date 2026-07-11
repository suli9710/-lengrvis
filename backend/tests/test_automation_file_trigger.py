from __future__ import annotations

import asyncio
from pathlib import Path

from app.automation import file_trigger, run_budget, store
from app.automation.file_trigger import AutomationFileTriggerService, _file_sha256
from app.automation.models import (
    AutomationTrigger,
    AutomationTriggerEvent,
    TriggerEventStatus,
)


async def _stable(_path: Path, *, stable_seconds: float) -> None:  # noqa: ARG001
    return None


def _template_and_trigger(directory: Path):
    template, _version = store.create_template(
        name="表格核对",
        goal_template="读取 {{file}} 并核对网页",
    )
    trigger = store.create_trigger(
        AutomationTrigger(
            template_id=template.id,
            directory=str(directory),
            suffixes=[".csv"],
            stable_seconds=0.5,
        )
    )
    return template, trigger


def test_stable_file_trigger_creates_one_draft_inbox_run(monkeypatch, tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    source = incoming / "orders.csv"
    source.write_text("order,total\nA-1,20\n", encoding="utf-8")
    template, trigger = _template_and_trigger(incoming)
    notifications: list[tuple[str, str, str]] = []
    monkeypatch.setattr(file_trigger, "_wait_for_stable_file", _stable)
    monkeypatch.setattr(
        file_trigger.notification_service,
        "notify",
        lambda title, body, *, task_id, **_kwargs: notifications.append((title, body, task_id)),
    )
    service = AutomationFileTriggerService(allowed_directories=[str(tmp_path)])

    first = asyncio.run(service.process_change(str(source)))
    second = asyncio.run(service.process_change(str(source)))

    assert len(first) == len(second) == 1
    assert first[0].id == second[0].id
    assert first[0].status == TriggerEventStatus.RUN_CREATED
    runs = store.list_automation_runs(trigger_id=trigger.id)
    assert len(runs) == 1
    assert runs[0].template_id == template.id
    assert runs[0].status == "draft"
    assert run_budget.get_run_budget(runs[0].id) is not None
    items = store.list_run_items(runs[0].id)
    assert len(items) == 1
    assert items[0].source is not None
    assert items[0].source.content_hash == _file_sha256(source)
    assert items[0].source.trust_level == "untrusted"
    assert items[0].source.taint_flags == ["external_content", "file_content"]
    assert len(notifications) == 1
    assert notifications[0][2] == runs[0].id


def test_trigger_ignores_wrong_suffix_and_outside_directory(monkeypatch, tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    outside = tmp_path / "outside"
    incoming.mkdir()
    outside.mkdir()
    _template, _trigger = _template_and_trigger(incoming)
    monkeypatch.setattr(file_trigger, "_wait_for_stable_file", _stable)
    service = AutomationFileTriggerService(allowed_directories=[str(tmp_path)])

    wrong_suffix = incoming / "notes.txt"
    wrong_suffix.write_text("ignored", encoding="utf-8")
    wrong_directory = outside / "orders.csv"
    wrong_directory.write_text("ignored", encoding="utf-8")

    assert asyncio.run(service.process_change(str(wrong_suffix))) == []
    assert asyncio.run(service.process_change(str(wrong_directory))) == []
    assert store.list_automation_runs() == []


def test_changed_content_creates_a_new_idempotent_run(monkeypatch, tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    source = incoming / "orders.csv"
    source.write_text("first", encoding="utf-8")
    _template, trigger = _template_and_trigger(incoming)
    monkeypatch.setattr(file_trigger, "_wait_for_stable_file", _stable)
    monkeypatch.setattr(file_trigger.notification_service, "notify", lambda *_args, **_kwargs: None)
    service = AutomationFileTriggerService(allowed_directories=[str(tmp_path)])

    asyncio.run(service.process_change(str(source)))
    source.write_text("second", encoding="utf-8")
    asyncio.run(service.process_change(str(source)))

    assert len(store.list_automation_runs(trigger_id=trigger.id)) == 2


def test_restart_recovers_observed_event_without_duplicate_submission(monkeypatch, tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    source = incoming / "orders.csv"
    source.write_text("recover", encoding="utf-8")
    _template, trigger = _template_and_trigger(incoming)
    content_hash = _file_sha256(source)
    event, created = store.create_or_get_trigger_event(
        AutomationTriggerEvent(
            trigger_id=trigger.id,
            path=str(source.resolve()),
            content_hash=content_hash,
            event_key=f"recover:{content_hash}",
        )
    )
    assert created is True
    monkeypatch.setattr(file_trigger.notification_service, "notify", lambda *_args, **_kwargs: None)
    service = AutomationFileTriggerService(allowed_directories=[str(tmp_path)])

    first = asyncio.run(service.recover_pending())
    second = asyncio.run(service.recover_pending())

    assert len(first) == 1
    assert second == []
    recovered = store.get_trigger_event(event.id)
    assert recovered is not None
    assert recovered.status == TriggerEventStatus.RUN_CREATED
    assert len(store.list_automation_runs(trigger_id=trigger.id)) == 1
