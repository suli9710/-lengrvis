from unittest.mock import MagicMock

from app.core import db
from app.core.schemas import MessageType
from app.orchestration.agent_bus import AgentBus
from app.services import notification_service


def test_notify_publishes_to_bus():
    bus = MagicMock()
    notification_service.init_bus(bus)
    result = notification_service.notify("Test Title", "Test body", severity="info")
    assert result["queued"] is True
    assert result["title"] == "Test Title"
    assert result["severity"] == "info"
    bus.publish_text.assert_called_once()
    call_kwargs = bus.publish_text.call_args
    assert "__system__" in str(call_kwargs)


def test_notify_with_task_id():
    bus = MagicMock()
    notification_service.init_bus(bus)
    result = notification_service.notify("Done", "Task complete", task_id="task-123")
    assert result["task_id"] == "task-123"


def test_legacy_signature():
    """Backward compatibility: notify(message) still works."""
    bus = MagicMock()
    notification_service.init_bus(bus)
    result = notification_service.notify("Simple message")
    assert result["queued"] is True


def test_notify_publishes_persisted_notification_message(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    bus = AgentBus()
    notification_service.init_bus(bus)

    result = notification_service.notify("Done", "Task complete", task_id="task-123", severity="warning")

    messages = bus.get_messages("task-123")
    assert result == {
        "queued": True,
        "title": "Done",
        "body": "Task complete",
        "task_id": "task-123",
        "severity": "warning",
    }
    assert len(messages) == 1
    assert messages[0].message_type == MessageType.NOTIFICATION
    assert messages[0].content == "Task complete"
    assert messages[0].structured_payload == {
        "title": "Done",
        "body": "Task complete",
        "severity": "warning",
    }
