"""Document source scope must be checked before parsing or provider access."""

from unittest.mock import Mock

import pytest

from app.core.errors import SecurityError
from app.tools import document_tools


@pytest.mark.parametrize("tool_name", ["extract_text", "qa", "ask_with_citations"])
@pytest.mark.parametrize("scope", ["empty", "outside"])
def test_document_source_outside_scope_never_reaches_parser_or_provider(monkeypatch, tmp_path, tool_name, scope):
    monkeypatch.setenv("LENGRVIS_PLAN", "pro")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    source = tmp_path / "private.txt"
    source.write_text("PRIVATE_DOCUMENT_MUST_NOT_BE_PARSED", encoding="utf-8")
    parse = Mock(side_effect=AssertionError("unauthorized document parsed"))
    provider = Mock(side_effect=AssertionError("unauthorized provider accessed"))
    cited = Mock(side_effect=AssertionError("unauthorized cited document parsed"))
    monkeypatch.setattr(document_tools, "extract_text_from_path", parse)
    monkeypatch.setattr(document_tools, "_document_provider", provider)
    monkeypatch.setattr(document_tools.document_intelligence_service, "ask_with_citations", cited)
    context = {"allowed_directories": [] if scope == "empty" else [str(allowed)]}

    with pytest.raises(SecurityError):
        getattr(document_tools, tool_name)({"path": str(source), "question": "What does this say?"}, context)

    parse.assert_not_called()
    provider.assert_not_called()
    cited.assert_not_called()
    assert source.read_text(encoding="utf-8") == "PRIVATE_DOCUMENT_MUST_NOT_BE_PARSED"
