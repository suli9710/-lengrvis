#!/usr/bin/env python3
"""Shared helpers for fail-closed reviewed-evidence validators."""

from __future__ import annotations

import json
import hmac
import os
import re
from hashlib import sha256
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

PASS_VALUES = {"pass", "passed", "success", "succeeded", "verified", "reviewed_passed"}
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9._-]{8,}", re.IGNORECASE)),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{8,}=*", re.IGNORECASE)),
    ("token parameter", re.compile(r"(?i)(?:[?&]|\b)(?:token|api[_-]?key|client_secret|secret|password|session)=\S+")),
    ("pairing code", re.compile(r"(?i)\b(?:pairing[_ -]?code|one[_ -]?time[_ -]?code|otp)\s*[:= ]\s*[\w.-]+")),
    ("private home path", re.compile(r"(?i)(?:[A-Z]:\\Users\\[^\\\s]+|/Users/[^/\s]+|/home/[^/\s]+)")),
    ("raw IPv4 address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("email address", re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")),
)
RAW_URL_RE = re.compile(r"(?i)\b(?:https?|wss?)://(?!\[redacted-host\])[^/\s]+")
PROHIBITED_KEY_MARKERS = (
    "raw_ip",
    "raw_host",
    "raw_hostname",
    "raw_device",
    "device_name",
    "token",
    "pairing_code",
    "authorization",
    "customer_email",
    "customer_name",
)
EVIDENCE_SIGNATURE_ENV = "LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"
SHA256_HEX_CHARS = frozenset("0123456789abcdefABCDEF")
DEFAULT_ARTIFACT_CROSS_CHECK_BINDINGS: tuple[tuple[str, str], ...] = (
    ("candidate.artifact_path", "candidate.artifact_sha256"),
)


def load_json(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [f"evidence file not found: {path}"]
    except json.JSONDecodeError as exc:
        return None, [f"evidence file is not valid JSON: {exc}"]
    if not isinstance(payload, dict):
        return None, ["evidence root must be a JSON object"]
    return payload, []


def get_path(payload: dict[str, Any], dotted_path: str) -> Any:
    value: Any = payload
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def require_artifact_type(payload: dict[str, Any], expected: str, errors: list[str]) -> None:
    actual = get_path(payload, "artifact_type")
    if actual != expected:
        errors.append(f"artifact_type must be {expected!r}, got {actual!r}")
    if isinstance(actual, str) and "template" in actual.lower():
        errors.append("template evidence is not accepted as reviewed pass evidence")


def require_nonempty(payload: dict[str, Any], path: str, errors: list[str]) -> None:
    value = get_path(payload, path)
    if value is None or (isinstance(value, str) and not value.strip()):
        errors.append(f"{path} is required")


def require_any_nonempty(payload: dict[str, Any], paths: tuple[str, ...], errors: list[str]) -> None:
    if not any(_has_value(get_path(payload, path)) for path in paths):
        errors.append(f"one of {', '.join(paths)} is required")


def require_passed(payload: dict[str, Any], path: str, errors: list[str]) -> None:
    value = get_path(payload, path)
    if not is_passed(value):
        errors.append(f"{path} must be passed")


def require_true(payload: dict[str, Any], path: str, errors: list[str]) -> None:
    if get_path(payload, path) is not True:
        errors.append(f"{path} must be true")


def require_false(payload: dict[str, Any], path: str, errors: list[str]) -> None:
    if get_path(payload, path) is not False:
        errors.append(f"{path} must be false")


def require_iso_datetime(payload: dict[str, Any], path: str, errors: list[str]) -> None:
    value = get_path(payload, path)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path} is required")
        return
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        datetime.fromisoformat(text)
    except ValueError:
        errors.append(f"{path} must be an ISO datetime")


def validate_redacted_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for path, value in _walk(payload):
        leaf = path.rsplit(".", 1)[-1].casefold()
        if value not in (None, "") and any(marker in leaf for marker in PROHIBITED_KEY_MARKERS):
            errors.append(f"{path} must not contain raw sensitive fields")
        if not isinstance(value, str):
            continue
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(value):
                errors.append(f"{path} contains {label}; use a redacted label")
        if RAW_URL_RE.search(value):
            errors.append(f"{path} contains a raw URL host; use https://[redacted-host] or a label")
    return errors


def canonical_evidence_payload_hash(payload: dict[str, Any]) -> str:
    unsigned = json.loads(json.dumps(payload, ensure_ascii=False))
    evidence = unsigned.get("evidence")
    if isinstance(evidence, dict):
        evidence.pop("payload_sha256", None)
        evidence.pop("signature", None)
        evidence.pop("signing_key_fingerprint", None)
    normalized = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(normalized.encode("utf-8")).hexdigest()


def is_sha256_hex(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    return len(text) == 64 and all(ch in SHA256_HEX_CHARS for ch in text)


def require_sha256_hex(payload: dict[str, Any], path: str, errors: list[str]) -> None:
    if not is_sha256_hex(get_path(payload, path)):
        errors.append(f"{path} must be a 64-character SHA256 hex digest")


def _normalize_repo_relative_path(path_text: str) -> str:
    return path_text.strip().replace("\\", "/").lstrip("./")


def _resolve_dist_artifact_path(repo_root: Path, artifact_path: str) -> Path | None:
    normalized = _normalize_repo_relative_path(artifact_path)
    if not normalized.startswith("dist/"):
        return None
    if ".." in PurePosixPath(normalized).parts:
        return None
    dist_root = (repo_root / "dist").resolve(strict=False)
    candidate = (repo_root / normalized).resolve(strict=False)
    try:
        candidate.relative_to(dist_root)
    except ValueError:
        return None
    return candidate


def validate_dist_artifact_sha256_cross_check(
    payload: dict[str, Any],
    errors: list[str],
    *,
    repo_root: Path | None = None,
    bindings: tuple[tuple[str, str], ...] = DEFAULT_ARTIFACT_CROSS_CHECK_BINDINGS,
) -> None:
    root = (repo_root or Path.cwd()).resolve()
    for path_key, sha_key in bindings:
        artifact_path = get_path(payload, path_key)
        if not isinstance(artifact_path, str) or not artifact_path.strip():
            continue
        expected_sha = get_path(payload, sha_key)
        if not is_sha256_hex(expected_sha):
            continue
        resolved = _resolve_dist_artifact_path(root, artifact_path)
        if resolved is None:
            errors.append(f"{path_key} must be a repo-relative path under dist/")
            continue
        if not resolved.is_file():
            continue
        actual_sha = sha256(resolved.read_bytes()).hexdigest()
        if not hmac.compare_digest(actual_sha, str(expected_sha).strip().lower()):
            errors.append(
                f"{sha_key} does not match SHA256 of on-disk artifact at {path_key} ({artifact_path})"
            )


def validate_evidence_signature(payload: dict[str, Any], errors: list[str]) -> dict[str, bool]:
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        errors.append("evidence signature block is required")
        return {"valid_hash": False, "valid_signature": False}
    payload_sha256 = str(evidence.get("payload_sha256") or "").strip()
    signature = str(evidence.get("signature") or "").strip()
    fingerprint = str(evidence.get("signing_key_fingerprint") or "").strip()
    computed_hash = canonical_evidence_payload_hash(payload)
    valid_hash = bool(payload_sha256 and hmac.compare_digest(payload_sha256, computed_hash))
    if not payload_sha256:
        errors.append("evidence.payload_sha256 is required")
    elif not valid_hash:
        errors.append("evidence.payload_sha256 does not match canonical reviewed evidence payload")
    if not fingerprint:
        errors.append("evidence.signing_key_fingerprint is required")
    secret = str(os.getenv(EVIDENCE_SIGNATURE_ENV) or "").strip()
    if not secret:
        errors.append(f"{EVIDENCE_SIGNATURE_ENV} must be set to verify reviewed evidence signatures")
        return {"valid_hash": valid_hash, "valid_signature": False}
    expected_signature = hmac.new(secret.encode("utf-8"), computed_hash.encode("utf-8"), sha256).hexdigest()
    valid_signature = bool(signature and hmac.compare_digest(signature, expected_signature))
    if not signature:
        errors.append("evidence.signature is required")
    elif not valid_signature:
        errors.append("evidence.signature is invalid for the reviewed evidence payload")
    return {"valid_hash": valid_hash, "valid_signature": valid_signature}


def is_passed(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in PASS_VALUES
    if isinstance(value, dict):
        for key in ("passed", "status", "result", "outcome"):
            if key in value and is_passed(value[key]):
                return True
    return False


def reviewed_evidence_contract_status(
    payload: dict[str, Any],
    *,
    release_signoff_path: str,
    reviewed_status_path: str = "review.status",
    errors: list[str] | None = None,
) -> dict[str, bool]:
    contract_errors: list[str] = [] if errors is None else errors
    signature = validate_evidence_signature(payload, contract_errors)
    return {
        **signature,
        "reviewed_pass": is_passed(get_path(payload, reviewed_status_path)),
        "release_signoff": get_path(payload, release_signoff_path) is True,
    }


def result_payload(
    evidence_path: Path,
    artifact_type: str,
    errors: list[str],
    *,
    contract: dict[str, bool] | None = None,
) -> dict[str, Any]:
    result = {
        "ok": not errors,
        "evidence": str(evidence_path),
        "artifact_type": artifact_type,
        "errors": errors,
    }
    if contract is not None:
        result["contract"] = contract
    return result


def print_result(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _has_value(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def _walk(value: Any, path: str = "$") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        rows: list[tuple[str, Any]] = []
        for key, item in value.items():
            rows.extend(_walk(item, f"{path}.{key}"))
        return rows
    if isinstance(value, list):
        rows = []
        for index, item in enumerate(value):
            rows.extend(_walk(item, f"{path}[{index}]"))
        return rows
    return [(path, value)]
