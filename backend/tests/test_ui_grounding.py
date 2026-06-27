"""Computer Use 视觉 grounding 回退链测试（UIA → 截图 → 视觉模型 → 坐标）。"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from app.tools import ui_automation_tools


class _FakeElement:
    def to_dict(self) -> dict[str, Any]:
        return {"name": "Save", "rect": {"x": 100, "y": 200, "width": 40, "height": 20}}


class _FakeTarget:
    def __init__(self, *, element: Any | None, screenshot: dict[str, Any] | None = None):
        self._element = element
        self._screenshot = screenshot or {}

    async def find_element(self, selector):  # noqa: ANN001, ARG002
        return self._element

    async def screenshot(self, **kwargs):  # noqa: ANN003, ARG002
        return self._screenshot


def _fake_screenshot_payload() -> dict[str, Any]:
    pixel = base64.b64encode(b"\xff\xd8\xff\xdbfakejpegdata").decode("ascii")
    return {
        "ok": True,
        "image": f"data:image/jpeg;base64,{pixel}",
        "width": 800,
        "height": 500,
        "original_width": 1920,
        "original_height": 1080,
    }


def test_semantic_uia_hit_returns_center_coordinates(monkeypatch):
    monkeypatch.setattr(
        ui_automation_tools,
        "create_ui_automation_target",
        lambda policy_engine=None, approval_context=None: _FakeTarget(element=_FakeElement()),  # noqa: ARG005
    )

    result = ui_automation_tools.locate_on_screen({"name": "Save"}, {})

    assert result["ok"] is True
    assert result["method"] == "uia"
    assert (result["x"], result["y"]) == (120, 210)
    assert result["confidence"] == 1.0


def test_vision_fallback_scales_ratios_to_screen_coordinates(monkeypatch):
    monkeypatch.setattr(
        ui_automation_tools,
        "create_ui_automation_target",
        lambda policy_engine=None, approval_context=None: _FakeTarget(  # noqa: ARG005
            element=None,
            screenshot=_fake_screenshot_payload(),
        ),
    )
    monkeypatch.setattr(
        "app.tools.vision_tools._run_vision",
        lambda prompt, image_path, task="vision": (
            '{"found": true, "x_ratio": 0.5, "y_ratio": 0.25, "confidence": 0.9, "label": "Save button"}'
        ),
    )

    result = ui_automation_tools.locate_on_screen({"name": "Save", "target": "the Save button"}, {})

    assert result["ok"] is True
    assert result["method"] == "vision"
    assert (result["x"], result["y"]) == (960, 270)
    assert result["confidence"] == 0.9
    assert result["label"] == "Save button"


def test_vision_fallback_not_found_is_actionable(monkeypatch):
    monkeypatch.setattr(
        ui_automation_tools,
        "create_ui_automation_target",
        lambda policy_engine=None, approval_context=None: _FakeTarget(  # noqa: ARG005
            element=None,
            screenshot=_fake_screenshot_payload(),
        ),
    )
    monkeypatch.setattr(
        "app.tools.vision_tools._run_vision",
        lambda prompt, image_path, task="vision": '{"found": false, "confidence": 0.0, "label": ""}',
    )

    result = ui_automation_tools.locate_on_screen({"target": "a button that does not exist"}, {})

    assert result["ok"] is False
    assert "could not find" in result["error"]


def test_vision_fallback_rejects_oversized_screenshot(monkeypatch):
    monkeypatch.setattr(
        ui_automation_tools,
        "create_ui_automation_target",
        lambda policy_engine=None, approval_context=None: _FakeTarget(  # noqa: ARG005
            element=None,
            screenshot=_fake_screenshot_payload(),
        ),
    )
    monkeypatch.setattr(ui_automation_tools, "MAX_VISION_GROUNDING_IMAGE_BYTES", 4)
    monkeypatch.setattr(ui_automation_tools, "MAX_VISION_GROUNDING_IMAGE_BASE64_CHARS", 8)

    result = ui_automation_tools.locate_on_screen({"target": "the Save button"}, {})

    assert result["ok"] is False
    assert "exceeds" in result["error"]


def test_missing_selector_and_description_returns_guidance(monkeypatch):
    monkeypatch.setattr(
        ui_automation_tools,
        "create_ui_automation_target",
        lambda policy_engine=None, approval_context=None: _FakeTarget(element=None),  # noqa: ARG005
    )

    result = ui_automation_tools.locate_on_screen({}, {})

    assert result["ok"] is False
    assert "target" in result["error"]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ('{"found": true, "x_ratio": 0.1, "y_ratio": 0.2, "confidence": 0.8}', True),
        ('Sure! Here it is: {"found": true, "x_ratio": 0.3, "y_ratio": 0.4, "confidence": 0.7}', True),
        ("[vision unavailable: no provider]", False),
        ("no json at all", False),
    ],
)
def test_parse_grounding_answer(answer, expected):
    parsed = ui_automation_tools._parse_grounding_answer(answer)
    assert (parsed is not None) is expected


def test_rect_center_handles_bad_payload():
    assert ui_automation_tools._rect_center({}) is None
    assert ui_automation_tools._rect_center({"x": 10, "y": 20, "width": 10, "height": 10}) == (15, 25)
