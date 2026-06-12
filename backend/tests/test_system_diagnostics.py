from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT
from app.core import db
from app.core.audit import record
from app.core.schemas import Approval, ApprovalStatus, Run, RunPhase, Task, ToolResult
from app.main import create_app
from app.orchestration.task_phase import TaskPhase
from app.services import system_service, task_recording_service
from tls_test_material import write_lan_tls_material


def test_system_diagnostics_include_local_product_metrics(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    monkeypatch.delenv("LENGRVIS_TASK_RECORDING_ENABLED", raising=False)
    monkeypatch.delenv("LENGRVIS_TASK_RECORDING_FORCE", raising=False)
    monkeypatch.setattr(system_service, "diagnostics", lambda: _fake_system_diagnostics())
    db.init_db()
    record("diagnostics.ready", "pytest", {"ok": True})
    failed_task = Task(user_goal="diagnostic failure sample", status=TaskPhase.FAILED, phase=TaskPhase.FAILED)
    db.upsert_model("tasks", failed_task)
    db.upsert_model("tool_results", ToolResult(tool_call_id="tool_missing", ok=False, error="failed"))

    response = TestClient(create_app()).get("/api/system/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["product"]["name"] == "Lengrvis"
    assert payload["product"]["version"] == "0.1.0"
    update_channel = payload["update_channel"]
    assert update_channel["schema_version"] == 1
    assert update_channel["configured"] is False
    assert update_channel["status"] == "not_configured"
    assert update_channel["label"] == "未配置在线更新通道"
    assert update_channel["detail"] == "当前未配置在线更新通道，只显示本机版本与本地发布说明。"
    assert update_channel["check_action"] == "refresh_local_status"
    assert update_channel["offline_only"] is True
    assert update_channel["network_check_performed"] is False
    assert update_channel["auto_update_claim"] == "not_configured"
    assert update_channel["crash_pipeline_claim"] == "not_reported"
    assert update_channel["user_action_label"] == "刷新本机状态"
    assert update_channel["evidence"] == {
        "update_channel_configured": False,
        "network_update_check_performed": False,
        "release_notes_available": True,
        "release_notes_source": "local_file",
        "auto_update_pipeline": "not_configured",
        "crash_pipeline": "not_reported",
    }
    assert update_channel["release_notes"]["available"] is True
    assert update_channel["release_notes"]["label"] == "本地发布说明"
    assert update_channel["release_notes"]["detail"] == "打开随安装包提供的说明文件；本页不会联网检查更新。"
    assert update_channel["release_notes"]["filename"] == "README.md"
    assert update_channel["release_notes"]["path"] == "[path_label:release_notes]"
    assert update_channel["release_notes"]["path_kind"] == "local_file"
    assert update_channel["release_notes"]["source"] == "local_file"
    assert update_channel["next_steps"] == [
        "确认是否有新版：查看本地发布说明或新的安装包说明。",
        "遇到故障：导出诊断包，再打开日志位置排查。",
    ]
    assert payload["local_paths"]["data_dir"] == str(tmp_path)
    assert payload["local_paths"]["database"].endswith("lengrvis.db")
    assert payload["local_paths"]["log_dirs"]
    assert payload["audit"]["verification"]["ok"] is True
    assert payload["audit"]["verification"]["checked"] >= 1
    assert payload["recent_failure_counts"]["tasks_failed"] == 1
    assert payload["recent_failure_counts"]["tool_results_failed"] == 1
    assert payload["lan_transport"]["status"] == "http_lan_insecure"
    assert payload["lan_transport"]["tls_ready"] is False
    redaction = payload["support_package_redaction"]
    assert redaction["schema_version"] == 1
    assert redaction["applies_to"] == "diagnostics_export_payload"
    assert redaction["scope"] == "local_only"
    assert redaction["intended_audience"] == "trusted_support"
    assert redaction["public_safe"] is False
    assert redaction["review_before_external_sharing"] is True
    assert redaction["current_response"] == {
        "public_safe": False,
        "contains_local_paths": True,
        "external_review_required": True,
    }
    _assert_current_response_contract(redaction, contains_local_paths=True)
    assert redaction["full_local_paths_removed"] is False
    assert redaction["export_full_local_paths_removed"] is True
    _assert_support_package_review_metadata(redaction)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert "自动更新已完成" not in encoded
    assert "auto_update_completed" not in encoded
    assert "crash_pipeline_completed" not in encoded


def test_system_diagnostics_task_recording_defaults_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    monkeypatch.delenv("LENGRVIS_TASK_RECORDING_ENABLED", raising=False)
    monkeypatch.delenv("LENGRVIS_TASK_RECORDING_FORCE", raising=False)
    monkeypatch.setattr(system_service, "diagnostics", lambda: _fake_system_diagnostics())
    db.init_db()

    response = TestClient(create_app()).get("/api/system/diagnostics")

    assert response.status_code == 200
    _assert_task_recording_status(response.json(), enabled=False, env_override="unset")


def test_system_diagnostics_task_recording_force_ignored_outside_test_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    monkeypatch.setenv("LENGRVIS_TASK_RECORDING_FORCE", "1")
    monkeypatch.delenv("LENGRVIS_TASK_RECORDING_ENABLED", raising=False)
    monkeypatch.setattr(task_recording_service, "_is_test_environment", lambda: False)
    monkeypatch.setattr(system_service, "diagnostics", lambda: _fake_system_diagnostics())
    db.init_db()

    response = TestClient(create_app()).get("/api/system/diagnostics")

    assert response.status_code == 200
    _assert_task_recording_status(response.json(), enabled=False, env_override="unset")


def test_system_diagnostics_task_recording_env_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    monkeypatch.setenv("LENGRVIS_TASK_RECORDING_ENABLED", "true")
    monkeypatch.setattr(system_service, "diagnostics", lambda: _fake_system_diagnostics())
    db.init_db()

    response = TestClient(create_app()).get("/api/system/diagnostics")

    assert response.status_code == 200
    _assert_task_recording_status(response.json(), enabled=True, env_override="enabled")


def test_system_diagnostics_include_anonymous_product_funnel(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    monkeypatch.setenv("LENGRVIS_API_KEY", "sk-diagnostic-secret")
    monkeypatch.setattr(system_service, "diagnostics", lambda: _fake_system_diagnostics())
    model_path = tmp_path / "models" / "sk-local-model-path" / "model.onnx"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"placeholder")
    monkeypatch.setenv("LENGRVIS_ONNX_MODEL_PATH", str(model_path))
    db.init_db()

    record("diagnostics.ready", "pytest", {"token": "sk-audit-secret"})
    completed_task = Task(
        user_goal="completed task body sk-task-secret",
        status=TaskPhase.COMPLETED,
        phase=TaskPhase.COMPLETED,
    )
    failed_task = Task(
        user_goal="failed task body sk-failed-task-secret",
        status=TaskPhase.FAILED,
        phase=TaskPhase.FAILED,
    )
    db.upsert_model("tasks", completed_task)
    db.upsert_model("tasks", failed_task)
    db.upsert_model("runs", Run(message="run body sk-run-success-secret", phase=RunPhase.COMPLETED))
    db.upsert_model("runs", Run(message="run body sk-run-failure-secret", phase=RunPhase.FAILED))
    db.upsert_model("tool_results", ToolResult(tool_call_id="tool_ok", ok=True, output={"token": "sk-tool-secret"}))
    db.upsert_model("tool_results", ToolResult(tool_call_id="tool_failed", ok=False, error="sk-tool-error-secret"))
    for status in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
        db.upsert_model(
            "approvals",
            Approval(
                task_id=completed_task.id,
                message=f"approval body sk-approval-{status.value}-secret",
                status=status,
            ),
            status=status.value,
        )
    _insert_mobile_funnel_fixture()

    response = TestClient(create_app()).get("/api/system/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    metrics = payload["product_metrics"]
    funnel = payload["product_funnel"]
    assert metrics["paired_devices_count"] == 1
    assert metrics["active_remote_input_grants_count"] == 1
    assert metrics["tasks"]["recent_success"] == 1
    assert metrics["tasks"]["recent_failure"] == 1
    assert metrics["runs"]["recent_success"] == 1
    assert metrics["runs"]["recent_failure"] == 1
    assert metrics["tool_results"]["recent_success"] == 1
    assert metrics["tool_results"]["recent_failure"] == 1
    assert metrics["approvals"]["pending"] == 1
    assert metrics["approvals"]["approved"] == 1
    assert metrics["approvals"]["rejected"] == 1
    assert metrics["local_model"]["onnx"]["llm_model"] == {"configured": True, "present": True}
    assert funnel["pairing"]["paired_devices_count"] == 1
    assert funnel["remote_input"]["active_remote_input_grants_count"] == 1
    assert funnel["first_task"]["tasks_recent_success_count"] == 1
    assert funnel["first_task"]["tasks_recent_failure_count"] == 1
    assert funnel["approval_response"]["approval_pending_count"] == 1
    assert funnel["approval_response"]["approval_approved_count"] == 1
    assert funnel["approval_response"]["approval_rejected_count"] == 1

    encoded = json.dumps(payload, sort_keys=True)
    for secret in (
        "sk-diagnostic-secret",
        "sk-audit-secret",
        "sk-task-secret",
        "sk-failed-task-secret",
        "sk-run-success-secret",
        "sk-run-failure-secret",
        "sk-tool-secret",
        "sk-tool-error-secret",
        "sk-approval-pending-secret",
        "sk-approval-approved-secret",
        "sk-approval-rejected-secret",
        "sk-device-name-secret",
        "sk-grant-id-secret",
        "sk-pairing-secret",
        "sk-local-model-path",
        str(model_path),
    ):
        assert secret not in encoded


def test_system_diagnostics_include_lan_tls_readiness(monkeypatch, tmp_path):
    cert, key = write_lan_tls_material(tmp_path)
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    monkeypatch.setenv("LENGRVIS_LAN_TLS_ENABLED", "true")
    monkeypatch.setenv("LENGRVIS_LAN_TLS_CERT_FILE", str(cert))
    monkeypatch.setenv("LENGRVIS_LAN_TLS_KEY_FILE", str(key))
    monkeypatch.setenv("LENGRVIS_LAN_PUBLIC_BASE_URL", "https://lengrvis.local:8443")
    monkeypatch.setattr(system_service, "diagnostics", lambda: _fake_system_diagnostics())
    db.init_db()

    response = TestClient(create_app()).get("/api/system/diagnostics")

    assert response.status_code == 200
    transport = response.json()["lan_transport"]
    assert transport["status"] == "https_ready"
    assert transport["origin"] == "https://lengrvis.local:8443"
    assert transport["tls_ready"] is True
    assert transport["tls_material_valid"] is True
    assert transport["requires_trust"] is True


def test_system_diagnostics_export_writes_redacted_support_package(monkeypatch, tmp_path):
    data_dir = tmp_path / "Users" / "Suli" / "Contoso" / "LengrvisData"
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    monkeypatch.setenv("LENGRVIS_API_KEY", "sk-export-secret")
    monkeypatch.delenv("LENGRVIS_TASK_RECORDING_ENABLED", raising=False)
    monkeypatch.delenv("LENGRVIS_TASK_RECORDING_FORCE", raising=False)
    secret_recording_path = data_dir / "task_recordings" / "task_secret" / "recording-secret-frame.png"
    support_debug_recording_path = data_dir / "task_recordings" / "task_secret" / "support-debug-recording.png"
    support_list_recording_path = data_dir / "task_recordings" / "task_secret" / "support-list-recording.png"
    monkeypatch.setattr(
        system_service,
        "diagnostics",
        lambda: {
            "info": {"memory_total": 1024, "memory_available": 512},
            "disks": [],
            "network": {},
            "battery": None,
            "top_processes": [
                {
                    "pid": 4242,
                    "name": "Lengrvis.exe",
                    "username": "Suli",
                    "cpu_percent": 0,
                    "memory_bytes": 128,
                    "status": "running",
                }
            ],
            "local_ai": {"scope": "local_only"},
            "task_recording": {
                "enabled": True,
                "file_name": "recording-secret-frame.png",
                "path": str(secret_recording_path),
                "url": "/api/tasks/task_secret/recordings/recording-secret-frame.png",
                "frames": [
                    {
                        "file_name": "nested-secret-recording.png",
                        "path": str(secret_recording_path.parent / "nested-secret-recording.png"),
                    }
                ],
            },
            "support_debug": {
                "recording_evidence": {
                    "kind": "step_screenshot",
                    "ok": True,
                    "file_name": "support-debug-recording.png",
                    "path": str(support_debug_recording_path),
                    "url": "/api/tasks/task_secret/recordings/support-debug-recording.png",
                    "image_base64": "raw-support-debug-recording-image-secret",
                },
                "recordings": [
                    {
                        "ok": True,
                        "file_name": "support-list-recording.png",
                        "path": str(support_list_recording_path),
                        "image": "raw-support-list-recording-image-secret",
                    }
                ],
            },
            "suggestions": ["No critical system issue detected from read-only diagnostics."],
        },
    )
    db.init_db()
    client = TestClient(create_app())

    local_response = client.get("/api/system/diagnostics")
    assert local_response.status_code == 200
    local_diagnostics = local_response.json()
    local_paths = local_diagnostics["local_paths"]
    assert local_paths["data_dir"] == str(data_dir)
    assert local_paths["database"].startswith(str(data_dir))
    assert any(str(data_dir) in path for path in local_paths["log_dirs"])
    assert local_diagnostics["top_processes"][0]["username"] == "[redacted:local_user]"

    response = client.post("/api/system/diagnostics/export")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["scope"] == "local_only"
    assert payload["path"].startswith(str(data_dir))
    package_path = data_dir / "diagnostic-packages" / payload["filename"]
    assert payload["path"] == str(package_path)
    assert package_path.exists()
    package = json.loads(package_path.read_text(encoding="utf-8"))
    assert package["schema_version"] == 1
    assert package["diagnostic_scope"] == "local_only"
    diagnostics = package["diagnostics"]
    assert diagnostics["diagnostic_scope"] == "local_only"
    assert diagnostics["local_ai"]["scope"] == "local_only"
    assert diagnostics["product"]["name"] == "Lengrvis"
    assert diagnostics["product"]["version"] == "0.1.0"
    assert diagnostics["update_channel"]["schema_version"] == 1
    assert diagnostics["update_channel"]["configured"] is False
    assert diagnostics["update_channel"]["status"] == "not_configured"
    assert diagnostics["update_channel"]["check_action"] == "refresh_local_status"
    assert diagnostics["update_channel"]["network_check_performed"] is False
    assert diagnostics["update_channel"]["auto_update_claim"] == "not_configured"
    assert diagnostics["update_channel"]["crash_pipeline_claim"] == "not_reported"
    assert diagnostics["update_channel"]["release_notes"]["path"] == "[path_label:release_notes]"
    assert diagnostics["update_channel"]["release_notes"]["path_label"] == "release_notes"
    assert diagnostics["update_channel"]["release_notes"]["path_redacted"] is True
    assert diagnostics["update_channel"]["evidence"]["network_update_check_performed"] is False
    assert diagnostics["update_channel"]["evidence"]["auto_update_pipeline"] == "not_configured"
    assert diagnostics["update_channel"]["evidence"]["crash_pipeline"] == "not_reported"
    assert diagnostics["product_metrics"]["schema_version"] == 1
    assert diagnostics["product_metrics"]["local_model"]["schema_version"] == 1
    assert diagnostics["product_funnel"]["schema_version"] == 1
    assert diagnostics["product_funnel"]["local_model"]["schema_version"] == 1
    _assert_task_recording_status(diagnostics, enabled=False, env_override="unset")
    assert set(("info", "disks", "network", "battery", "top_processes", "suggestions")).issubset(diagnostics)
    redaction = diagnostics["support_package_redaction"]
    assert redaction["schema_version"] == 1
    assert redaction["applies_to"] == "diagnostics_export_payload"
    assert redaction["scope"] == "local_only"
    assert redaction["intended_audience"] == "trusted_support"
    assert redaction["public_safe"] is False
    assert redaction["review_before_external_sharing"] is True
    assert redaction["current_response"] == {
        "public_safe": False,
        "contains_local_paths": False,
        "external_review_required": True,
    }
    _assert_current_response_contract(redaction, contains_local_paths=False)
    _assert_support_package_review_metadata(redaction)
    assert redaction["local_paths"] == "redacted_to_path_labels"
    assert redaction["local_path_labels"] == {
        "data_dir": "app_data_dir",
        "database": "app_database",
        "release_notes": "release_notes",
        "log_dirs": ["project_logs", "app_data_logs"],
    }
    assert redaction["process_usernames"] == "redacted_to_user_labels"
    assert redaction["device_names_and_ids"] == "redacted"
    assert redaction["grant_and_pairing_identifiers"] == "redacted"
    assert redaction["task_content"] == "redacted"
    assert redaction["tokens_and_credentials"] == "redacted"
    assert redaction["model_paths"] == "redacted"
    assert redaction["task_recording"] == "status_only_no_images_or_file_names"
    assert redaction["release_notes_path"] == "redacted_to_path_label_when_present"
    assert redaction["full_local_paths_removed"] is True
    assert redaction["export_full_local_paths_removed"] is True
    assert "原始日志" in redaction["guidance"]
    assert "外发前需要单独检查" in redaction["guidance"]
    assert diagnostics["top_processes"][0]["username"] == "[redacted:local_user]"
    assert diagnostics["support_debug"]["recording_evidence"]["status_only"] is True
    assert diagnostics["support_debug"]["recordings"]["status_only"] is True

    exported_paths = diagnostics["local_paths"]
    assert exported_paths["data_dir"] == {
        "path_label": "app_data_dir",
        "kind": "data_dir",
        "redacted": True,
    }
    assert exported_paths["database"] == {
        "path_label": "app_database",
        "kind": "database",
        "filename": "lengrvis.db",
        "parent_path_label": "app_data_dir",
        "redacted": True,
    }
    assert exported_paths["log_dirs"]
    assert all(item["kind"] == "log_dir" and item["redacted"] is True for item in exported_paths["log_dirs"])
    assert all(item["path_label"] for item in exported_paths["log_dirs"])
    assert [item["path_label"] for item in exported_paths["log_dirs"]] == ["project_logs", "app_data_logs"]
    assert all(item["replacement_label"] == item["path_label"] for item in exported_paths["log_dirs"])

    package_text = json.dumps(package, ensure_ascii=False, sort_keys=True).replace("\\\\", "\\")
    raw_paths = [local_paths["data_dir"], local_paths["database"], *local_paths["log_dirs"]]
    for raw_path in raw_paths:
        assert raw_path not in package_text
        assert raw_path.replace("\\", "/") not in package_text
    assert str(PROJECT_ROOT / "README.md") not in package_text
    assert str(PROJECT_ROOT / "README.md").replace("\\", "/") not in package_text
    assert "Suli" not in package_text
    assert "Contoso" not in package_text
    assert "sk-export-secret" not in package_text
    assert "recording-secret-frame.png" not in package_text
    assert "nested-secret-recording.png" not in package_text
    assert "support-debug-recording.png" not in package_text
    assert "support-list-recording.png" not in package_text
    assert "raw-support-debug-recording-image-secret" not in package_text
    assert "raw-support-list-recording-image-secret" not in package_text
    assert "/api/tasks/task_secret/recordings" not in package_text
    assert "task_secret" not in package_text


def test_system_diagnostics_export_redacts_seeded_sensitive_evidence(monkeypatch, tmp_path):
    data_dir = tmp_path / "Users" / "Suli" / "ContosoExportEvidence" / "LengrvisData"
    model_path = tmp_path / "Users" / "Suli" / "Acme Research Models" / "private model" / "model.onnx"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"placeholder")
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    monkeypatch.setenv("LENGRVIS_API_KEY", "sk-export-api-key-secret")
    monkeypatch.setenv("LENGRVIS_ONNX_MODEL_PATH", str(model_path))

    task_body = "export fake task body marker: call finance team about Project Zeta"
    api_key = "sk-export-diagnostics-secret"
    cookie = "cookie-export-secret-1234567890"
    device_name = "Pixel Export Evidence Phone"
    grant_id = "grant-export-secret-1234567890"
    pairing_code = "pair-export-secret-1234567890"
    nested_device_id = "device-nested-secret-1234567890"
    nested_device_name = "Nested Export Evidence Phone"
    nested_grant_id = "grant-nested-secret-1234567890"
    nested_pairing_id = "pairing-nested-secret-1234567890"
    nested_pairing_code = "pairing-code-nested-secret-1234567890"
    nested_task_goal = "nested task goal marker: prepare payroll notes"
    nested_task_prompt = "nested task prompt marker: inspect private calendar"
    nested_run_message = "nested run message marker: summarize confidential draft"
    nested_approval_message = "nested approval message marker: click payroll approval"
    host_name = "SULI-WORKSTATION-SECRET"
    host_header = "support-host-secret.internal"
    bearer_token = "bearer-export-secret-1234567890"
    camel_device_id = "camel-device-secret-1234567890"
    camel_grant_id = "camel-grant-secret-1234567890"
    camel_pairing_id = "camel-pairing-secret-1234567890"
    camel_pairing_code = "camel-pairing-code-secret-1234567890"
    camel_task_body = "camel task body marker: reconcile private invoices"
    camel_user_goal = "camel user goal marker: open private compensation sheet"
    machine_id = "machine-id-secret-1234567890"
    update_token = "update-token-secret-1234567890"
    crash_report_id = "crash-report-secret-1234567890"
    bare_recording_filename = "recording-secret-frame.png"
    bare_screenshot_filename = "screen-shot-secret-before.png"
    dump_file_name = "SULI-WORKSTATION-SECRET-crash.dmp"
    unc_log_path = r"\\SULI-WORKSTATION-SECRET\Users\Suli\Logs\backend.log"
    env_log_path = r"%USERPROFILE%\AppData\Local\Lengrvis\debug.log"
    posix_log_path = "/Users/Suli/Library/Logs/Lengrvis/backend.log"
    log_snippet = (
        f"task_body={task_body}; api_key={api_key}; Cookie: session={cookie}; "
        f"device_name={device_name}; grant_id={grant_id}; model_path={model_path}"
    )
    host_log_snippet = (
        f"Host: {host_header}; host={host_name}; Authorization: Bearer {bearer_token}; "
        f"Set-Cookie: session={cookie}; deviceId={camel_device_id}; grantId={camel_grant_id}; "
        f"pairingCode={camel_pairing_code}; taskBody={camel_task_body}; modelPath={model_path}"
    )
    artifact_log_snippet = (
        f"saved recording {bare_recording_filename}; saved screenshot {bare_screenshot_filename}"
    )
    monkeypatch.setattr(
        system_service,
        "diagnostics",
        lambda: {
            "info": {"memory_total": 2048, "memory_available": 1024},
            "disks": [{"mountpoint": str(data_dir), "note": f"org folder {data_dir}"}],
            "network": {},
            "battery": None,
            "top_processes": [
                {
                    "pid": 4242,
                    "name": "Lengrvis.exe",
                    "username": "Suli",
                    "cpu_percent": 0,
                    "memory_bytes": 128,
                    "status": "running",
                }
            ],
            "local_ai": {
                "scope": "local_only",
                "error": f"failed loading model path {model_path}",
            },
            "support_debug": {
                "task_body": task_body,
                "api_key": api_key,
                "headers": {
                    "Cookie": f"session={cookie}",
                    "Host": host_header,
                    "Authorization": f"Bearer {bearer_token}",
                },
                "device_name": device_name,
                "deviceId": camel_device_id,
                "grant_id": grant_id,
                "grantId": camel_grant_id,
                "pairingId": camel_pairing_id,
                "pairing_code": pairing_code,
                "pairingCode": camel_pairing_code,
                "model_path": str(model_path),
                "modelPath": str(model_path),
                "hostname": host_name,
                "hostName": host_name,
                "machineId": machine_id,
                "taskBody": camel_task_body,
                "userGoal": camel_user_goal,
                "device": {"id": nested_device_id, "name": nested_device_name},
                "grant": {"id": nested_grant_id, "scope": "remote_input"},
                "pairing": {"id": nested_pairing_id, "code": nested_pairing_code},
                "task": {"goal": nested_task_goal, "prompt": nested_task_prompt},
                "run": {"message": nested_run_message},
                "approval": {"message": nested_approval_message},
                "artifacts": {
                    "latestFile": bare_recording_filename,
                    "thumbnail": {"filename": bare_screenshot_filename},
                },
                "local_path_logs": [
                    f"opened {unc_log_path}",
                    f"fallback log at {env_log_path}",
                    f"posix log at {posix_log_path}",
                    f"app child log at {data_dir / 'logs' / 'Suli-secret-child.log'}",
                ],
                "future_update": {
                    "installerPath": str(data_dir / "updates" / "Suli-secret-installer.exe"),
                    "feedHost": host_header,
                    "requestToken": update_token,
                    "log": f"update host={host_header}; token={update_token}; path={env_log_path}",
                },
                "future_crash": {
                    "crashReportId": crash_report_id,
                    "dumpPath": str(data_dir / "crashes" / dump_file_name),
                    "dumpFileName": dump_file_name,
                    "host": host_header,
                },
                "log_snippets": [log_snippet, host_log_snippet, artifact_log_snippet],
            },
            "suggestions": ["No critical system issue detected from read-only diagnostics."],
        },
    )
    db.init_db()
    record("diagnostics.ready", "pytest", {"task_body": task_body, "token": api_key, "cookie": cookie})
    seeded_task = Task(user_goal=task_body, status=TaskPhase.FAILED, phase=TaskPhase.FAILED)
    db.upsert_model("tasks", seeded_task)
    db.upsert_model("runs", Run(message=f"run body {task_body}", phase=RunPhase.FAILED))
    db.upsert_model("tool_results", ToolResult(tool_call_id="tool_failed", ok=False, error=f"api_key={api_key}"))
    db.upsert_model(
        "approvals",
        Approval(task_id=seeded_task.id, message=f"approval body {task_body}", status=ApprovalStatus.PENDING),
        status=ApprovalStatus.PENDING.value,
    )
    _insert_mobile_funnel_fixture()

    response = TestClient(create_app()).post("/api/system/diagnostics/export")

    assert response.status_code == 200
    payload = response.json()
    package_path = data_dir / "diagnostic-packages" / payload["filename"]
    package = json.loads(package_path.read_text(encoding="utf-8"))
    diagnostics = package["diagnostics"]
    assert diagnostics["diagnostic_scope"] == "local_only"
    assert diagnostics["product_metrics"]["schema_version"] == 1
    assert diagnostics["product_metrics"]["tasks"]["recent_failure"] == 1
    assert diagnostics["product_metrics"]["paired_devices_count"] == 1
    assert diagnostics["product_metrics"]["active_remote_input_grants_count"] == 1
    assert diagnostics["product_metrics"]["local_model"]["onnx"]["llm_model"] == {
        "configured": True,
        "present": True,
    }
    assert diagnostics["product_funnel"]["schema_version"] == 1
    assert diagnostics["product_funnel"]["first_task"]["tasks_recent_failure_count"] == 1
    assert diagnostics["product_funnel"]["pairing"]["paired_devices_count"] == 1
    assert diagnostics["product_funnel"]["remote_input"]["active_remote_input_grants_count"] == 1
    assert diagnostics["product_funnel"]["local_model"]["onnx"]["llm_model"] == {
        "configured": True,
        "present": True,
    }
    _assert_support_package_review_metadata(diagnostics["support_package_redaction"])

    package_text = json.dumps(package, ensure_ascii=False, sort_keys=True).replace("\\\\", "\\")
    assert '"checklist"' in package_text
    assert '"public_safe": false' in package_text
    for secret in (
        task_body,
        api_key,
        cookie,
        device_name,
        grant_id,
        pairing_code,
        nested_device_id,
        nested_device_name,
        nested_grant_id,
        nested_pairing_id,
        nested_pairing_code,
        nested_task_goal,
        nested_task_prompt,
        nested_run_message,
        nested_approval_message,
        host_name,
        host_header,
        bearer_token,
        camel_device_id,
        camel_grant_id,
        camel_pairing_id,
        camel_pairing_code,
        camel_task_body,
        camel_user_goal,
        machine_id,
        update_token,
        crash_report_id,
        bare_recording_filename,
        bare_screenshot_filename,
        dump_file_name,
        unc_log_path,
        env_log_path,
        posix_log_path,
        "sk-export-api-key-secret",
        "sk-device-name-secret",
        "sk-grant-id-secret",
        "sk-pairing-secret",
        str(model_path),
        str(model_path).replace("\\", "/"),
        "Acme Research Models",
        "private model",
        "ContosoExportEvidence",
        "Suli",
    ):
        assert secret not in package_text
    assert diagnostics["support_debug"]["artifacts"]["latestFile"] == "[redacted:task_recording_artifact]"
    assert diagnostics["support_debug"]["artifacts"]["thumbnail"]["status_only"] is True
    assert "[path_label:app_data_dir]/[redacted:relative_path]" in package_text
    assert "[redacted:local_user]" in package_text
    assert "[REDACTED" in package_text or "[redacted:" in package_text


def test_system_diagnostics_get_redacts_seeded_sensitive_evidence(monkeypatch, tmp_path):
    data_dir = tmp_path / "Users" / "Suli" / "ContosoGetEvidence" / "LengrvisData"
    model_path = tmp_path / "Users" / "Suli" / "Acme Research Models" / "private model" / "model.onnx"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"placeholder")
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    monkeypatch.setenv("LENGRVIS_ONNX_MODEL_PATH", str(model_path))

    task_body = "get fake task body marker: call finance team about Project Zeta"
    api_key = "sk-get-diagnostics-secret"
    device_name = "Pixel Get Evidence Phone"
    grant_id = "grant-get-secret-1234567890"
    pairing_code = "pair-get-secret-1234567890"
    host_name = "SULI-WORKSTATION-GET-SECRET"
    monkeypatch.setattr(
        system_service,
        "diagnostics",
        lambda: {
            "info": {"memory_total": 2048, "memory_available": 1024},
            "disks": [{"mountpoint": str(data_dir), "note": f"org folder {data_dir}"}],
            "network": {},
            "battery": None,
            "top_processes": [
                {
                    "pid": 4242,
                    "name": "Lengrvis.exe",
                    "username": "Suli",
                    "cpu_percent": 0,
                    "memory_bytes": 128,
                    "status": "running",
                }
            ],
            "local_ai": {"scope": "local_only", "error": f"failed loading model path {model_path}"},
            "support_debug": {
                "task_body": task_body,
                "api_key": api_key,
                "device_name": device_name,
                "grant_id": grant_id,
                "pairing_code": pairing_code,
                "model_path": str(model_path),
                "hostname": host_name,
            },
            "suggestions": ["No critical system issue detected from read-only diagnostics."],
        },
    )
    db.init_db()
    record("diagnostics.ready", "pytest", {"task_body": task_body, "token": api_key})

    response = TestClient(create_app()).get("/api/system/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["local_paths"]["data_dir"] == str(data_dir)
    assert payload["local_paths"]["database"].endswith("lengrvis.db")
    assert payload["support_package_redaction"]["current_response"]["contains_local_paths"] is True
    assert payload["support_debug"]["task_body"] == "[redacted:sensitive_field]"
    assert payload["support_debug"]["api_key"] == "[redacted:sensitive_field]"
    assert payload["support_debug"]["device_name"] == "[redacted:sensitive_field]"
    assert payload["support_debug"]["grant_id"] == "[redacted:sensitive_field]"
    assert payload["support_debug"]["pairing_code"] == "[redacted:sensitive_field]"
    assert payload["support_debug"]["model_path"] == "[redacted:sensitive_field]"
    assert payload["support_debug"]["hostname"] == "[redacted:sensitive_field]"
    assert payload["top_processes"][0]["username"] == "[redacted:local_user]"

    payload_text = json.dumps({key: value for key, value in payload.items() if key != "local_paths"}, ensure_ascii=False, sort_keys=True)
    for secret in (
        task_body,
        api_key,
        device_name,
        grant_id,
        pairing_code,
        host_name,
        str(model_path),
        "Acme Research Models",
    ):
        assert secret not in payload_text
    assert "[REDACTED" in payload_text or "[redacted:" in payload_text


def _assert_support_package_review_metadata(redaction: dict):
    external_review = redaction["external_review"]
    assert redaction["review_status"] == "manual_review_required"
    assert redaction["review_required"] is True
    assert redaction["external_sharing_allowed"] is False
    assert redaction["fail_closed"] is True
    assert external_review["schema_version"] == 1
    assert external_review["status"] == "manual_review_required"
    assert external_review["review_status"] == "manual_review_required"
    assert external_review["review_required"] is True
    assert external_review["required_before_external_sharing"] is True
    assert external_review["public_safe"] is False
    assert external_review["external_sharing_allowed"] is False
    assert external_review["fail_closed"] is True
    assert external_review["machine_decision"] == "block_external_sharing_until_manual_review"
    checklist = external_review["checklist"]
    assert isinstance(checklist, list)
    assert {item["id"] for item in checklist} == {
        "scope_and_audience",
        "raw_logs_and_artifacts",
        "local_paths",
        "secrets_and_identifiers",
        "task_content",
        "external_sharing_decision",
    }
    assert all(item["required"] is True for item in checklist)
    assert all(item["status"] for item in checklist)
    assert any(item["status"] == "pending" for item in checklist)
    assert any(item["status"] == "requires_reviewer_confirmation" for item in checklist)
    assert any(item["status"] == "automated_redaction_applied" for item in checklist)
    expected_summary = {
        "total": 6,
        "required": 6,
        "required_pending": 2,
        "all_required_have_status": True,
        "status_counts": {
            "requires_reviewer_confirmation": 1,
            "export_metadata_only": 1,
            "automated_redaction_applied": 3,
            "pending": 1,
        },
    }
    assert external_review["checklist_summary"] == expected_summary
    assert redaction["checklist_summary"] == expected_summary


def _assert_current_response_contract(redaction: dict, *, contains_local_paths: bool):
    assert redaction["current_response_contract"] == {
        "schema_version": 1,
        "response_kind": "diagnostics_get_response" if contains_local_paths else "diagnostics_export_payload",
        "public_safe": False,
        "contains_local_paths": contains_local_paths,
        "local_path_state": "full_local_paths_present" if contains_local_paths else "path_labels_only",
        "review_status": "manual_review_required",
        "review_required": True,
        "external_sharing_allowed": False,
        "machine_decision": "block_external_sharing_until_manual_review",
    }


def _fake_system_diagnostics() -> dict:
    return {
        "info": {
            "memory_total": 1024,
            "memory_available": 768,
        },
        "disks": [],
        "network": {},
        "battery": None,
        "top_processes": [],
        "local_ai": {"scope": "local_only"},
        "suggestions": ["No critical system issue detected from read-only diagnostics."],
    }


def _assert_task_recording_status(payload: dict, *, enabled: bool, env_override: str):
    task_recording = payload["task_recording"]
    assert set(task_recording) == {"schema_version", "enabled", "default_policy", "local_only", "configuration", "export"}
    assert task_recording["schema_version"] == 1
    assert task_recording["enabled"] is enabled
    assert task_recording["default_policy"] == {
        "mode": "opt_in",
        "enabled_by_default": False,
        "scope": "local_only",
    }
    assert task_recording["local_only"] is True
    assert task_recording["configuration"] == {
        "env_override": env_override,
        "explicit_opt_in": env_override == "enabled",
    }
    assert task_recording["export"] == {
        "status_only": True,
        "contains_images": False,
        "contains_image_paths": False,
        "contains_recording_file_names": False,
    }


def _insert_mobile_funnel_fixture():
    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    expires_at = (now + timedelta(minutes=5)).isoformat()
    device = {
        "id": "device_secret_id",
        "device_id": "device_secret_id",
        "device_name": "Pixel sk-device-name-secret",
        "status": "active",
        "revoked_at": "",
        "remote_input_grants": [
            {
                "id": "sk-grant-id-secret",
                "status": "active",
                "scope": "remote_input",
                "created_at": created_at,
                "expires_at": expires_at,
                "revoked_at": "",
            }
        ],
        "created_at": created_at,
        "updated_at": created_at,
    }
    pairing = {
        "id": "sk-pairing-secret",
        "code": "sk-pairing-secret",
        "status": "used",
        "device_id": "device_secret_id",
        "device_name": "Pixel sk-device-name-secret",
        "created_at": created_at,
        "expires_at": expires_at,
        "used_at": created_at,
        "updated_at": created_at,
    }
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO mobile_devices (id, data, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (device["id"], json.dumps(device), created_at, created_at),
        )
        conn.execute(
            """
            INSERT INTO mobile_pairings (id, data, status, created_at, expires_at, used_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pairing["id"],
                json.dumps(pairing),
                pairing["status"],
                pairing["created_at"],
                pairing["expires_at"],
                pairing["used_at"],
                pairing["updated_at"],
            ),
        )
