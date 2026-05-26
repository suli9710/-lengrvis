from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


AdapterResult = dict[str, Any]


@dataclass(slots=True)
class AdapterConfig:
    service_name: str
    enabled: bool = True
    dry_run: bool = True
    test_mode: bool = False
    base_url: str = ""
    timeout_seconds: float = 10.0
    default_sender: str = ""
    credentials: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class AdapterBase(ABC):
    def __init__(self, config: AdapterConfig) -> None:
        self.config = config
        self._connected = False

    @abstractmethod
    def connect(self) -> AdapterResult:
        """Prepare the adapter for execution."""

    @abstractmethod
    def execute(self, operation: str, payload: dict[str, Any]) -> AdapterResult:
        """Execute an adapter operation."""

    @abstractmethod
    def health_check(self) -> AdapterResult:
        """Return adapter health without mutating the external service."""

    def _dry_run_enabled(self, payload: dict[str, Any]) -> bool:
        if "dry_run" in payload:
            return bool(payload["dry_run"])
        return bool(self.config.dry_run or self.config.test_mode)

    def _disabled_result(self) -> AdapterResult | None:
        if self.config.enabled:
            return None
        return {
            "ok": False,
            "adapter": self.config.service_name,
            "error": f"{self.config.service_name} adapter is disabled.",
        }

    def _ensure_connected(self) -> AdapterResult | None:
        disabled = self._disabled_result()
        if disabled is not None:
            return disabled
        if not self._connected:
            result = self.connect()
            if not result.get("ok"):
                return result
        return None
