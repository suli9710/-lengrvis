from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core import db
from app.indexer.file_watcher import FileWatcher
from app.orchestration.dispatcher import EventDispatcher
from app.perception.environment_stream import (
    EnvironmentEvent,
    EnvironmentEventType,
    EnvironmentRule,
    EnvironmentRuleEngine,
    EnvironmentStream,
    file_changed_event,
    reset_environment_stream,
)
from app.perception.schemas import AppContext


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    reset_environment_stream()
    db.init_db()
    yield
    reset_environment_stream()


def test_environment_rule_matches_event_type_and_subject():
    rule = EnvironmentRule(
        id="excel_rule",
        event_pattern=[EnvironmentEventType.APP_SWITCHED],
        title="Spreadsheet context",
        body="Use spreadsheet context.",
        metadata_matches={"process_name": "EXCEL.EXE"},
    )
    event = EnvironmentEvent(
        environment_type=EnvironmentEventType.APP_SWITCHED,
        metadata={"process_name": "EXCEL.EXE"},
    )
    engine = EnvironmentRuleEngine([rule])

    suggestions = engine.evaluate(event)

    assert len(suggestions) == 1
    assert suggestions[0].rule_id == "excel_rule"
    assert engine.evaluate(EnvironmentEvent(environment_type=EnvironmentEventType.FILE_CHANGED, path="Budget.xlsx")) == []


def test_environment_stream_emits_file_change_and_proactive_suggestion():
    async def run() -> None:
        dispatcher = EventDispatcher()
        rule_engine = EnvironmentRuleEngine(
            [
                EnvironmentRule(
                    id="xlsx_change",
                    event_pattern=[EnvironmentEventType.FILE_CHANGED],
                    title="Spreadsheet changed",
                    body="Spreadsheet changed; offer to refresh analysis.",
                    metadata_matches={"action": "upsert"},
                )
            ]
        )
        seen: list[EnvironmentEvent] = []
        suggestions_seen = []

        async def on_event(event: EnvironmentEvent) -> None:
            seen.append(event)

        async def on_suggestion(event) -> None:
            suggestions_seen.append(event)

        dispatcher.register("environment.event", on_event)
        dispatcher.register("environment.proactive_suggestion", on_suggestion)
        stream = EnvironmentStream(dispatcher=dispatcher, rule_engine=rule_engine)

        event = file_changed_event("C:/work/Budget.xlsx", "upsert")
        suggestions = await stream.emit(event)

        assert seen == [event]
        assert seen[0].environment_type == EnvironmentEventType.FILE_CHANGED
        assert len(suggestions) == 1
        assert suggestions[0].body == "Spreadsheet changed; offer to refresh analysis."
        assert suggestions_seen == suggestions

    asyncio.run(run())


def test_environment_stream_dispatches_environment_event_to_dispatcher():
    async def run() -> None:
        received: list[EnvironmentEvent] = []

        async def handler(event: EnvironmentEvent) -> None:
            received.append(event)

        dispatcher = EventDispatcher()
        dispatcher.register("environment.event", handler)
        stream = EnvironmentStream(dispatcher=dispatcher, rule_engine=EnvironmentRuleEngine([]))

        await stream.emit(EnvironmentEvent(environment_type=EnvironmentEventType.APP_SWITCHED, summary_text="notepad.exe notes.txt"))

        assert len(received) == 1
        assert received[0].environment_type == EnvironmentEventType.APP_SWITCHED

    asyncio.run(run())


def test_file_watcher_bridges_debounced_changes_to_environment_stream(tmp_path: Path):
    async def run() -> None:
        suggestions_seen = []
        dispatcher = EventDispatcher()
        dispatcher.register("environment.proactive_suggestion", lambda event: suggestions_seen.append(event))
        stream = EnvironmentStream(dispatcher=dispatcher, rule_engine=EnvironmentRuleEngine(default_file_rules()))
        watcher = FileWatcher(debounce_seconds=0.1)

        watched_dir = tmp_path / "watched"
        watched_dir.mkdir()
        bridge = stream.file_change_sink()
        watcher.subscribe_changes(bridge)
        await stream.start()
        await watcher.start([str(watched_dir)])
        try:
            test_file = watched_dir / "notes.txt"
            test_file.write_text("environment stream bridge", encoding="utf-8")
            await asyncio.sleep(1.0)
        finally:
            await watcher.stop()
            watcher.unsubscribe_changes(bridge)
            await stream.stop()

        assert suggestions_seen
        assert suggestions_seen[-1].matched_event_types == [EnvironmentEventType.FILE_CHANGED]

    asyncio.run(run())


def test_environment_stream_continues_after_dispatcher_handler_error():
    async def run() -> None:
        seen: list[EnvironmentEvent] = []
        dispatcher = EventDispatcher()

        def bad_handler(_event: EnvironmentEvent) -> None:
            raise RuntimeError("sink failed")

        dispatcher.register("environment.event", bad_handler)
        dispatcher.register("environment.event", lambda event: seen.append(event))
        stream = EnvironmentStream(dispatcher=dispatcher, rule_engine=EnvironmentRuleEngine([]))

        event = EnvironmentEvent(environment_type=EnvironmentEventType.SCREEN_CHANGED, summary_text="desktop changed")
        await stream.emit(event)

        assert seen == [event]

    asyncio.run(run())


def test_environment_rules_can_load_from_settings():
    class Settings:
        environment_rules = [
            {
                "id": "notepad_rule",
                "event_pattern": ["app_switched"],
                "title": "Notepad",
                "body": "Notepad is active.",
            }
        ]

    from app.perception.environment_stream import build_environment_stream

    stream = build_environment_stream(settings=Settings())
    rules = stream.rule_engine.rules

    assert len(rules) == 1
    assert rules[0].event_pattern == [EnvironmentEventType.APP_SWITCHED]
    assert rules[0].body == "Notepad is active."


def test_environment_rule_engine_matches_event_sequence():
    engine = EnvironmentRuleEngine(
        [
            EnvironmentRule(
                id="app_then_file",
                event_pattern=[EnvironmentEventType.APP_SWITCHED, EnvironmentEventType.FILE_CHANGED],
                title="Refresh context",
                body="Refresh context after file change.",
            )
        ]
    )

    assert engine.evaluate(EnvironmentEvent(environment_type=EnvironmentEventType.APP_SWITCHED, subject="Editor")) == []
    suggestions = engine.evaluate(file_changed_event("C:/work/notes.txt", "upsert"))

    assert len(suggestions) == 1
    assert suggestions[0].rule_id == "app_then_file"
    assert suggestions[0].matched_event_types == [
        EnvironmentEventType.APP_SWITCHED,
        EnvironmentEventType.FILE_CHANGED,
    ]


def test_environment_stream_system_event_helpers_emit_expected_types():
    async def run() -> None:
        seen: list[EnvironmentEvent] = []
        dispatcher = EventDispatcher()
        dispatcher.register("environment.event", lambda event: seen.append(event))
        stream = EnvironmentStream(dispatcher=dispatcher, rule_engine=EnvironmentRuleEngine([]))

        await stream.network_changed("offline", interface="wifi")
        await stream.usb_connected("Backup Drive", volume="E:")
        await stream.session_locked(True)

        assert [event.environment_type for event in seen] == [
            EnvironmentEventType.NETWORK_CHANGED,
            EnvironmentEventType.USB_CONNECTED,
            EnvironmentEventType.SESSION_LOCKED,
        ]
        assert seen[0].details["interface"] == "wifi"
        assert seen[1].device_name == "Backup Drive"
        assert seen[2].locked is True

    asyncio.run(run())


def test_environment_stream_app_context_poll_emits_only_on_switch():
    async def run() -> None:
        contexts = iter(
            [
                AppContext(available=True, process_name="notepad.exe", active_window_title="one.txt"),
                AppContext(available=True, process_name="notepad.exe", active_window_title="one.txt"),
                AppContext(available=True, process_name="EXCEL.EXE", active_window_title="Budget.xlsx"),
            ]
        )
        seen: list[EnvironmentEvent] = []
        dispatcher = EventDispatcher()
        dispatcher.register("environment.event", lambda event: seen.append(event))
        stream = EnvironmentStream(
            dispatcher=dispatcher,
            rule_engine=EnvironmentRuleEngine([]),
            app_context_fn=lambda: next(contexts),
        )

        first = await stream.poll_app_context_once()
        duplicate = await stream.poll_app_context_once()
        second = await stream.poll_app_context_once()

        assert first is seen[0]
        assert duplicate is None
        assert second is seen[1]
        assert [event.environment_type for event in seen] == [
            EnvironmentEventType.APP_SWITCHED,
            EnvironmentEventType.APP_SWITCHED,
        ]
        assert seen[1].metadata["process_name"] == "EXCEL.EXE"

    asyncio.run(run())


def default_file_rules() -> list[EnvironmentRule]:
    return [
        EnvironmentRule(
            id="file_changed",
            event_pattern=[EnvironmentEventType.FILE_CHANGED],
            title="File changed",
            body="File changed.",
        )
    ]
