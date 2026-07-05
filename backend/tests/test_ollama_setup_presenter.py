from __future__ import annotations

from app.services.ollama_public import public_bundle_manifest_summary, public_text
from app.services.ollama_setup_presenter import (
    bundle_manifest_evidence_detail,
    bundled_model_evidence_detail,
    model_setup_label,
    setup_next_action,
    setup_repair_action,
)


def test_setup_next_action_prefers_repairable_runtime_states() -> None:
    ready = {"can_install": True}

    assert setup_next_action({"can_install": False}, False, False, False) == "hardware_blocked"
    assert setup_next_action(ready, False, False, False) == "install_runtime"
    assert setup_next_action(ready, True, False, False) == "start_runtime"
    assert setup_next_action(ready, True, True, False, bundled_model_available=True) == (
        "restart_runtime_with_bundled_models"
    )
    assert setup_next_action(ready, True, True, False, bundled_model_available=True, bundled_model_configured=True) == (
        "use_bundled_model"
    )
    assert setup_next_action(ready, True, True, False) == "download_model"
    assert setup_next_action(ready, True, True, True) == "ready"


def test_setup_repair_action_keeps_privacy_boundary_visible() -> None:
    action = setup_repair_action("download_model", "qwen2.5:3b")

    assert action["code"] == "download_model"
    assert "Privacy tasks stay local-only" in action["detail"]


def test_model_setup_label_distinguishes_bundled_restart() -> None:
    assert model_setup_label(True, False, False, True) == "Use local model"
    assert model_setup_label(False, True, False, True) == "Restart local service for bundled model"
    assert model_setup_label(False, True, True, False) == "Use bundled local model"
    assert model_setup_label(False, False, False, True) == "Download recommended model"


def test_bundle_evidence_details_report_missing_and_proven_states() -> None:
    missing_bundle = {
        "runtime_available": False,
        "models_available": False,
        "model_manifest_present": False,
        "bundle_manifest_present": False,
        "bundle_manifest_valid": False,
        "manifest_model_matches": False,
    }
    proven_bundle = {
        "runtime_available": True,
        "models_available": True,
        "model_manifest_present": True,
        "bundle_manifest_present": True,
        "bundle_manifest_valid": True,
        "manifest_model_matches": True,
    }

    assert bundle_manifest_evidence_detail(missing_bundle, "qwen2.5:3b") == "No Ollama bundle manifest was found."
    assert "missing bundled runtime" in bundled_model_evidence_detail(missing_bundle, "qwen2.5:3b", False)
    assert "proves that qwen2.5:3b is included" in bundle_manifest_evidence_detail(proven_bundle, "qwen2.5:3b")
    assert "preferred model directory points to it" in bundled_model_evidence_detail(proven_bundle, "qwen2.5:3b", True)


def test_public_ollama_text_redacts_urls_and_manifest_paths() -> None:
    assert public_text("failed from https://example.test/token") == "failed from [REDACTED_URL]"

    summary = public_bundle_manifest_summary(
        {
            "path": "C:/Users/Suli/.ollama/models",
            "model_manifest": "C:/Users/Suli/.ollama/models/manifests/qwen",
            "nested": {"bundle_path": "C:/secret"},
        }
    )

    assert summary["path"] == ""
    assert "[REDACTED_LOCAL_PATH]" in summary["model_manifest"]
    assert summary["nested"]["bundle_path"] == ""
