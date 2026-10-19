from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StepInputBinding(BaseModel):
    """A literal JSON path into the output of a successful dependency."""

    model_config = ConfigDict(extra="forbid", strict=True)

    step_id: str = Field(min_length=1)
    path: list[str | int] = Field(min_length=1, max_length=16)


def resolve_inputs(
    args: dict[str, Any],
    bindings: dict[str, StepInputBinding],
    dependencies: list[str],
    results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    resolved = deepcopy(args)
    for name, binding in bindings.items():
        source = results.get(binding.step_id)
        if binding.step_id not in dependencies or not source or source.get("ok") is not True:
            raise ValueError(f"Input {name} requires a successful declared dependency.")
        value: Any = source
        for key in binding.path:
            if isinstance(value, dict) and isinstance(key, str) and key in value:
                value = value[key]
            elif isinstance(value, list) and type(key) is int and 0 <= key < len(value):
                value = value[key]
            else:
                raise ValueError(f"Input {name} references a missing dependency output field.")
        resolved[name] = deepcopy(value)
    return resolved
