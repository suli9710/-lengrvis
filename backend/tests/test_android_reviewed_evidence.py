from __future__ import annotations

import base64
import importlib.util
import json
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
STRICT_CANDIDATE = {
    "commit": "c" * 40,
    "build_identifier": f"rc-45678-3-{'c' * 40}",
    "repository": "lengrvis/mavris",
    "ci_run_id": "45678",
    "ci_run_attempt": "3",
}
APK_SHA256 = "d" * 64
SIGNER_SHA256 = "a" * 64
PACKAGE_NAME = "com.lengrvis.approval"
VERSION_NAME = "0.1.2"
VERSION_CODE = 2


def _load_script(name: str):
    sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), SCRIPTS_DIR / name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


evidence_contracts = _load_script("evidence_contracts.py")
android_reviewed = _load_script("verify_android_reviewed_evidence.py")
android_seal = _load_script("seal_android_real_device_evidence.py")
TEST_EVIDENCE_PRIVATE_KEY = "ed25519:" + base64.urlsafe_b64encode(
    sha256(b"android-reviewed-evidence-test-key").digest()
).decode("ascii").rstrip("=")
TEST_EVIDENCE_PUBLIC_KEY = evidence_contracts.evidence_public_key_text(
    evidence_contracts.load_evidence_private_key(TEST_EVIDENCE_PRIVATE_KEY)
)


@pytest.fixture(autouse=True)
def _configure_reviewed_evidence_public_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV, TEST_EVIDENCE_PUBLIC_KEY)
    monkeypatch.delenv(evidence_contracts.EVIDENCE_PRIVATE_KEY_ENV, raising=False)


def _signed(payload: dict) -> dict:
    return evidence_contracts.seal_evidence_payload_signature(
        payload,
        private_key_text=TEST_EVIDENCE_PRIVATE_KEY,
    )


def _artifact_manifest() -> dict:
    entries = [
        ("adb_install_status", "adb-install-status.redacted.txt", "1", 101),
        ("backend_log", "backend-session.redacted.log", "2", 202),
        ("device_screenshot", "device-session.redacted.png", "3", 303),
        ("device_video", "device-session.redacted.mp4", "4", 404),
        ("mobile_log", "mobile-session.redacted.log", "5", 505),
    ]
    return {
        "version": "sha256-manifest/v1",
        "entries": [
            {
                "kind": kind,
                "label_redacted": label,
                "sha256": digest_character * 64,
                "size_bytes": size_bytes,
            }
            for kind, label, digest_character, size_bytes in entries
        ],
    }


def _reviewed_android_payload() -> dict:
    artifact_manifest = _artifact_manifest()
    return _signed(
        {
            "artifact_type": "android-real-device-remote-control-evidence",
            "real_device_result": "passed",
            "candidate": deepcopy(STRICT_CANDIDATE),
            "app": {
                "artifact_sha256": APK_SHA256,
                "artifact_label_redacted": "preview-apk-redacted",
                "build_profile": "preview",
                "eas_build_label_redacted": "eas-build-redacted",
                "package_name": PACKAGE_NAME,
                "version_name": VERSION_NAME,
                "version_code": VERSION_CODE,
                "signer_certificate_sha256": SIGNER_SHA256,
                "provenance": {
                    "type": "reviewed-build-record/v1",
                    "builder_id": "eas-build-production",
                    "build_invocation_id": "eas-build-98765-redacted",
                    "source_repository": STRICT_CANDIDATE["repository"],
                    "source_commit": STRICT_CANDIDATE["commit"],
                    "build_profile": "preview",
                    "built_at_utc": "2026-07-10T10:00:00Z",
                    "artifact_sha256": APK_SHA256,
                    "package_name": PACKAGE_NAME,
                    "version_name": VERSION_NAME,
                    "version_code": VERSION_CODE,
                    "signer_certificate_sha256": SIGNER_SHA256,
                },
            },
            "review": {
                "status": "reviewed_passed",
                "reviewer_label": "reviewer-redacted",
                "reviewed_at_utc": "2026-07-10T12:00:00Z",
            },
            "evidence_artifact_manifest": artifact_manifest,
            "evidence_artifacts_redacted": [entry["label_redacted"] for entry in artifact_manifest["entries"]],
        }
    )


def _binding():
    return evidence_contracts.CandidateBinding(**STRICT_CANDIDATE)


def test_signed_android_reviewed_evidence_accepts_the_matching_candidate(monkeypatch) -> None:
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV, TEST_EVIDENCE_PUBLIC_KEY)

    errors, contract = android_reviewed.validate_payload_with_contract(
        _reviewed_android_payload(),
        expected_candidate_binding=_binding(),
    )

    assert errors == []
    assert contract == {
        "valid_hash": True,
        "valid_signature": True,
        "valid_key_binding": True,
        "candidate_binding_valid": True,
        "artifact_identity_valid": True,
        "artifact_provenance_valid": True,
        "artifact_manifest_valid": True,
        "signing_key_fingerprint_bound": True,
    }


@pytest.mark.parametrize(
    ("path", "replacement", "expected_error"),
    [
        (("app", "package_name"), "invalid", "valid Android application id"),
        (("app", "version_code"), 0, "positive integer"),
        (("app", "signer_certificate_sha256"), "bad", "64-character SHA256"),
        (
            ("app", "provenance", "artifact_sha256"),
            "b" * 64,
            "must match app.artifact_sha256",
        ),
        (
            ("app", "provenance", "source_commit"),
            "f" * 40,
            "must match candidate.commit",
        ),
        (
            ("app", "provenance", "builder_id"),
            "uncollected",
            "reviewed non-placeholder label",
        ),
        (
            ("app", "provenance", "built_at_utc"),
            "2026-07-10T10:00:00+08:00",
            "must use UTC",
        ),
    ],
)
def test_android_reviewed_evidence_rejects_unbound_artifact_identity_or_provenance(
    monkeypatch,
    path: tuple[str, ...],
    replacement: object,
    expected_error: str,
) -> None:
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV, TEST_EVIDENCE_PUBLIC_KEY)
    payload = _reviewed_android_payload()
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    payload = _signed(payload)

    errors, contract = android_reviewed.validate_payload_with_contract(
        payload,
        expected_candidate_binding=_binding(),
    )

    assert any(expected_error in error for error in errors)
    if path[1] == "provenance":
        assert contract["artifact_provenance_valid"] is False
    else:
        assert contract["artifact_identity_valid"] is False


@pytest.mark.parametrize(
    ("field", "replacement", "error_code"),
    [
        ("commit", "a" * 40, "candidate_commit_mismatch"),
        ("build_identifier", f"rc-45678-3-{'a' * 40}", "candidate_build_identifier_mismatch"),
        ("repository", "other/repo", "candidate_repository_mismatch"),
        ("ci_run_id", "99999", "candidate_ci_run_id_mismatch"),
        ("ci_run_attempt", "4", "candidate_ci_run_attempt_mismatch"),
    ],
)
def test_android_reviewed_evidence_rejects_a_signed_replay_for_another_candidate(
    monkeypatch,
    field: str,
    replacement: str,
    error_code: str,
) -> None:
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV, TEST_EVIDENCE_PUBLIC_KEY)
    payload = _reviewed_android_payload()
    payload["candidate"][field] = replacement
    payload = _signed(payload)

    errors = android_reviewed.validate_payload(
        payload,
        expected_candidate_binding=_binding(),
    )

    assert error_code in errors


def test_android_reviewed_evidence_rejects_unsigned_or_tampered_evidence(monkeypatch) -> None:
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV, TEST_EVIDENCE_PUBLIC_KEY)
    unsigned = _reviewed_android_payload()
    unsigned.pop("evidence")
    assert any("signature block" in error for error in android_reviewed.validate_payload(unsigned))

    tampered = _reviewed_android_payload()
    tampered["candidate"]["commit"] = "a" * 40
    assert any(
        "payload_sha256" in error or "signature" in error
        for error in android_reviewed.validate_payload(tampered, expected_candidate_binding=_binding())
    )


def test_android_reviewed_evidence_binds_signing_key_fingerprint(monkeypatch) -> None:
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV, TEST_EVIDENCE_PUBLIC_KEY)
    payload = _reviewed_android_payload()
    payload["evidence"]["signing_key_fingerprint"] = "replacement-key-label"

    errors, contract = android_reviewed.validate_payload_with_contract(
        payload,
        expected_candidate_binding=_binding(),
    )

    assert any("payload_sha256" in error or "signature" in error for error in errors)
    assert contract["signing_key_fingerprint_bound"] is False


def test_android_reviewed_evidence_rejects_a_resigned_false_key_fingerprint(monkeypatch) -> None:
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV, TEST_EVIDENCE_PUBLIC_KEY)
    payload = _reviewed_android_payload()
    payload["evidence"]["signing_key_fingerprint"] = "f" * 64
    payload_hash = evidence_contracts.canonical_evidence_payload_hash(payload)
    payload["evidence"]["payload_sha256"] = payload_hash
    signature = evidence_contracts.load_evidence_private_key(TEST_EVIDENCE_PRIVATE_KEY).sign(
        evidence_contracts.EVIDENCE_SIGNATURE_DOMAIN + bytes.fromhex(payload_hash)
    )
    payload["evidence"]["signature"] = "ed25519:" + base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")

    errors, contract = android_reviewed.validate_payload_with_contract(
        payload,
        expected_candidate_binding=_binding(),
    )

    assert any("does not match the configured public key" in error for error in errors)
    assert contract["signing_key_fingerprint_bound"] is False


@pytest.mark.parametrize(
    ("mutate", "expected_error"),
    [
        (
            lambda payload: payload.pop("evidence_artifact_manifest"),
            "evidence_artifact_manifest must be an object",
        ),
        (
            lambda payload: payload["evidence_artifact_manifest"]["entries"].pop(),
            "missing required artifact kinds",
        ),
        (
            lambda payload: payload["evidence_artifact_manifest"]["entries"][0].update({"sha256": "bad"}),
            "64-character SHA256",
        ),
        (
            lambda payload: payload["evidence_artifacts_redacted"].append("unbound.redacted.log"),
            "must exactly match",
        ),
        (
            lambda payload: payload["evidence_artifact_manifest"].update({"artifact_root": "C:\\Users\\private"}),
            "contains unsupported fields",
        ),
        (
            lambda payload: payload["evidence_artifact_manifest"]["entries"][1].update(
                {"sha256": payload["evidence_artifact_manifest"]["entries"][0]["sha256"]}
            ),
            "SHA256 digests must be unique",
        ),
    ],
)
def test_android_reviewed_evidence_rejects_unbound_artifact_manifest(
    monkeypatch,
    mutate,
    expected_error: str,
) -> None:
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV, TEST_EVIDENCE_PUBLIC_KEY)
    payload = _reviewed_android_payload()
    payload.pop("evidence")
    mutate(payload)
    payload = _signed(payload)

    errors, contract = android_reviewed.validate_payload_with_contract(
        payload,
        expected_candidate_binding=_binding(),
    )

    assert any(expected_error in error for error in errors)
    assert contract["artifact_manifest_valid"] is False


def test_android_real_device_sealer_emits_candidate_bound_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV, TEST_EVIDENCE_PUBLIC_KEY)
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PRIVATE_KEY_ENV, TEST_EVIDENCE_PRIVATE_KEY)
    monkeypatch.setenv("LENGRVIS_RELEASE_CANDIDATE_COMMIT", STRICT_CANDIDATE["commit"])
    monkeypatch.setenv("LENGRVIS_RELEASE_BUILD_IDENTIFIER", STRICT_CANDIDATE["build_identifier"])
    monkeypatch.setenv("LENGRVIS_RELEASE_CANDIDATE_REPOSITORY", STRICT_CANDIDATE["repository"])
    monkeypatch.setenv("LENGRVIS_RELEASE_CANDIDATE_RUN_ID", STRICT_CANDIDATE["ci_run_id"])
    monkeypatch.setenv("LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT", STRICT_CANDIDATE["ci_run_attempt"])
    draft = _reviewed_android_payload()
    draft.pop("evidence")
    input_path = tmp_path / "android-review.draft.json"
    output_path = tmp_path / "build" / "android-real-device-evidence-reviewed.json"
    input_path.write_text(json.dumps(draft), encoding="utf-8")

    sealed, errors = android_seal.write_sealed_evidence(
        input_path=input_path,
        output_path=output_path,
        force=False,
    )

    assert errors == []
    assert sealed is not None
    assert output_path.exists()
    assert sealed["evidence"]["signature_payload_version"] == (evidence_contracts.EVIDENCE_SIGNATURE_PAYLOAD_VERSION)
    assert (
        android_reviewed.validate_payload(
            sealed,
            expected_candidate_binding=_binding(),
        )
        == []
    )


def test_android_real_device_sealer_rejects_a_template(monkeypatch) -> None:
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV, TEST_EVIDENCE_PUBLIC_KEY)
    template = _reviewed_android_payload()
    template["template_status"] = "manual_real_device_evidence_required"

    with pytest.raises(ValueError, match="template evidence"):
        android_seal.seal_payload(template, private_key_text=TEST_EVIDENCE_PRIVATE_KEY)


@pytest.mark.parametrize(
    ("private_key", "message"),
    [
        ("short", "ed25519: prefix"),
        ("ed25519:YQ", "invalid Ed25519 length"),
    ],
)
def test_android_real_device_sealer_rejects_invalid_private_keys(private_key: str, message: str) -> None:
    payload = _reviewed_android_payload()
    payload.pop("evidence")
    with pytest.raises(ValueError, match=message):
        android_seal.seal_payload(
            payload,
            private_key_text=private_key,
        )
