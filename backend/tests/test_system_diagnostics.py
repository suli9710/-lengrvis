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
from app.services import system_service
from tls_test_material import write_lan_tls_material


def test_system_diagnostics_include_local_product_metrics(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
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
    assert payload["update_channel"] == {
        "configured": False,
        "status": "not_configured",
        "label": "未配置在线更新通道",
        "detail": "当前未配置在线更新通道，只显示本机版本与本地发布说明。",
        "check_action": "refresh_local_status",
        "offline_only": True,
        "user_action_label": "刷新本机状态",
        "release_notes": {
            "available": True,
            "label": "本地发布说明",
            "detail": "打开随安装包提供的说明文件；本页不会联网检查更新。",
            "path": str(PROJECT_ROOT / "README.md"),
            "source": "local_file",
        },
        "next_steps": [
            "确认是否有新版：查看本地发布说明或新的安装包说明。",
            "遇到故障：导出诊断包，再打开日志位置排查。",
        ],
    }
    assert payload["local_paths"]["data_dir"] == str(tmp_path)
    assert payload["local_paths"]["database"].endswith("lengrvis.db")
    assert payload["local_paths"]["log_dirs"]
    assert payload["audit"]["verification"]["ok"] is True
    assert payload["audit"]["verification"]["checked"] >= 1
    assert payload["recent_failure_counts"]["tasks_failed"] == 1
    assert payload["recent_failure_counts"]["tool_results_failed"] == 1
    assert payload["lan_transport"]["status"] == "http_lan_insecure"
    assert payload["lan_transport"]["tls_ready"] is False
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert "自动更新已完成" not in encoded
    assert "auto_update_completed" not in encoded


def test_system_diagnostics_include_anonymous_product_funnel(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    monkeypatch.setenv("LENGRVIS_API_KEY", "sk-diagnostic-secret")
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
    assert local_diagnostics["top_processes"][0]["username"] == "Suli"

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
    assert diagnostics["update_channel"]["configured"] is False
    assert diagnostics["update_channel"]["status"] == "not_configured"
    assert diagnostics["update_channel"]["check_action"] == "refresh_local_status"
    assert diagnostics["update_channel"]["release_notes"]["path"] == "[path_label:release_notes]"
    assert diagnostics["product_metrics"]["schema_version"] == 1
    assert diagnostics["product_metrics"]["local_model"]["schema_version"] == 1
    assert diagnostics["product_funnel"]["schema_version"] == 1
    assert diagnostics["product_funnel"]["local_model"]["schema_version"] == 1
    assert set(("info", "disks", "network", "battery", "top_processes", "suggestions")).issubset(diagnostics)
    assert diagnostics["support_package_redaction"] == {
        "local_paths": "redacted_to_path_labels",
        "process_usernames": "redacted_to_user_labels",
        "release_notes_path": "redacted_to_path_label_when_present",
        "full_local_paths_removed": True,
        "data_dir_path_label": "app_data_dir",
        "database_path_label": "app_database",
        "scope": "local_only",
    }
    assert diagnostics["top_processes"][0]["username"] == "[redacted:local_user]"

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


def test_system_diagnostics_export_redacts_seeded_sensitive_evidence(monkeypatch, tmp_path):
    data_dir = tmp_path / "Users" / "Suli" / "ContosoExportEvidence" / "LengrvisData"
    model_path = tmp_path / "Users" / "Suli" / "AcmeResearchModels" / "private-model" / "model.onnx"
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
    log_snippet = (
        f"task_body={task_body}; api_key={api_key}; Cookie: session={cookie}; "
        f"device_name={device_name}; grant_id={grant_id}; model_path={model_path}"
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
                "headers": {"Cookie": f"session={cookie}"},
                "device_name": device_name,
                "grant_id": grant_id,
                "pairing_code": pairing_code,
                "model_path": str(model_path),
                "log_snippets": [log_snippet],
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

    package_text = json.dumps(package, ensure_ascii=False, sort_keys=True).replace("\\\\", "\\")
    for secret in (
        task_body,
        api_key,
        cookie,
        device_name,
        grant_id,
        pairing_code,
        "sk-export-api-key-secret",
        "sk-device-name-secret",
        "sk-grant-id-secret",
        "sk-pairing-secret",
        str(model_path),
        str(model_path).replace("\\", "/"),
        "AcmeResearchModels",
        "ContosoExportEvidence",
        "Suli",
    ):
        assert secret not in package_text
    assert "[redacted:local_user]" in package_text
    assert "[REDACTED" in package_text or "[redacted:" in package_text


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
