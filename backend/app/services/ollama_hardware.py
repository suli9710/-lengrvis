"""Hardware readiness assessment helpers for Ollama setup."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from app.services.ollama_setup_presenter import setup_repair_action

RECOMMENDED_MODEL = "qwen2.5:3b"
FALLBACK_SMALL_MODEL = RECOMMENDED_MODEL
FALLBACK_MEDIUM_MODEL = "qwen2.5:7b"

GIB = 1024**3
MIN_CPU_CORES = 4
MIN_RAM_BYTES = 8 * GIB
MIN_DISK_BYTES = 8 * GIB
MEDIUM_CPU_CORES = 6
MEDIUM_RAM_BYTES = 16 * GIB
MEDIUM_DISK_BYTES = 12 * GIB


class RecommendedModelSelector(Protocol):
    def __call__(
        self,
        *,
        memory_total_bytes: int,
        disk_free_bytes: int,
        cpu_logical_cores: int,
    ) -> str: ...


RequirementsProvider = Callable[[str], dict[str, int]]
RepairActionProvider = Callable[[str, str], dict[str, str]]
ByteFormatter = Callable[[int], str]


def assess_hardware(
    *,
    model: str | None = None,
    memory_total_bytes: int = 0,
    disk_free_bytes: int = 0,
    cpu_logical_cores: int = 0,
    gpu_summary: str = "",
    recommended_model_for_hardware_fn: RecommendedModelSelector | None = None,
    requirements_for_model_fn: RequirementsProvider | None = None,
    repair_action_fn: RepairActionProvider = setup_repair_action,
    format_bytes_fn: ByteFormatter | None = None,
) -> dict[str, Any]:
    """Return the pure local-model hardware gate result for supplied resources."""
    recommended_model_for_hardware_fn = recommended_model_for_hardware_fn or recommended_model_for_hardware
    requirements_for_model_fn = requirements_for_model_fn or requirements_for_model
    format_bytes_fn = format_bytes_fn or format_bytes
    target = model or recommended_model_for_hardware_fn(
        memory_total_bytes=memory_total_bytes,
        disk_free_bytes=disk_free_bytes,
        cpu_logical_cores=cpu_logical_cores,
    )
    requirements = requirements_for_model_fn(target)
    checks = [
        {
            "key": "memory",
            "label": "Memory",
            "ok": memory_total_bytes >= requirements["memory_total_bytes"],
            "actual": format_bytes_fn(memory_total_bytes),
            "required": format_bytes_fn(requirements["memory_total_bytes"]),
        },
        {
            "key": "disk",
            "label": "Free disk space",
            "ok": disk_free_bytes >= requirements["disk_free_bytes"],
            "actual": format_bytes_fn(disk_free_bytes),
            "required": format_bytes_fn(requirements["disk_free_bytes"]),
        },
        {
            "key": "cpu",
            "label": "CPU cores",
            "ok": cpu_logical_cores >= requirements["cpu_logical_cores"],
            "actual": str(cpu_logical_cores or "unknown"),
            "required": str(requirements["cpu_logical_cores"]),
        },
    ]
    can_install = all(check["ok"] for check in checks)
    failed = [check for check in checks if not check["ok"]]
    reason = (
        f"This computer is ready for {target}."
        if can_install
        else "Local AI setup needs " + ", ".join(f"{item['label']} >= {item['required']}" for item in failed) + "."
    )
    next_action = "continue_setup" if can_install else "hardware_blocked"
    return {
        "can_install": can_install,
        "recommended_model": target,
        "reason": reason,
        "checks": checks,
        "next_action": next_action,
        "repair_action": repair_action_fn(next_action, target),
        "memory_total_bytes": memory_total_bytes,
        "disk_free_bytes": disk_free_bytes,
        "cpu_logical_cores": cpu_logical_cores,
        "gpu_summary": gpu_summary,
    }


def requirements_for_model(model: str) -> dict[str, int]:
    normalized = model.lower()
    if "7b" in normalized:
        return {
            "memory_total_bytes": MEDIUM_RAM_BYTES,
            "disk_free_bytes": MEDIUM_DISK_BYTES,
            "cpu_logical_cores": MEDIUM_CPU_CORES,
        }
    return {
        "memory_total_bytes": MIN_RAM_BYTES,
        "disk_free_bytes": MIN_DISK_BYTES,
        "cpu_logical_cores": MIN_CPU_CORES,
    }


def recommended_model_for_hardware(
    *,
    memory_total_bytes: int,
    disk_free_bytes: int,
    cpu_logical_cores: int,
) -> str:
    if (
        memory_total_bytes >= MEDIUM_RAM_BYTES
        and disk_free_bytes >= MEDIUM_DISK_BYTES
        and cpu_logical_cores >= MEDIUM_CPU_CORES
    ):
        return FALLBACK_MEDIUM_MODEL
    return FALLBACK_SMALL_MODEL


def format_bytes(value: int) -> str:
    if value <= 0:
        return "unknown"
    gib = value / GIB
    return f"{gib:.1f} GB"
