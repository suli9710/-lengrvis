from __future__ import annotations

from app.context.message_text import content_text, json_text, preview_text, single_line


def test_content_text_flattens_text_and_nested_tool_results():
    content = [
        {"type": "text", "text": "first"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        {"type": "tool_result", "content": [{"type": "text", "text": "nested"}, "plain"]},
        42,
    ]

    assert content_text(content) == "first\nnested\nplain\n42"
    assert content_text(None) == ""


def test_preview_text_preserves_head_tail_and_original_length():
    preview = preview_text("abcdefghijklmnopqrstuvwxyz", 10)

    assert preview.startswith("abcde\n")
    assert preview.endswith("\nvwxyz")
    assert "original 26 chars" in preview


def test_single_line_and_json_text_are_stable_for_prompt_metadata():
    assert single_line(" a\n  b\tc ") == "a b c"
    assert json_text({"b": 2, "a": "一"}) == '{"a": "一", "b": 2}'
