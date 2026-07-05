from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DocumentBlock:
    id: str
    text: str
    kind: str = "paragraph"
    page: int | None = 1
    index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def citation(self) -> str:
        if self.page is None:
            return f"[block {self.index + 1}]"
        return f"[p{self.page}:b{self.index + 1}]"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "kind": self.kind,
            "page": self.page,
            "index": self.index,
            "citation": self.citation,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class DocumentTable:
    id: str
    rows: list[list[str]]
    headers: list[str] = field(default_factory=list)
    page: int | None = 1
    caption: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "headers": list(self.headers),
            "rows": [list(row) for row in self.rows],
            "page": self.page,
            "caption": self.caption,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class DocumentIR:
    document_id: str
    path: str
    kind: str
    pages: list[dict[str, Any]]
    blocks: list[DocumentBlock]
    tables: list[DocumentTable]
    metadata: dict[str, Any]
    parse_engine: str
    ocr_engine: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks if block.text)

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "path": self.path,
            "kind": self.kind,
            "pages": list(self.pages),
            "blocks": [block.as_dict() for block in self.blocks],
            "tables": [table.as_dict() for table in self.tables],
            "metadata": dict(self.metadata),
            "parse_engine": self.parse_engine,
            "ocr_engine": self.ocr_engine,
            "warnings": list(self.warnings),
        }


ProviderResolver = Callable[[str], Any]
