from __future__ import annotations

from app.adapters.base import AdapterBase, AdapterConfig, AdapterResult
from app.adapters.calendar import CalendarAdapter
from app.adapters.email import EmailAdapter
from app.adapters.webhook import WebhookAdapter

__all__ = [
    "AdapterBase",
    "AdapterConfig",
    "AdapterResult",
    "CalendarAdapter",
    "EmailAdapter",
    "WebhookAdapter",
]
