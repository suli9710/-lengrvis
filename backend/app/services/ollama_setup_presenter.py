"""User-facing Ollama setup state and evidence copy."""

from __future__ import annotations

from typing import Any


def setup_next_action(
    readiness: dict[str, Any],
    installed: bool,
    running: bool,
    has_model: bool,
    bundled_model_available: bool = False,
    bundled_model_configured: bool = False,
) -> str:
    if not readiness.get("can_install"):
        return "hardware_blocked"
    if not installed:
        return "install_runtime"
    if not running:
        return "start_runtime"
    if bundled_model_available and not has_model:
        if not bundled_model_configured:
            return "restart_runtime_with_bundled_models"
        return "use_bundled_model"
    if not has_model:
        return "download_model"
    return "ready"


def setup_repair_action(next_action: str, target: str) -> dict[str, str]:
    actions = {
        "hardware_blocked": {
            "code": "free_resources_for_local_ai",
            "label": "Free resources for local AI",
            "detail": (
                "Close memory-heavy apps, free disk space, or choose a smaller supported local model, "
                "then check again. "
                "Privacy mode stays local-only and will not silently use cloud or mock AI."
            ),
        },
        "continue_setup": {
            "code": "continue_setup",
            "label": "Continue local AI setup",
            "detail": (
                f"This computer passes the hardware preflight for {target}. "
                "Continue setup to verify Ollama, start the local service, and prepare the model."
            ),
        },
        "install_runtime": {
            "code": "install_runtime",
            "label": "Install Ollama runtime",
            "detail": (
                f"Use one-click setup to install Ollama, start the local service, and prepare {target}. "
                "If automatic install is unavailable, install Ollama manually and retry. "
                "Privacy tasks stay paused until a local runtime is available."
            ),
        },
        "start_runtime": {
            "code": "start_runtime",
            "label": "Start local AI service",
            "detail": (
                "Start Ollama, or close any stuck Ollama process and retry. "
                "Lengrvis will not switch privacy tasks to cloud or mock AI while the service is down."
            ),
        },
        "restart_runtime_with_bundled_models": {
            "code": "restart_runtime_with_bundled_models",
            "label": "Restart Ollama with bundled models",
            "detail": (
                "Close Ollama, then retry setup so Lengrvis can restart it with the included local model files. "
                "Privacy tasks stay local-only until Ollama lists the model."
            ),
        },
        "use_bundled_model": {
            "code": "use_bundled_model",
            "label": "Use bundled local model",
            "detail": f"Use the bundled {target} model without downloading it.",
        },
        "download_model": {
            "code": "download_model",
            "label": "Download recommended model",
            "detail": (
                f"Keep Ollama running and download {target}. If this app should include the model, "
                "verify the bundled model package, then retry setup. "
                "Privacy tasks stay local-only until the model is present."
            ),
        },
        "ready": {
            "code": "none",
            "label": "No repair needed",
            "detail": f"{target} is ready for local AI.",
        },
    }
    return actions.get(
        next_action,
        {
            "code": "prepare_local_ai",
            "label": "Prepare local AI",
            "detail": f"Run setup again to prepare {target}.",
        },
    )


def runtime_setup_detail(installed: bool, bundled_available: bool) -> str:
    if installed and bundled_available:
        return "Lengrvis bundled Ollama runtime is available."
    if installed:
        return "Ollama is installed."
    if bundled_available:
        return "Lengrvis will use the bundled Ollama runtime."
    return "Ollama is not installed yet; one-click setup can install it, start it, and prepare the model."


def model_setup_label(
    has_model: bool,
    bundled_model_available: bool,
    bundled_model_configured: bool,
    running: bool,
) -> str:
    if has_model:
        return "Use local model"
    if bundled_model_available and running and not bundled_model_configured:
        return "Restart local service for bundled model"
    if bundled_model_available:
        return "Use bundled local model"
    return "Download recommended model"


def model_setup_detail(
    target: str,
    has_model: bool,
    bundled_model_available: bool,
    bundled_model_configured: bool = False,
    running: bool = False,
) -> str:
    if has_model:
        return f"{target} is ready."
    if bundled_model_configured:
        return f"{target} is included with Lengrvis and the local service is configured to read it."
    if bundled_model_available and running:
        return (
            f"{target} is included with Lengrvis, but the running Ollama service is not using "
            "the Lengrvis bundled model directory."
        )
    if bundled_model_available:
        return f"{target} is included with Lengrvis and will be used when the local service starts."
    if running:
        return f"Ollama is running; download {target} before privacy mode can use local AI."
    return f"After Ollama is installed and running, {target} will be downloaded before privacy mode can use local AI."


def runtime_evidence_detail(installed: bool, runtime_source: str) -> str:
    if installed and runtime_source == "bundled":
        return "Bundled Ollama runtime executable was found."
    if installed and runtime_source == "system":
        return "System Ollama executable was found."
    if runtime_source == "bundled":
        return "Bundled Ollama runtime is available but not yet started."
    return "No Ollama runtime executable was found."


def bundle_manifest_evidence_detail(bundle: dict[str, Any], target: str) -> str:
    if not bundle["bundle_manifest_present"]:
        return "No Ollama bundle manifest was found."
    if not bundle["bundle_manifest_valid"]:
        return "Ollama bundle manifest is present but invalid or lacks accepted licenses."
    if not bundle["manifest_model_matches"]:
        return f"Ollama bundle manifest does not prove that {target} is included."
    return f"Ollama bundle manifest proves that {target} is included."


def bundled_model_evidence_detail(bundle: dict[str, Any], target: str, configured: bool) -> str:
    missing = []
    if not bundle["runtime_available"]:
        missing.append("bundled runtime")
    if not bundle["models_available"]:
        missing.append("bundled models directory")
    if not bundle["model_manifest_present"]:
        missing.append("model manifest")
    if not bundle["bundle_manifest_valid"] or not bundle["manifest_model_matches"]:
        missing.append("valid bundle manifest")
    if missing:
        return f"Bundled {target} is not proven available; missing " + ", ".join(missing) + "."
    if not configured:
        return (
            f"Bundled {target} is proven available, but Ollama is not configured to read the bundled model directory."
        )
    return f"Bundled {target} is proven available and the preferred model directory points to it."
