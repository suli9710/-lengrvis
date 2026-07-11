from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from app.connectors.contracts import (
    ConnectorDescriptor,
    ConnectorHandoff,
    ConnectorOutcome,
    ConnectorPhase,
    ConnectorRequest,
    ConnectorResult,
    ToolInvocation,
    ToolInvocationResult,
    ToolInvoker,
)
from app.core.content_provenance import (
    ContentRevalidationRequired,
    assert_content_revalidated,
    content_envelope_for_tool_output,
    create_content_envelope,
    merge_content_envelopes,
)
from app.core.schemas import ContentEnvelope

_SENSITIVE_ACTION_MARKERS: dict[str, tuple[str, ...]] = {
    "mfa": ("mfa", "2fa", "otp", "one_time_password", "verification_code", "二次验证", "动态口令"),
    "captcha": ("captcha", "recaptcha", "验证码", "图形验证"),
    "qr_scan": ("qr_scan", "scan_qr", "扫码", "二维码"),
    "payment": (
        "payment",
        "pay",
        "checkout",
        "card_number",
        "credit_card",
        "cvv",
        "billing",
        "支付",
        "付款",
        "结算",
    ),
    "order": (
        "place_order",
        "submit_order",
        "confirm_order",
        "create_order",
        "/order",
        "order/",
        "purchase",
        "下单",
        "购买",
    ),
    "account_security": (
        "account_security",
        "change_password",
        "reset_password",
        "security_setting",
        "账户安全",
        "修改密码",
        "重置密码",
    ),
}
_CATEGORICAL_PARAMETER_KEYS = {
    "action",
    "action_type",
    "challenge",
    "challenge_type",
    "field",
    "field_name",
    "kind",
    "operation",
    "operation_type",
    "risk_category",
    "selector",
    "type",
    "url",
}


class Connector(ABC):
    @property
    @abstractmethod
    def descriptor(self) -> ConnectorDescriptor: ...

    @abstractmethod
    async def probe(self, request: ConnectorRequest) -> ConnectorResult: ...

    @abstractmethod
    async def observe(self, request: ConnectorRequest) -> ConnectorResult: ...

    @abstractmethod
    async def preview(self, request: ConnectorRequest) -> ConnectorResult: ...

    @abstractmethod
    async def execute(self, request: ConnectorRequest) -> ConnectorResult: ...

    @abstractmethod
    async def verify(self, request: ConnectorRequest) -> ConnectorResult: ...

    @abstractmethod
    async def recover(self, request: ConnectorRequest) -> ConnectorResult: ...

    @abstractmethod
    async def handoff(
        self,
        request: ConnectorRequest,
        *,
        reason_code: str,
        message: str,
        guidance: list[str] | None = None,
    ) -> ConnectorResult: ...


class ToolBackedConnector(Connector):
    def __init__(self, invoker: ToolInvoker) -> None:
        self._invoker = invoker

    async def probe(self, request: ConnectorRequest) -> ConnectorResult:
        invocation = self._build_probe_invocation(request)
        if invocation is None:
            return self._result(
                request,
                phase=ConnectorPhase.PROBE,
                outcome=ConnectorOutcome.READY,
                ok=True,
                output={"available": True, "descriptor": self.descriptor.model_dump(mode="json")},
            )
        invoked = await self._invoke(invocation)
        return self._invoked_result(
            request,
            invocation,
            invoked,
            phase=ConnectorPhase.PROBE,
            success_outcome=ConnectorOutcome.READY,
        )

    async def observe(self, request: ConnectorRequest) -> ConnectorResult:
        invocation = self._build_observe_invocation(request)
        if invocation is None:
            return self._unsupported(request, ConnectorPhase.OBSERVE)
        invoked = await self._invoke(invocation)
        return self._invoked_result(
            request,
            invocation,
            invoked,
            phase=ConnectorPhase.OBSERVE,
            success_outcome=ConnectorOutcome.OBSERVED,
        )

    async def preview(self, request: ConnectorRequest) -> ConnectorResult:
        sensitive = self._sensitive_handoff(request)
        if sensitive is not None:
            return await self.handoff(request, **sensitive)
        invocation = self._build_execute_invocation(request, phase=ConnectorPhase.EXECUTE)
        if invocation is None:
            return self._unsupported(request, ConnectorPhase.PREVIEW)
        return self._result(
            request,
            phase=ConnectorPhase.PREVIEW,
            outcome=ConnectorOutcome.PREVIEWED,
            ok=True,
            output={
                "action": request.action,
                "tool_name": invocation.tool_name,
                "args": invocation.args,
                "will_execute": False,
            },
            planned_invocations=[invocation],
        )

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        sensitive = self._sensitive_handoff(request)
        if sensitive is not None:
            return await self.handoff(request, **sensitive)
        invocation = self._build_execute_invocation(request, phase=ConnectorPhase.EXECUTE)
        if invocation is None:
            return self._unsupported(request, ConnectorPhase.EXECUTE)
        invoked = await self._invoke(invocation)
        return self._invoked_result(
            request,
            invocation,
            invoked,
            phase=ConnectorPhase.EXECUTE,
            success_outcome=ConnectorOutcome.EXECUTED,
        )

    async def verify(self, request: ConnectorRequest) -> ConnectorResult:
        invocation = self._build_verify_invocation(request)
        if invocation is None:
            return self._unsupported(request, ConnectorPhase.VERIFY)
        invoked = await self._invoke(invocation)
        verified = invoked.ok and self._verification_passed(request, invoked.output)
        output = {**invoked.output, "verified": verified}
        normalized = invoked.model_copy(update={"ok": verified, "output": output})
        if invoked.ok and not verified:
            normalized.error = "Postcondition verification did not match the expected result."
        return self._invoked_result(
            request,
            invocation,
            normalized,
            phase=ConnectorPhase.VERIFY,
            success_outcome=ConnectorOutcome.VERIFIED,
        )

    async def recover(self, request: ConnectorRequest) -> ConnectorResult:
        invocation = self._build_recover_invocation(request)
        if invocation is None:
            return await self.handoff(
                request,
                reason_code="recovery_requires_user",
                message="This connector cannot safely reverse the last action automatically.",
                guidance=["Review the execution receipt and choose a corrective action."],
            )
        invoked = await self._invoke(invocation)
        return self._invoked_result(
            request,
            invocation,
            invoked,
            phase=ConnectorPhase.RECOVER,
            success_outcome=ConnectorOutcome.RECOVERED,
        )

    async def handoff(
        self,
        request: ConnectorRequest,
        *,
        reason_code: str,
        message: str,
        guidance: list[str] | None = None,
    ) -> ConnectorResult:
        handoff = ConnectorHandoff(
            reason_code=reason_code,
            message=message,
            guidance=guidance or ["Complete this step manually, then resume observation."],
        )
        return self._result(
            request,
            phase=ConnectorPhase.HANDOFF,
            outcome=ConnectorOutcome.HANDOFF_REQUIRED,
            ok=False,
            output={"action": request.action, "reason_code": reason_code},
            handoff=handoff,
            error=message,
        )

    async def _invoke(self, invocation: ToolInvocation) -> ToolInvocationResult:
        if invocation.input_envelopes and _invocation_requires_content_revalidation(invocation):
            try:
                assert_content_revalidated(
                    invocation.input_envelopes,
                    task_scopes={
                        invocation.task_scope,
                        str(invocation.runtime_context.get("automation_run_id") or ""),
                    },
                    boundary=f"{invocation.connector_id} {invocation.phase.value}",
                )
            except ContentRevalidationRequired as exc:
                return ToolInvocationResult(ok=False, error=str(exc))
        try:
            raw = await self._invoker.invoke(invocation)
            return ToolInvocationResult.from_value(raw)
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary; the invoker is an execution boundary.
            return ToolInvocationResult(
                ok=False,
                error=f"Tool invocation failed safely ({type(exc).__name__}). See the governed runtime audit.",
            )

    def _invoked_result(
        self,
        request: ConnectorRequest,
        invocation: ToolInvocation,
        invoked: ToolInvocationResult,
        *,
        phase: ConnectorPhase,
        success_outcome: ConnectorOutcome,
    ) -> ConnectorResult:
        invoked = ensure_tool_envelope(self, request, invocation, invoked)
        output = dict(invoked.output)
        if invoked.changed_paths:
            output.setdefault("changed_paths", invoked.changed_paths)
        if invoked.rollback_info:
            output.setdefault("rollback_info", invoked.rollback_info)
        return self._result(
            request,
            phase=phase,
            outcome=success_outcome if invoked.ok else ConnectorOutcome.FAILED,
            ok=invoked.ok,
            output=output,
            tool_envelope=invoked.content_envelope,
            executed_invocations=[invocation],
            error=invoked.error,
        )

    def _result(
        self,
        request: ConnectorRequest,
        *,
        phase: ConnectorPhase,
        outcome: ConnectorOutcome,
        ok: bool,
        output: dict[str, Any],
        tool_envelope: ContentEnvelope | None = None,
        planned_invocations: list[ToolInvocation] | None = None,
        executed_invocations: list[ToolInvocation] | None = None,
        handoff: ConnectorHandoff | None = None,
        error: str = "",
    ) -> ConnectorResult:
        parents = list(request.content_envelopes)
        if tool_envelope is not None:
            parents.append(tool_envelope)
        if parents:
            envelope = merge_content_envelopes(
                parents,
                output,
                source_kind="connector",
                source_id=f"{self.descriptor.connector_id}:{phase.value}",
                origin=f"{self.descriptor.connector_id}@{self.descriptor.version}",
                task_scope=request.context.task_id,
            )
        else:
            envelope = create_content_envelope(
                output,
                source_kind="connector",
                source_id=f"{self.descriptor.connector_id}:{phase.value}",
                origin=f"{self.descriptor.connector_id}@{self.descriptor.version}",
                trust_level="internal",
                task_scope=request.context.task_id,
            )
        return ConnectorResult(
            connector_id=self.descriptor.connector_id,
            connector_version=self.descriptor.version,
            phase=phase,
            outcome=outcome,
            ok=ok,
            output=output,
            content_envelope=envelope,
            planned_invocations=planned_invocations or [],
            executed_invocations=executed_invocations or [],
            handoff=handoff,
            error=error,
        )

    def _unsupported(self, request: ConnectorRequest, phase: ConnectorPhase) -> ConnectorResult:
        return self._result(
            request,
            phase=phase,
            outcome=ConnectorOutcome.UNSUPPORTED,
            ok=False,
            output={"action": request.action},
            error=f"{self.descriptor.connector_id} does not support {request.action!r} during {phase.value}.",
        )

    def _tool_invocation(
        self,
        request: ConnectorRequest,
        *,
        phase: ConnectorPhase,
        tool_name: str,
        args: Mapping[str, Any],
        has_side_effect: bool,
    ) -> ToolInvocation:
        return ToolInvocation(
            connector_id=self.descriptor.connector_id,
            connector_version=self.descriptor.version,
            phase=phase,
            tool_name=tool_name,
            args=dict(args),
            runtime_context=request.context.runtime_context(),
            task_scope=request.context.task_id,
            has_side_effect=has_side_effect,
            input_envelopes=request.content_envelopes,
        )

    def _tool_result_envelope(
        self,
        request: ConnectorRequest,
        invocation: ToolInvocation,
        output: dict[str, Any],
    ) -> ContentEnvelope:
        return content_envelope_for_tool_output(
            invocation.tool_name,
            output,
            tool_call_id=invocation.id,
            task_scope=request.context.task_id,
            external_network=invocation.tool_name.startswith("browser."),
        )

    def _sensitive_handoff(self, request: ConnectorRequest) -> dict[str, Any] | None:
        candidates = [request.action.casefold()]
        for key, value in _walk_parameters(request.parameters):
            normalized_key = key.casefold()
            candidates.append(normalized_key)
            if normalized_key in _CATEGORICAL_PARAMETER_KEYS or value is True:
                candidates.append(str(value).casefold())
        haystack = " ".join(candidates)
        for reason_code, markers in _SENSITIVE_ACTION_MARKERS.items():
            if any(marker in haystack for marker in markers):
                return {
                    "reason_code": reason_code,
                    "message": (
                        "MFA, captcha, QR scan, payment, order, and account-security steps require user control."
                    ),
                    "guidance": ["Complete the sensitive step in the visible application, then resume the task."],
                }
        return None

    def _verification_passed(self, request: ConnectorRequest, output: dict[str, Any]) -> bool:
        expected = request.parameters.get("expect")
        if not isinstance(expected, Mapping) or not expected:
            return True
        return all(output.get(str(key)) == value for key, value in expected.items())

    def _build_probe_invocation(self, request: ConnectorRequest) -> ToolInvocation | None:
        return None

    def _build_observe_invocation(self, request: ConnectorRequest) -> ToolInvocation | None:
        return None

    def _build_execute_invocation(
        self,
        request: ConnectorRequest,
        *,
        phase: ConnectorPhase,
    ) -> ToolInvocation | None:
        return None

    def _build_verify_invocation(self, request: ConnectorRequest) -> ToolInvocation | None:
        return self._build_observe_invocation(request)

    def _build_recover_invocation(self, request: ConnectorRequest) -> ToolInvocation | None:
        return None


def ensure_tool_envelope(
    connector: ToolBackedConnector,
    request: ConnectorRequest,
    invocation: ToolInvocation,
    invoked: ToolInvocationResult,
) -> ToolInvocationResult:
    if invoked.content_envelope is not None:
        return invoked
    return invoked.model_copy(
        update={"content_envelope": connector._tool_result_envelope(request, invocation, invoked.output)}
    )


def _invocation_requires_content_revalidation(invocation: ToolInvocation) -> bool:
    name = invocation.tool_name.casefold()
    return invocation.has_side_effect or name.startswith("mcp.") or "credential" in name


def _walk_parameters(value: Mapping[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, Mapping):
            items.extend(_walk_parameters(item, path))
        else:
            items.append((str(key), item))
    return items
