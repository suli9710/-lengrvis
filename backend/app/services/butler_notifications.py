"""Persist a batch and consume its signed pending entries in one transaction."""

from datetime import UTC, datetime

from app.core import butler_store as store
from app.core import db
from app.core.butler_models import ButlerMatter
from app.core.local_data_lifecycle import tracks_local_data
from app.core.schemas import AgentMessage, MessageType


@tracks_local_data
def flush_notifications(*, force: bool = False, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    db.init_db()
    with db.connect() as conn:
        db._begin_immediate_transaction(conn)
        pending = [item for item in store.list_records(ButlerMatter, limit=None) if item.pending_notifications]
        if not pending:
            return
        oldest = min(
            datetime.fromisoformat(value["created_at"])
            for item in pending
            for value in item.pending_notifications.values()
        )
        if not force and (now - oldest).total_seconds() < 60:
            return
        lines = []
        identities = []
        for item in pending:
            messages = list(dict.fromkeys(value["message"] for value in item.pending_notifications.values()))
            identities.extend(f"{item.id}:{key}" for key in item.pending_notifications)
            store.mutate(
                ButlerMatter,
                item.id,
                lambda record: record.pending_notifications.clear(),
                expected_version=item.version,
            )
            lines.append(f"{item.goal[:120]}：{'；'.join(messages)[:500]}")
        body = "\n".join(lines[:20])
        if len(lines) > 20:
            body += f"\n另有 {len(lines) - 20} 项变化，请查看管家概览。"
        message = AgentMessage(
            id="msg_butler_" + store.source_key(sorted(identities)),
            task_id="__system__",
            from_agent="NotificationService",
            message_type=MessageType.NOTIFICATION,
            content=body,
            structured_payload={"title": "管家事项汇总", "body": body, "severity": "info"},
        )
        db.upsert_model("agent_messages", message)
    # The persisted inbox and live event share one ID, even across a restart.
    from app.services.notification_service import _get_bus

    _get_bus().publish(message)
