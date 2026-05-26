from __future__ import annotations

from dataclasses import dataclass

from app.config import AppSettings


@dataclass(slots=True)
class PrivacyDecision:
    allowed: bool
    reason: str


def can_upload_file_content(settings: AppSettings) -> PrivacyDecision:
    if (settings.mode or "efficiency").lower() == "privacy":
        return PrivacyDecision(False, "Privacy mode does not allow file content upload.")
    if not settings.allow_file_content_upload:
        return PrivacyDecision(False, "allow_file_content_upload is disabled.")
    return PrivacyDecision(True, "File content upload is enabled subject to safety review.")


def can_use_browser_network(settings: AppSettings) -> PrivacyDecision:
    if (settings.mode or "efficiency").lower() == "privacy":
        return PrivacyDecision(False, "Privacy mode blocks all browser network access.")
    if not settings.allow_browser_network:
        return PrivacyDecision(False, "Browser network access is disabled.")
    return PrivacyDecision(True, "Browser network access is enabled subject to safety review.")


def can_use_cloud_model(settings: AppSettings, task: str = "default") -> PrivacyDecision:
    mode = (settings.mode or "efficiency").lower()
    if mode == "privacy":
        return PrivacyDecision(False, "Privacy mode runs every task on a local model.")
    if mode == "efficiency":
        return PrivacyDecision(True, "Efficiency mode allows cloud models for every task.")
    if mode == "hybrid":
        if task in {"planner", "supervisor"}:
            return PrivacyDecision(True, "Hybrid mode uses cloud models for planning and supervision.")
        if task in {"vision", "ocr"} and settings.allow_cloud_context:
            return PrivacyDecision(True, "Hybrid mode allows cloud vision when allow_cloud_context is true.")
        return PrivacyDecision(False, "Hybrid mode keeps subagent and embedding work on local models.")
    return PrivacyDecision(False, f"Unknown mode '{mode}'.")


def can_use_browser_writes(settings: AppSettings) -> PrivacyDecision:
    mode = (settings.mode or "efficiency").lower()
    if mode == "privacy":
        return PrivacyDecision(False, "Privacy mode forbids browser write or interaction actions.")
    if not settings.allow_browser_network:
        return PrivacyDecision(False, "Browser network access is disabled; write actions also blocked.")
    if mode == "hybrid" and not settings.allow_cloud_context:
        return PrivacyDecision(False, "Hybrid mode requires allow_cloud_context for browser writes.")
    return PrivacyDecision(True, "Browser write actions allowed subject to per-tool approval.")
