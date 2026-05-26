from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


CommandKind = Literal["diagnostic", "action", "workflow"]


class CommandDefinition(BaseModel):
    name: str
    title: str = ""
    description: str = ""
    summary: str = ""
    kind: CommandKind = "diagnostic"
    category: str = ""
    surface: str = "shared"
    aliases: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    related_routes: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    status: str = "available"
    next_action: str = ""

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("Command name is required.")
        return text if text.startswith("/") else f"/{text}"

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, value: list[str]) -> list[str]:
        aliases: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if not text:
                continue
            aliases.append(text if text.startswith("/") else f"/{text}")
        return aliases

    @model_validator(mode="after")
    def fill_public_labels(self) -> "CommandDefinition":
        if not self.title:
            self.title = self.name
        if not self.description:
            self.description = self.summary or self.title
        if not self.summary:
            self.summary = self.description
        if not self.category:
            self.category = self.kind
        return self


class CommandExecuteRequest(BaseModel):
    command: str = ""
    name: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    surface: str = "shared"

    @model_validator(mode="before")
    @classmethod
    def accept_name_alias(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if not normalized.get("command") and normalized.get("name"):
            normalized["command"] = normalized["name"]
        return normalized

    @field_validator("command")
    @classmethod
    def normalize_command(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("Command is required.")
        return text if text.startswith("/") else f"/{text}"


class CommandResult(BaseModel):
    ok: bool = True
    command: str
    title: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    diagnostics: list[str] = Field(default_factory=list)
    next_action: str = ""
    delegated_to: str = ""
    error: str = ""
    surface: str = "shared"
