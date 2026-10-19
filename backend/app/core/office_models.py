"""Bounded inputs and durable output records for the office workflow."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.schemas import new_id


class OfficeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRow(OfficeModel):
    values: list[str | int | float | bool | None] = Field(min_length=1, max_length=30)
    source_url: str = Field(min_length=1, max_length=2048)
    quote: str = Field(min_length=1, max_length=12000)

    @field_validator("values")
    @classmethod
    def bounded_values(cls, values):
        for value in values:
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Cell numbers must be finite.")
            if isinstance(value, str) and (len(value) > 12000 or any(ord(c) < 32 and c not in "\t\n\r" for c in value)):
                raise ValueError("Cell text contains unsupported characters or is too long.")
        return values


class CreateWorkbook(OfficeModel):
    path: str = Field(min_length=1, max_length=2048)
    title: str = Field(default="网页资料整理", min_length=1, max_length=160)
    headers: list[str] = Field(min_length=1, max_length=30)
    rows: list[SourceRow] = Field(min_length=1, max_length=500)
    dry_run: bool = True

    @model_validator(mode="after")
    def rectangular(self):
        if any(not h.strip() or len(h) > 160 for h in self.headers) or len(set(self.headers)) != len(self.headers):
            raise ValueError("Column headings must be unique, nonempty and bounded.")
        if any(len(row.values) != len(self.headers) for row in self.rows):
            raise ValueError("Each row must match the column headings.")
        if len(self.model_dump_json()) > 200_000:
            raise ValueError("Workbook exceeds the bounded document size; narrow the task.")
        return self


class ReportSection(OfficeModel):
    heading: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=12000)


class CreateReport(OfficeModel):
    path: str = Field(min_length=1, max_length=2048)
    source_path: str = Field(min_length=1, max_length=2048)
    title: str = Field(default="资料汇总报告", min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=12000)
    sections: list[ReportSection] = Field(default_factory=list, max_length=20)
    dry_run: bool = True


class OfficeArtifact(OfficeModel):
    id: str = Field(default_factory=lambda: new_id("artifact"))
    kind: Literal["xlsx", "docx"]
    requested_path: str
    path: str
    title: str
    state: Literal["reserved", "writing", "verified"] = "reserved"
    content: dict = Field(default_factory=dict)
    sha256: str = ""
    source_artifact_id: str = ""
    source_sha256: str = ""
    verified_at: str = ""
    opened: bool = False


class OpenArtifact(OfficeModel):
    version: int = Field(ge=1)
