from __future__ import annotations

from types import SimpleNamespace

from app.services import diagnostics_runtime_status


def test_local_model_metrics_convert_probe_failure_to_safe_evidence(monkeypatch) -> None:
    def fail_hardware_probe():
        raise RuntimeError("private native runtime detail")

    monkeypatch.setattr(diagnostics_runtime_status.ollama_service, "hardware_readiness", fail_hardware_probe)
    monkeypatch.setattr(diagnostics_runtime_status.ollama_service, "is_installed", lambda: False)
    monkeypatch.setattr(diagnostics_runtime_status.ollama_service, "bundled_runtime_available", lambda: False)

    metrics = diagnostics_runtime_status.local_model_product_metrics(SimpleNamespace(mode="privacy", provider_name="ollama"))

    assert metrics["ollama"]["hardware_can_install"] is False
    assert metrics["ollama"]["readiness_error_type"] == "RuntimeError"
    assert "private native runtime detail" not in str(metrics)


def test_runtime_boolean_probe_fails_closed() -> None:
    def fail_probe() -> bool:
        raise OSError("unavailable")

    assert diagnostics_runtime_status.safe_bool_call(fail_probe) is False
