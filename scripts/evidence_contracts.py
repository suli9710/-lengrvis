#!/usr/bin/env python3
"""Shared helpers for fail-closed reviewed-evidence validators."""

from __future__ import annotations

import base64
import binascii
import json
import hmac
import os
import re
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

PASS_VALUES = {"pass", "passed", "success", "succeeded", "verified", "reviewed_passed"}
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9._-]{8,}", re.IGNORECASE)),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{8,}=*", re.IGNORECASE)),
    (
        "token parameter",
        re.compile(
            r"(?i)(?:[?&]|\b)(?:token|api[_-]?key|client_secret|secret|password|session)=\S+"
        ),
    ),
    (
        "pairing code",
        re.compile(
            r"(?i)\b(?:pairing[_ -]?code|one[_ -]?time[_ -]?code|otp)\s*[:= ]\s*[\w.-]+"
        ),
    ),
    (
        "private home path",
        re.compile(r"(?i)(?:[A-Z]:\\Users\\[^\\\s]+|/Users/[^/\s]+|/home/[^/\s]+)"),
    ),
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
EVIDENCE_PRIVATE_KEY_ENV = "LENGRVIS_REVIEWED_EVIDENCE_PRIVATE_KEY"
EVIDENCE_PUBLIC_KEY_ENV = "LENGRVIS_REVIEWED_EVIDENCE_PUBLIC_KEY"
EVIDENCE_SIGNATURE_PAYLOAD_VERSION = "reviewed-evidence-ed25519/v3"
EVIDENCE_SIGNATURE_DOMAIN = b"lengrvis-reviewed-evidence/v3\0"
UNPADDED_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SHA256_HEX_CHARS = frozenset("0123456789abcdefABCDEF")
DEFAULT_ARTIFACT_CROSS_CHECK_BINDINGS: tuple[tuple[str, str], ...] = (
    ("candidate.artifact_path", "candidate.artifact_sha256"),
)
FULL_GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
GITHUB_REPOSITORY_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$"
)
POSITIVE_INTEGER_RE = re.compile(r"^[1-9][0-9]*$")
CANDIDATE_BINDING_ENVIRONMENT: tuple[tuple[str, str], ...] = (
    ("LENGRVIS_RELEASE_CANDIDATE_COMMIT", "commit"),
    ("LENGRVIS_RELEASE_BUILD_IDENTIFIER", "build_identifier"),
    ("LENGRVIS_RELEASE_CANDIDATE_REPOSITORY", "repository"),
    ("LENGRVIS_RELEASE_CANDIDATE_RUN_ID", "ci_run_id"),
    ("LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT", "ci_run_attempt"),
)


def load_evidence_private_key(value: str) -> Ed25519PrivateKey:
    raw = _decode_ed25519_value(
        value,
        expected_length=32,
        label=EVIDENCE_PRIVATE_KEY_ENV,
    )
    return Ed25519PrivateKey.from_private_bytes(raw)


def load_evidence_public_key(value: str) -> Ed25519PublicKey:
    raw = _decode_ed25519_value(
        value,
        expected_length=32,
        label=EVIDENCE_PUBLIC_KEY_ENV,
    )
    return Ed25519PublicKey.from_public_bytes(raw)


def evidence_public_key_text(private_key: Ed25519PrivateKey) -> str:
    return "ed25519:" + _b64url(_public_key_bytes(private_key.public_key()))


def evidence_public_key_fingerprint(public_key: Ed25519PublicKey) -> str:
    return sha256(_public_key_bytes(public_key)).hexdigest()


def seal_evidence_payload_signature(
    payload: dict[str, Any], *, private_key_text: str
) -> dict[str, Any]:
    """Return a copy sealed by the reviewer-only Ed25519 private key."""

    private_key = load_evidence_private_key(private_key_text)
    sealed = deepcopy(payload)
    evidence = sealed.setdefault("evidence", {})
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object when present")
    evidence["payload_sha256"] = ""
    evidence["signature"] = ""
    evidence["signature_payload_version"] = EVIDENCE_SIGNATURE_PAYLOAD_VERSION
    evidence["signing_key_fingerprint"] = evidence_public_key_fingerprint(
        private_key.public_key()
    )
    payload_hash = canonical_evidence_payload_hash(sealed)
    evidence["payload_sha256"] = payload_hash
    signature = private_key.sign(_evidence_signature_message(payload_hash))
    evidence["signature"] = "ed25519:" + _b64url(signature)
    return sealed


@dataclass(frozen=True)
class CandidateBinding:
    """Immutable CI identity a reviewed release-evidence artifact must match."""

    commit: str
    build_identifier: str
    repository: str
    ci_run_id: str
    ci_run_attempt: str


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


def paths_refer_to_same_file(input_path: Path, output_path: Path) -> bool:
    """Return whether two paths name the same file, including hard links."""

    if input_path.resolve(strict=False) == output_path.resolve(strict=False):
        return True
    try:
        return os.path.samefile(input_path, output_path)
    except (FileNotFoundError, OSError):
        return False


def write_text_atomically(path: Path, text: str, *, force: bool) -> None:
    """Publish a complete UTF-8 file without racing an implicit overwrite."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=False,
    )
    temporary_path = Path(temporary_name)
    identity: tuple[int, int] | None = None
    try:
        identity = _descriptor_identity(descriptor)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
            if os.name != "nt" and hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), 0o644)
        if not _path_has_identity(temporary_path, identity):
            raise OSError("temporary output path identity changed before publication")
        if force:
            os.replace(temporary_path, path)
        elif os.name == "nt":
            os.rename(temporary_path, path)
        else:
            os.link(temporary_path, path, follow_symlinks=False)
            temporary_path.unlink()
        _fsync_parent_directory(path.parent)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        if identity is not None:
            _unlink_if_identity_matches(temporary_path, identity)
        raise


def _descriptor_identity(descriptor: int) -> tuple[int, int]:
    stat = os.fstat(descriptor)
    return int(stat.st_dev), int(stat.st_ino)


def _path_has_identity(path: Path, identity: tuple[int, int]) -> bool:
    if path.is_symlink():
        return False
    try:
        stat = path.lstat()
    except (FileNotFoundError, OSError):
        return False
    return (int(stat.st_dev), int(stat.st_ino)) == identity


def _unlink_if_identity_matches(path: Path, identity: tuple[int, int]) -> None:
    if not _path_has_identity(path, identity):
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _fsync_parent_directory(path: Path) -> None:
    if os.name == "nt" or not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def get_path(payload: dict[str, Any], dotted_path: str) -> Any:
    value: Any = payload
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def candidate_binding_from_environment(
    environment: Mapping[str, str] | None = None,
) -> tuple[CandidateBinding | None, list[str]]:
    """Load the candidate identity explicitly supplied by a release workflow.

    Strict release validation deliberately does not infer this identity from the
    checkout or generic GitHub variables.  Evidence may be reviewed in a
    separate candidate run, so the immutable candidate context must be carried
    forward explicitly and compared exactly.
    """

    source = os.environ if environment is None else environment
    values: dict[str, str] = {}
    errors: list[str] = []
    for environment_name, field_name in CANDIDATE_BINDING_ENVIRONMENT:
        value = str(source.get(environment_name) or "").strip()
        if not value:
            errors.append(
                f"{environment_name} is required for strict candidate binding"
            )
        values[field_name] = value
    if errors:
        return None, errors

    binding = CandidateBinding(**values)
    errors.extend(
        _candidate_binding_definition_errors(
            binding, context="strict candidate context"
        )
    )
    return (binding if not errors else None), errors


def validate_candidate_binding(
    payload: dict[str, Any],
    expected: CandidateBinding,
    errors: list[str],
) -> None:
    """Fail closed unless signed reviewed evidence names this exact candidate."""

    expected_errors = _candidate_binding_definition_errors(
        expected, context="strict candidate context"
    )
    if expected_errors:
        errors.extend(expected_errors)
        return

    actual_values: dict[str, str] = {}
    missing = False
    for _, field_name in CANDIDATE_BINDING_ENVIRONMENT:
        value = get_path(payload, f"candidate.{field_name}")
        if not isinstance(value, str) or not value.strip():
            errors.append(
                f"candidate.{field_name} is required for strict candidate binding"
            )
            missing = True
            continue
        actual_values[field_name] = value.strip()
    if missing:
        return

    actual = CandidateBinding(**actual_values)
    for field_name in (
        "commit",
        "build_identifier",
        "repository",
        "ci_run_id",
        "ci_run_attempt",
    ):
        if getattr(actual, field_name) != getattr(expected, field_name):
            errors.append(f"candidate_{field_name}_mismatch")


def _candidate_binding_definition_errors(
    binding: CandidateBinding, *, context: str
) -> list[str]:
    errors: list[str] = []
    if not FULL_GIT_COMMIT_RE.fullmatch(binding.commit):
        errors.append(f"{context}.commit must be a lowercase 40-character Git SHA")
    if not GITHUB_REPOSITORY_RE.fullmatch(binding.repository):
        errors.append(f"{context}.repository must be an owner/repository identifier")
    if not POSITIVE_INTEGER_RE.fullmatch(binding.ci_run_id):
        errors.append(f"{context}.ci_run_id must be a positive integer")
    if not POSITIVE_INTEGER_RE.fullmatch(binding.ci_run_attempt):
        errors.append(f"{context}.ci_run_attempt must be a positive integer")
    expected_identifier = (
        f"rc-{binding.ci_run_id}-{binding.ci_run_attempt}-{binding.commit}"
    )
    if binding.build_identifier != expected_identifier:
        errors.append(f"{context}.build_identifier must equal {expected_identifier!r}")
    return errors


def require_artifact_type(
    payload: dict[str, Any], expected: str, errors: list[str]
) -> None:
    actual = get_path(payload, "artifact_type")
    if actual != expected:
        errors.append(f"artifact_type must be {expected!r}, got {actual!r}")
    if isinstance(actual, str) and "template" in actual.lower():
        errors.append("template evidence is not accepted as reviewed pass evidence")


def require_nonempty(payload: dict[str, Any], path: str, errors: list[str]) -> None:
    value = get_path(payload, path)
    if value is None or (isinstance(value, str) and not value.strip()):
        errors.append(f"{path} is required")


def require_any_nonempty(
    payload: dict[str, Any], paths: tuple[str, ...], errors: list[str]
) -> None:
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
        if value not in (None, "") and any(
            marker in leaf for marker in PROHIBITED_KEY_MARKERS
        ):
            errors.append(f"{path} must not contain raw sensitive fields")
        if not isinstance(value, str):
            continue
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(value):
                errors.append(f"{path} contains {label}; use a redacted label")
        if RAW_URL_RE.search(value):
            errors.append(
                f"{path} contains a raw URL host; use https://[redacted-host] or a label"
            )
    return errors


def canonical_evidence_payload_hash(payload: dict[str, Any]) -> str:
    unsigned = json.loads(json.dumps(payload, ensure_ascii=False))
    evidence = unsigned.get("evidence")
    if isinstance(evidence, dict):
        evidence.pop("payload_sha256", None)
        evidence.pop("signature", None)
    normalized = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
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
            errors.append(
                f"{path_key} is required for on-disk artifact SHA256 verification"
            )
            continue
        expected_sha = get_path(payload, sha_key)
        if not is_sha256_hex(expected_sha):
            errors.append(
                f"{sha_key} must be a 64-character SHA256 hex digest for on-disk verification"
            )
            continue
        resolved = _resolve_dist_artifact_path(root, artifact_path)
        if resolved is None:
            errors.append(f"{path_key} must be a repo-relative path under dist/")
            continue
        if not resolved.is_file():
            errors.append(
                f"{path_key} must point to an existing on-disk artifact: {artifact_path}"
            )
            continue
        actual_sha = sha256(resolved.read_bytes()).hexdigest()
        if not hmac.compare_digest(actual_sha, str(expected_sha).strip().lower()):
            errors.append(
                f"{sha_key} does not match SHA256 of on-disk artifact at {path_key} ({artifact_path})"
            )


def validate_evidence_signature(
    payload: dict[str, Any], errors: list[str]
) -> dict[str, bool]:
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        errors.append("evidence signature block is required")
        return {
            "valid_hash": False,
            "valid_signature": False,
            "valid_key_binding": False,
        }
    payload_sha256 = str(evidence.get("payload_sha256") or "").strip()
    signature = str(evidence.get("signature") or "").strip()
    fingerprint = str(evidence.get("signing_key_fingerprint") or "").strip()
    payload_version = str(evidence.get("signature_payload_version") or "").strip()
    computed_hash = canonical_evidence_payload_hash(payload)
    valid_hash = bool(
        payload_sha256 and hmac.compare_digest(payload_sha256, computed_hash)
    )
    if not payload_sha256:
        errors.append("evidence.payload_sha256 is required")
    elif not valid_hash:
        errors.append(
            "evidence.payload_sha256 does not match canonical reviewed evidence payload"
        )

    if payload_version != EVIDENCE_SIGNATURE_PAYLOAD_VERSION:
        errors.append(
            "evidence.signature_payload_version must be "
            f"{EVIDENCE_SIGNATURE_PAYLOAD_VERSION!r}"
        )
    if not is_sha256_hex(fingerprint):
        errors.append(
            "evidence.signing_key_fingerprint must be a full SHA256 hex digest"
        )

    try:
        public_key = load_evidence_public_key(
            str(os.getenv(EVIDENCE_PUBLIC_KEY_ENV) or "")
        )
    except ValueError as exc:
        errors.append(str(exc))
        return {
            "valid_hash": valid_hash,
            "valid_signature": False,
            "valid_key_binding": False,
        }

    expected_fingerprint = evidence_public_key_fingerprint(public_key)
    valid_key_binding = bool(
        is_sha256_hex(fingerprint)
        and hmac.compare_digest(fingerprint.lower(), expected_fingerprint)
        and payload_version == EVIDENCE_SIGNATURE_PAYLOAD_VERSION
    )
    if is_sha256_hex(fingerprint) and not hmac.compare_digest(
        fingerprint.lower(), expected_fingerprint
    ):
        errors.append(
            "evidence.signing_key_fingerprint does not match the configured public key"
        )

    valid_signature = False
    if not signature:
        errors.append("evidence.signature is required")
    else:
        try:
            signature_bytes = _decode_ed25519_value(
                signature,
                expected_length=64,
                label="evidence.signature",
            )
            public_key.verify(
                signature_bytes, _evidence_signature_message(computed_hash)
            )
            valid_signature = True
        except (InvalidSignature, ValueError):
            errors.append(
                "evidence.signature is invalid for the reviewed evidence payload"
            )
    return {
        "valid_hash": valid_hash,
        "valid_signature": valid_signature,
        "valid_key_binding": valid_key_binding,
    }


def _decode_ed25519_value(value: str, *, expected_length: int, label: str) -> bytes:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    if not normalized.startswith("ed25519:"):
        raise ValueError(f"{label} must use the ed25519: prefix")
    encoded = normalized.removeprefix("ed25519:")
    if not encoded or "=" in encoded:
        raise ValueError(f"{label} must use unpadded base64url encoding")
    if not UNPADDED_BASE64URL_RE.fullmatch(encoded):
        raise ValueError(f"{label} is not valid base64url")
    padded = encoded + ("=" * (-len(encoded) % 4))
    try:
        decoded = base64.b64decode(
            padded.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise ValueError(f"{label} is not valid base64url") from exc
    if len(decoded) != expected_length:
        raise ValueError(f"{label} has an invalid Ed25519 length")
    return decoded


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _public_key_bytes(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)


def _evidence_signature_message(payload_hash: str) -> bytes:
    return EVIDENCE_SIGNATURE_DOMAIN + bytes.fromhex(payload_hash)


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
        "valid_hash": signature["valid_hash"],
        "valid_signature": signature["valid_signature"],
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
