from __future__ import annotations

import asyncio
from typing import Any

from app.connectors import (
    ConnectorContext,
    ConnectorOutcome,
    ConnectorPhase,
    ConnectorRegistry,
    ConnectorRequest,
    ControlledBrowserConnector,
    DesktopNotificationConnector,
    OfficeDocumentConnector,
    SpreadsheetConnector,
    ToolInvocation,
    ToolInvocationResult,
    build_builtin_connector_registry,
)
from app.core.content_provenance import create_content_envelope, revalidate_content_envelope


class RecordingInvoker:
    def __init__(self, results: dict[str, dict[str, Any]] | None = None) -> None:
        self.calls: list[ToolInvocation] = []
        self.results = results or {}

    async def invoke(self, invocation: ToolInvocation) -> ToolInvocationResult | dict[str, Any]:
        self.calls.append(invocation)
        return self.results.get(invocation.tool_name, {"ok": True, "output": {"tool": invocation.tool_name}})


class FailingInvoker:
    async def invoke(self, invocation: ToolInvocation) -> ToolInvocationResult:
        raise RuntimeError("credential=top-secret path=D:/private/customer.csv")


def _request(
    action: str,
    parameters: dict[str, Any],
    *,
    tainted: bool = False,
) -> ConnectorRequest:
    envelopes = []
    if tainted:
        envelopes.append(
            create_content_envelope(
                "untrusted input",
                source_kind="browser",
                source_id="page-1",
                origin="https://example.test",
                trust_level="untrusted",
                taint_flags=["prompt_injection", "external_content"],
                task_scope="task-1",
            )
        )
    return ConnectorRequest(
        action=action,
        parameters=parameters,
        context=ConnectorContext(task_id="task-1", run_id="run-1", intent_capsule_id="capsule-1"),
        content_envelopes=envelopes,
    )


def test_builtin_registry_is_versioned_and_exposes_office_wps_equivalence() -> None:
    registry = build_builtin_connector_registry(RecordingInvoker())

    assert registry.list_versions("spreadsheet") == ["1.0.0"]
    assert registry.resolve("controlled_browser").descriptor.version == "1.0.0"
    office = registry.resolve("office_document").descriptor
    assert office.application_families == ["microsoft_office", "wps_office"]
    assert {item["connector_id"] for item in registry.manifest()} == {
        "controlled_browser",
        "desktop_notification",
        "office_document",
        "spreadsheet",
    }


def test_registry_resolves_latest_or_an_explicit_connector_version() -> None:
    class SpreadsheetConnectorV110(SpreadsheetConnector):
        descriptor = SpreadsheetConnector.descriptor.model_copy(update={"version": "1.1.0"})

    registry = ConnectorRegistry()
    old = SpreadsheetConnector(RecordingInvoker())
    latest = SpreadsheetConnectorV110(RecordingInvoker())
    registry.register(old)
    registry.register(latest)

    assert registry.resolve("spreadsheet") is latest
    assert registry.resolve("spreadsheet", "1.0.0") is old
    assert registry.list_versions("spreadsheet") == ["1.0.0", "1.1.0"]


def test_preview_is_pure_and_preserves_upstream_taint() -> None:
    invoker = RecordingInvoker()
    connector = SpreadsheetConnector(invoker)
    request = _request(
        "write_cell",
        {"path": "D:/work/book.xlsx", "sheet": "Sheet1", "cell": "B2", "value": "approved"},
        tainted=True,
    )

    result = asyncio.run(connector.preview(request))

    assert result.phase == ConnectorPhase.PREVIEW
    assert result.outcome == ConnectorOutcome.PREVIEWED
    assert result.output["will_execute"] is False
    assert result.planned_invocations[0].tool_name == "document.edit_xlsx"
    assert invoker.calls == []
    assert result.content_envelope.trust_level == "untrusted"
    assert result.content_envelope.taint_flags == ["external_content", "prompt_injection"]


def test_execute_routes_side_effect_only_through_injected_invoker() -> None:
    invoker = RecordingInvoker(
        {
            "document.edit_xlsx": {
                "ok": True,
                "output": {"saved": True},
                "changed_paths": ["D:/work/book.xlsx"],
                "rollback_info": {"previous_value": "old"},
            }
        }
    )
    connector = SpreadsheetConnector(invoker)
    request = _request(
        "write_cell",
        {"path": "D:/work/book.xlsx", "sheet": "Sheet1", "cell": "B2", "value": "new"},
    )

    result = asyncio.run(connector.execute(request))

    assert result.outcome == ConnectorOutcome.EXECUTED
    assert len(invoker.calls) == 1
    call = invoker.calls[0]
    assert call.tool_name == "document.edit_xlsx"
    assert call.has_side_effect is True
    assert call.runtime_context["automation_run_id"] == "run-1"
    assert call.args["dry_run"] is False
    assert result.output["changed_paths"] == ["D:/work/book.xlsx"]


def test_tainted_connector_execute_is_rejected_before_invoker_call() -> None:
    invoker = RecordingInvoker()
    connector = SpreadsheetConnector(invoker)
    request = _request(
        "write_cell",
        {"path": "D:/work/book.xlsx", "sheet": "Sheet1", "cell": "B2", "value": "external"},
        tainted=True,
    )

    result = asyncio.run(connector.execute(request))

    assert result.outcome == ConnectorOutcome.FAILED
    assert "revalidation" in result.error
    assert invoker.calls == []


def test_task_scoped_revalidation_allows_connector_execute() -> None:
    invoker = RecordingInvoker()
    connector = SpreadsheetConnector(invoker)
    request = _request(
        "write_cell",
        {"path": "D:/work/book.xlsx", "sheet": "Sheet1", "cell": "B2", "value": "external"},
        tainted=True,
    )
    request.content_envelopes = [
        revalidate_content_envelope(
            request.content_envelopes[0],
            "untrusted input",
            task_scope="task-1",
        )
    ]

    result = asyncio.run(connector.execute(request))

    assert result.outcome == ConnectorOutcome.EXECUTED
    assert len(invoker.calls) == 1


def test_invoker_exception_does_not_leak_raw_error_details() -> None:
    connector = SpreadsheetConnector(FailingInvoker())
    request = _request(
        "write_cell",
        {"path": "D:/work/book.xlsx", "sheet": "Sheet1", "cell": "B2", "value": "new"},
    )

    result = asyncio.run(connector.execute(request))

    assert result.outcome == ConnectorOutcome.FAILED
    assert "RuntimeError" in result.error
    assert "top-secret" not in result.error
    assert "customer.csv" not in result.error
    assert "governed runtime audit" in result.error


def test_browser_tool_output_and_input_taint_are_merged() -> None:
    invoker = RecordingInvoker({"browser.observe": {"ok": True, "output": {"title": "Untrusted page"}}})
    connector = ControlledBrowserConnector(invoker)

    result = asyncio.run(
        connector.observe(
            _request(
                "read",
                {"url": "https://example.test", "session_id": "session-1"},
                tainted=True,
            )
        )
    )

    assert result.outcome == ConnectorOutcome.OBSERVED
    assert result.content_envelope.trust_level == "untrusted"
    assert set(result.content_envelope.taint_flags) == {
        "external_content",
        "prompt_injection",
        "web_content",
    }


def test_mfa_captcha_payment_order_and_account_security_require_handoff() -> None:
    scenarios = [
        ("fill", {"url": "https://example.test", "fields": {"otp": "123456"}}),
        ("submit", {"url": "https://example.test/captcha", "selector": "form"}),
        ("submit", {"url": "https://shop.test/checkout", "selector": "form"}),
        ("submit_order", {"url": "https://shop.test", "selector": "form"}),
        ("change_password", {"url": "https://example.test/settings"}),
        ("scan_qr", {"url": "https://example.test/login"}),
    ]
    invoker = RecordingInvoker()
    connector = ControlledBrowserConnector(invoker)

    for action, parameters in scenarios:
        result = asyncio.run(connector.execute(_request(action, parameters)))
        assert result.phase == ConnectorPhase.HANDOFF
        assert result.outcome == ConnectorOutcome.HANDOFF_REQUIRED
        assert result.handoff is not None

    assert invoker.calls == []


def test_browser_verify_and_recover_use_allowlisted_tools_via_invoker() -> None:
    invoker = RecordingInvoker(
        {
            "browser.wait_for_selector": {"ok": True, "output": {"present": True}},
            "browser.navigate": {"ok": True, "output": {"url": "https://example.test/form"}},
        }
    )
    connector = ControlledBrowserConnector(invoker)
    request = _request(
        "submit",
        {
            "url": "https://example.test/receipt",
            "session_id": "session-1",
            "receipt_selector": "#receipt",
            "expect": {"present": True},
            "recovery_url": "https://example.test/form",
        },
    )

    verified = asyncio.run(connector.verify(request))
    recovered = asyncio.run(connector.recover(request))

    assert verified.outcome == ConnectorOutcome.VERIFIED
    assert verified.output["verified"] is True
    assert recovered.outcome == ConnectorOutcome.RECOVERED
    assert [call.tool_name for call in invoker.calls] == ["browser.wait_for_selector", "browser.navigate"]
    assert all(call.phase in {ConnectorPhase.VERIFY, ConnectorPhase.RECOVER} for call in invoker.calls)


def test_spreadsheet_recover_replays_previous_value_through_invoker() -> None:
    invoker = RecordingInvoker({"app.excel.write_cell": {"ok": True, "output": {"restored": True}}})
    connector = SpreadsheetConnector(invoker)
    request = _request(
        "write_cell",
        {
            "path": "D:/work/book.xlsx",
            "rollback_info": {
                "operation": "app.excel.write_cell",
                "path": "D:/work/book.xlsx",
                "sheet": "Sheet1",
                "cell": "A1",
                "previous_value": "before",
            },
        },
    )

    result = asyncio.run(connector.recover(request))

    assert result.outcome == ConnectorOutcome.RECOVERED
    assert invoker.calls[0].tool_name == "app.excel.write_cell"
    assert invoker.calls[0].args["value"] == "before"


def test_notification_execute_is_invoker_backed_and_receipt_can_be_verified() -> None:
    invoker = RecordingInvoker({"notification.send": {"ok": True, "output": {"queued": True}}})
    connector = DesktopNotificationConnector(invoker)

    executed = asyncio.run(
        connector.execute(_request("send", {"title": "Done", "body": "Task completed", "severity": "info"}))
    )
    verified = asyncio.run(connector.verify(_request("send", {"receipt": {"queued": True}})))

    assert executed.outcome == ConnectorOutcome.EXECUTED
    assert invoker.calls[0].tool_name == "notification.send"
    assert verified.outcome == ConnectorOutcome.VERIFIED


def test_office_connector_maps_docx_and_pptx_to_equal_semantics() -> None:
    invoker = RecordingInvoker()
    connector = OfficeDocumentConnector(invoker)

    docx = asyncio.run(
        connector.preview(
            _request(
                "template_modify",
                {"path": "D:/work/template.docx", "find": "{{name}}", "replace": "Lengrvis"},
            )
        )
    )
    pptx = asyncio.run(
        connector.preview(
            _request(
                "template_modify",
                {"path": "D:/work/template.pptx", "find": "{{name}}", "replace": "Lengrvis"},
            )
        )
    )

    assert docx.planned_invocations[0].tool_name == "document.edit_docx"
    assert pptx.planned_invocations[0].tool_name == "document.edit_pptx"
    assert invoker.calls == []
