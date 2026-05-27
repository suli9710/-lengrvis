from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services import document_intelligence_service as svc
from app.tools import document_tools
from app.tools.registry import ToolRegistry


class _StubProvider:
    name = "stub"

    def __init__(self, reply: str | None = None) -> None:
        self.reply = reply or ""
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(self, messages, model=None, temperature=None, tools=None) -> str:  # noqa: ANN001, ARG002
        self.calls.append(messages)
        return self.reply


def test_parse_advanced_txt_uses_builtin_fallback_without_heavy_deps(tmp_path: Path):
    path = tmp_path / "memo.txt"
    path.write_text("Executive summary\n\nRevenue grew 12% in Q1.", encoding="utf-8")

    ir = svc.parse_advanced(path)

    assert ir.path == str(path)
    assert ir.kind == "text"
    assert ir.parse_engine == "builtin"
    assert ir.pages
    assert len(ir.blocks) == 2
    assert "Revenue grew" in ir.text
    assert ir.document_id.startswith(("blake3:", "sha256:"))


def test_extract_tables_from_csv(tmp_path: Path):
    path = tmp_path / "sales.csv"
    path.write_text("Region,Revenue\nNorth,100\nSouth,120\n", encoding="utf-8")

    result = svc.extract_tables(path)

    assert result["tables"][0]["headers"] == ["Region", "Revenue"]
    assert result["tables"][0]["rows"][1] == ["North", "100"]


def test_extract_tables_from_xlsx(tmp_path: Path):
    from openpyxl import Workbook

    path = tmp_path / "workbook.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Plan"
    sheet.append(["Task", "Owner"])
    sheet.append(["Launch", "Ada"])
    workbook.save(path)

    result = svc.extract_tables(path)

    assert result["tables"][0]["caption"] == "Plan"
    assert result["tables"][0]["headers"] == ["Task", "Owner"]
    assert result["tables"][0]["rows"][1] == ["Launch", "Ada"]


def test_ask_with_citations_uses_extractive_fallback(tmp_path: Path):
    path = tmp_path / "policy.md"
    path.write_text(
        "Payment terms\n\nInvoices must be paid within 30 days after receipt.\n\nTermination requires notice.",
        encoding="utf-8",
    )

    result = svc.ask_with_citations(path, "When are invoices paid?", provider_resolver=lambda task="subagent": None)

    assert result["note"] == "extractive_fallback"
    assert "30 days" in result["answer"]
    assert result["citations"]
    assert result["source_blocks"][0]["citation"].startswith("[p1:b")


def test_ask_with_citations_uses_provider_when_available(tmp_path: Path):
    path = tmp_path / "policy.txt"
    path.write_text("Support responses must include citations for source-backed claims.", encoding="utf-8")
    provider = _StubProvider("Use citations for source-backed claims. [p1:b1]")

    result = svc.ask_with_citations(path, "What must support responses include?", provider_resolver=lambda task="subagent": provider)

    assert result["note"] == "llm_qa"
    assert "[p1:b1]" in result["answer"]
    assert provider.calls
    assert "Source blocks" in provider.calls[0][-1]["content"]


def test_compare_documents_reports_added_removed_changed(tmp_path: Path):
    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    old.write_text("Intro\n\nKeep this paragraph.\n\nRemove this paragraph.", encoding="utf-8")
    new.write_text("Intro updated\n\nKeep this paragraph.\n\nAdd this paragraph.", encoding="utf-8")

    result = svc.compare_documents(old, new)

    assert result["changed"][0]["from"]["text"] == "Intro"
    assert result["changed"][0]["to"]["text"] == "Intro updated"
    assert any(item["text"] == "Remove this paragraph." for item in result["removed"])
    assert any(item["text"] == "Add this paragraph." for item in result["added"])


def test_redact_preview_returns_preview_without_writing_file(tmp_path: Path):
    path = tmp_path / "contacts.txt"
    path.write_text("Email ada@example.com or call 212-555-0199.", encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    result = svc.redact_preview(path)

    assert "[REDACTED:EMAIL]" in result["redacted_text"]
    assert "[REDACTED:PHONE]" in result["redacted_text"]
    assert path.read_text(encoding="utf-8") == before


def test_generate_cited_report_fallback_contains_citations(tmp_path: Path):
    path = tmp_path / "brief.txt"
    path.write_text("Market demand increased in the enterprise segment.\n\nCosts remained flat.", encoding="utf-8")

    result = svc.generate_cited_report(path, title="Market Brief", provider_resolver=lambda task="subagent": None)

    assert result["note"] == "extractive_fallback"
    assert result["report"].startswith("# Market Brief")
    assert "[p1:b1]" in result["report"]


def test_document_intelligence_tools_are_registered_readonly():
    registry = ToolRegistry()
    document_tools.register(registry)

    for name in {
        "document.parse_advanced",
        "document.extract_tables",
        "document.ask_with_citations",
        "document.compare",
        "document.redact_preview",
        "document.generate_cited_report",
    }:
        tool = registry.get(name)
        assert tool.risk_level.value == "R0_READ_ONLY"
        assert tool.is_read_only() is True
        assert tool.requires_authorized_path is True
        assert tool.fast_path_eligible is True


def test_document_tool_parse_advanced_authorizes_path(tmp_path: Path):
    path = tmp_path / "memo.txt"
    path.write_text("Authorized document text", encoding="utf-8")
    context = {"allowed_directories": [str(tmp_path)]}

    result = document_tools.parse_advanced({"path": str(path)}, context)

    assert result["kind"] == "text"
    assert result["blocks"][0]["text"] == "Authorized document text"
