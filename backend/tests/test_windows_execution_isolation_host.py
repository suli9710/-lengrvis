from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from app.security import windows_execution_isolation_host as host


def _sha256_label(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _configure_host(monkeypatch: pytest.MonkeyPatch, path) -> tuple[object, str]:
    policy_path = path.parent / "isolation-policy.json"
    policy_path.write_text('{"policy":"fixture"}', encoding="utf-8")
    policy_digest = _sha256_label(policy_path.read_bytes())
    monkeypatch.setattr(host.sys, "platform", "win32")
    monkeypatch.setenv("LENGRVIS_WINDOWS_ISOLATION_HOST_PATH", str(path))
    monkeypatch.setenv(
        "LENGRVIS_WINDOWS_ISOLATION_HOST_SHA256",
        _sha256_label(path.read_bytes()),
    )
    monkeypatch.setenv("LENGRVIS_WINDOWS_ISOLATION_POLICY_PATH", str(policy_path))
    monkeypatch.setenv("LENGRVIS_WINDOWS_ISOLATION_POLICY_SHA256", policy_digest)
    return policy_path, policy_digest


def _challenge(binary, policy_digest: str) -> dict[str, object]:
    return {
        "nonce": "n" * 32,
        "process_id": 10,
        "parent_process_id": 9,
        "expected_host_binary_sha256": _sha256_label(binary.read_bytes()),
        "expected_policy_sha256": policy_digest,
    }


def test_windows_host_adapter_uses_fixed_pinned_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    binary = tmp_path / "lengrvis-isolation-host.exe"
    binary.write_bytes(b"signed-host-fixture")
    policy_path, policy_digest = _configure_host(monkeypatch, binary)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-isolation-host")
    monkeypatch.setattr(host, "_authenticode_signature_valid", lambda _path: True)
    captured = {}
    response = {
        "payload": {"provider": "fixture"},
        "signature": "ed25519:fixture",
    }

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=json.dumps(response) + "\n", stderr="")

    monkeypatch.setattr(host, "run_process_tree", fake_run)
    challenge = _challenge(binary, policy_digest)

    result = host.attest_current_process_tree(challenge)

    assert result == response
    assert captured["command"] == [
        str(binary.resolve()),
        "--attest-current-process-tree",
        "--policy",
        str(policy_path.resolve()),
    ]
    assert captured["shell"] is False
    assert captured["cwd"] == str(binary.resolve().parent)
    assert captured["require_windows_isolation"] is True
    assert captured["windows_job_limits"].active_processes == 1
    assert "OPENAI_API_KEY" not in captured["env"]
    request = json.loads(captured["input"])
    assert request["challenge"] == challenge
    assert request["expected_host_binary_sha256"] == _sha256_label(binary.read_bytes())
    assert request["expected_policy_sha256"] == policy_digest


def test_windows_host_adapter_rejects_digest_mismatch_before_launch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    binary = tmp_path / "lengrvis-isolation-host.exe"
    binary.write_bytes(b"host")
    _policy_path, policy_digest = _configure_host(monkeypatch, binary)
    monkeypatch.setenv("LENGRVIS_WINDOWS_ISOLATION_HOST_SHA256", f"sha256:{'0' * 64}")
    monkeypatch.setattr(
        host,
        "run_process_tree",
        lambda *_args, **_kwargs: pytest.fail("digest mismatch launched the isolation host"),
    )

    with pytest.raises(RuntimeError, match="digest"):
        host.attest_current_process_tree(_challenge(binary, policy_digest))


def test_windows_host_adapter_rejects_unsigned_or_multiline_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    binary = tmp_path / "lengrvis-isolation-host.exe"
    binary.write_bytes(b"host")
    _policy_path, policy_digest = _configure_host(monkeypatch, binary)
    monkeypatch.setattr(host, "_authenticode_signature_valid", lambda _path: False)

    with pytest.raises(RuntimeError, match="Authenticode"):
        host.attest_current_process_tree(_challenge(binary, policy_digest))

    monkeypatch.setattr(host, "_authenticode_signature_valid", lambda _path: True)
    monkeypatch.setattr(
        host,
        "run_process_tree",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout='{"payload":{},"signature":"x"}\n{"extra":true}\n',
            stderr="",
        ),
    )
    with pytest.raises(RuntimeError, match="one JSON line"):
        host.attest_current_process_tree(_challenge(binary, policy_digest))
