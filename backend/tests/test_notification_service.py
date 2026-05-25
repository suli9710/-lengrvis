from unittest.mock import MagicMock, patch
from app.services import notification_service


def test_notify_publishes_to_bus():
    bus = MagicMock()
    notification_service.init_bus(bus)
    result = notification_service.notify("Test Title", "Test body", severity="info")
    assert result["queued"] is True
    assert result["title"] == "Test Title"
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
