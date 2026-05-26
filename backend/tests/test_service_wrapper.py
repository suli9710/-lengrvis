from __future__ import annotations

import logging
import os
import platform
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import service_wrapper


def test_get_backend_config_uses_backend_main_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAVRIS_BACKEND_HOST", "0.0.0.0")
    monkeypatch.setenv("MAVRIS_BACKEND_PORT", "8123")
    monkeypatch.setenv("MAVRIS_BACKEND_LOG_LEVEL", "debug")

    config = service_wrapper.get_backend_config()

    assert config.host == "0.0.0.0"
    assert config.port == 8123
    assert config.log_level == "debug"


def test_get_backend_config_can_read_service_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAVRIS_BACKEND_HOST", raising=False)
    monkeypatch.delenv("MAVRIS_BACKEND_PORT", raising=False)
    monkeypatch.delenv("MAVRIS_BACKEND_LOG_LEVEL", raising=False)
    fake_util = SimpleNamespace(
        GetServiceCustomOption=MagicMock(
            side_effect=lambda _name, key, default=None: {
                "BackendHost": "0.0.0.0",
                "BackendPort": "9555",
                "BackendLogLevel": "warning",
            }.get(key, default)
        )
    )

    with patch.object(
        service_wrapper,
        "import_pywin32_service_modules",
        return_value=SimpleNamespace(win32serviceutil=fake_util),
    ), patch.object(service_wrapper.platform, "system", return_value="Windows"):
        config = service_wrapper.get_backend_config()

    assert config.host == "0.0.0.0"
    assert config.port == 9555
    assert config.log_level == "warning"


def test_get_backend_config_falls_back_when_port_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAVRIS_BACKEND_PORT", "not-a-port")

    config = service_wrapper.get_backend_config()

    assert config.port == 8000


def test_apply_service_runtime_options_sets_cwd_and_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MARVIS_CONFIG_DIR", raising=False)
    monkeypatch.delenv("MAVRIS_BACKEND_HOST", raising=False)
    monkeypatch.delenv("MAVRIS_BACKEND_PORT", raising=False)
    monkeypatch.delenv("MAVRIS_BACKEND_LOG_LEVEL", raising=False)
    project_root = tmp_path / "project"
    project_root.mkdir()
    fake_util = SimpleNamespace(
        GetServiceCustomOption=MagicMock(
            side_effect=lambda _name, key, default=None: {
                "ProjectRoot": str(project_root),
                "BackendHost": "127.0.0.8",
                "BackendPort": "8124",
                "BackendLogLevel": "error",
            }.get(key, default)
        )
    )

    with patch.object(
        service_wrapper,
        "import_pywin32_service_modules",
        return_value=SimpleNamespace(win32serviceutil=fake_util),
    ), patch.object(service_wrapper.platform, "system", return_value="Windows"):
        service_wrapper.apply_service_runtime_options()

    assert Path.cwd() == project_root
    assert os.environ["MARVIS_CONFIG_DIR"] == str(project_root)
    assert os.environ["MAVRIS_BACKEND_HOST"] == "127.0.0.8"
    assert os.environ["MAVRIS_BACKEND_PORT"] == "8124"
    assert os.environ["MAVRIS_BACKEND_LOG_LEVEL"] == "error"


def test_logs_directory_defaults_to_project_logs() -> None:
    assert service_wrapper.DEFAULT_LOG_DIR == service_wrapper.PROJECT_ROOT / "logs"


def test_configure_logging_adds_file_handler(tmp_path: Path) -> None:
    logger = logging.getLogger("test_configure_logging_adds_file_handler")
    logger.handlers.clear()

    service_wrapper.configure_logging(log_dir=tmp_path, logger=logger, event_log=False)

    logger.info("service test log line")
    for handler in logger.handlers:
        handler.flush()

    log_path = tmp_path / service_wrapper.SERVICE_LOG_FILENAME
    assert log_path.exists()
    assert "service test log line" in log_path.read_text(encoding="utf-8")


def test_configure_logging_skips_event_log_when_pywin32_missing(tmp_path: Path) -> None:
    logger = logging.getLogger("test_configure_logging_skips_event_log_when_pywin32_missing")
    logger.handlers.clear()

    with patch.object(service_wrapper, "import_pywin32_service_modules", return_value=None):
        service_wrapper.configure_logging(log_dir=tmp_path, logger=logger, event_log=True)

    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.FileHandler)


def test_create_uvicorn_server_loads_backend_app_after_runtime_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAVRIS_BACKEND_HOST", "127.0.0.2")
    monkeypatch.setenv("MAVRIS_BACKEND_PORT", "9001")
    monkeypatch.setenv("MAVRIS_BACKEND_LOG_LEVEL", "warning")
    app = object()

    fake_config = MagicMock(name="Config")
    fake_server = MagicMock(name="Server")
    uvicorn_module = SimpleNamespace(
        Config=MagicMock(return_value=fake_config),
        Server=MagicMock(return_value=fake_server),
    )

    with patch.object(service_wrapper, "load_backend_app", return_value=app) as load_app:
        server = service_wrapper.create_uvicorn_server(uvicorn_module=uvicorn_module)

    assert server is fake_server
    load_app.assert_called_once_with()
    uvicorn_module.Config.assert_called_once_with(
        app,
        host="127.0.0.2",
        port=9001,
        log_level="warning",
        timeout_graceful_shutdown=10,
    )
    uvicorn_module.Server.assert_called_once_with(fake_config)


def test_service_runner_start_and_stop_controls_uvicorn_thread() -> None:
    fake_server = MagicMock()
    fake_server.started = True

    runner = service_wrapper.ServiceRunner(server_factory=lambda: fake_server)
    runner.start()

    assert runner.thread is not None
    runner.thread.join(timeout=1)
    assert fake_server.run.called

    runner.stop(timeout=1)

    assert fake_server.should_exit is True


def test_service_runner_raises_when_server_exits_before_start() -> None:
    fake_server = MagicMock()
    fake_server.started = False

    runner = service_wrapper.ServiceRunner(server_factory=lambda: fake_server)

    with pytest.raises(RuntimeError, match="exited before startup"):
        runner.start(timeout=1)


def test_service_runner_stop_reports_stuck_thread() -> None:
    fake_server = MagicMock()
    runner = service_wrapper.ServiceRunner(server_factory=lambda: fake_server)
    runner.server = fake_server
    runner.thread = MagicMock()
    runner.thread.is_alive.return_value = True

    assert runner.stop(timeout=1) is False
    assert fake_server.should_exit is True


def test_get_service_class_returns_stub_when_not_windows() -> None:
    with (
        patch.object(service_wrapper.platform, "system", return_value="Linux"),
        patch.object(service_wrapper, "import_pywin32_service_modules") as import_modules,
    ):
        service_class = service_wrapper.get_service_class()

    assert service_class is service_wrapper.UnsupportedService
    import_modules.assert_not_called()


def test_get_service_class_returns_stub_when_pywin32_missing() -> None:
    with (
        patch.object(service_wrapper.platform, "system", return_value="Windows"),
        patch.object(service_wrapper, "import_pywin32_service_modules", return_value=None),
    ):
        service_class = service_wrapper.get_service_class()

    assert service_class is service_wrapper.UnsupportedService


def test_get_service_class_builds_pywin32_service() -> None:
    class FakeServiceFramework:
        def __init__(self, args):
            self.args = args

        def ReportServiceStatus(self, status):
            self.last_status = status

    fake_win32event = SimpleNamespace(
        CreateEvent=MagicMock(return_value="stop-event"),
        SetEvent=MagicMock(),
        WaitForSingleObject=MagicMock(),
        INFINITE=999,
    )
    fake_win32service = SimpleNamespace(
        SERVICE_START_PENDING=2,
        SERVICE_STOP_PENDING=3,
        SERVICE_STOPPED=1,
        SERVICE_RUNNING=4,
    )
    fake_win32serviceutil = SimpleNamespace(ServiceFramework=FakeServiceFramework)
    modules = service_wrapper.Pywin32ServiceModules(
        servicemanager=SimpleNamespace(LogInfoMsg=MagicMock(), LogErrorMsg=MagicMock()),
        win32event=fake_win32event,
        win32service=fake_win32service,
        win32serviceutil=fake_win32serviceutil,
    )
    runner = MagicMock()

    with (
        patch.object(service_wrapper.platform, "system", return_value="Windows"),
        patch.object(service_wrapper, "import_pywin32_service_modules", return_value=modules),
        patch.object(service_wrapper, "configure_logging"),
    ):
        service_class = service_wrapper.get_service_class(runner_factory=lambda: runner)

    service = service_class(["service-arg"])
    service.SvcDoRun()
    service.SvcStop()

    assert issubclass(service_class, FakeServiceFramework)
    assert service_class._svc_name_ == service_wrapper.SERVICE_NAME
    assert service_class._svc_display_name_ == service_wrapper.SERVICE_DISPLAY_NAME
    assert service_class._exe_name_ == sys.executable
    assert service_wrapper.SERVICE_DISPATCH_COMMAND in service_class._exe_args_
    runner.start.assert_called_once()
    runner.wait.assert_called_once()
    runner.stop.assert_called_once()
    fake_win32event.SetEvent.assert_called_once_with("stop-event")


def test_main_returns_error_when_service_is_unsupported(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(service_wrapper, "get_service_class", return_value=service_wrapper.UnsupportedService):
        assert service_wrapper.main(["status"]) == 1

    assert "only available on Windows" in capsys.readouterr().err


def test_main_queries_service_status(capsys: pytest.CaptureFixture[str]) -> None:
    fake_service = type("FakeService", (), {"_svc_name_": service_wrapper.SERVICE_NAME})
    fake_util = SimpleNamespace(QueryServiceStatus=MagicMock(return_value=(16, 4, 0, 0, 0, 0, 0)))

    with (
        patch.object(service_wrapper, "get_service_class", return_value=fake_service),
        patch.object(service_wrapper, "import_pywin32_service_modules", return_value=SimpleNamespace(win32serviceutil=fake_util)),
    ):
        result = service_wrapper.main(["query"])

    assert result == 0
    fake_util.QueryServiceStatus.assert_called_once_with(service_wrapper.SERVICE_NAME)
    assert "RUNNING" in capsys.readouterr().out


def test_main_query_handles_missing_service(capsys: pytest.CaptureFixture[str]) -> None:
    fake_service = type("FakeService", (), {"_svc_name_": service_wrapper.SERVICE_NAME})
    fake_util = SimpleNamespace(QueryServiceStatus=MagicMock(side_effect=RuntimeError("not installed")))

    with (
        patch.object(service_wrapper, "get_service_class", return_value=fake_service),
        patch.object(service_wrapper, "import_pywin32_service_modules", return_value=SimpleNamespace(win32serviceutil=fake_util)),
    ):
        result = service_wrapper.main(["query"])

    assert result == 1
    assert "Could not query" in capsys.readouterr().err


def test_main_delegates_lifecycle_commands_to_pywin32() -> None:
    fake_service = type("FakeService", (), {})
    fake_util = SimpleNamespace(HandleCommandLine=MagicMock(return_value=0))

    with (
        patch.object(service_wrapper, "get_service_class", return_value=fake_service),
        patch.object(service_wrapper, "import_pywin32_service_modules", return_value=SimpleNamespace(win32serviceutil=fake_util)),
    ):
        result = service_wrapper.main(["start", "--wait", "10"])

    assert result == 0
    fake_util.HandleCommandLine.assert_called_once_with(
        fake_service,
        serviceClassString=service_wrapper.SERVICE_CLASS_STRING,
        argv=["service_wrapper.py", "--wait", "10", "start"],
    )


def test_main_persists_install_options() -> None:
    fake_service = type("FakeService", (), {"_svc_name_": service_wrapper.SERVICE_NAME})
    fake_util = SimpleNamespace(
        HandleCommandLine=MagicMock(return_value=0),
        SetServiceCustomOption=MagicMock(),
    )

    with (
        patch.object(service_wrapper, "get_service_class", return_value=fake_service),
        patch.object(service_wrapper, "import_pywin32_service_modules", return_value=SimpleNamespace(win32serviceutil=fake_util)),
        patch.object(service_wrapper, "PROJECT_ROOT", Path("C:/repo")),
    ):
        result = service_wrapper.main(
            [
                "install",
                "--startup",
                "auto",
                "--project-root",
                "C:/work/mavris",
                "--backend-host",
                "0.0.0.0",
                "--backend-port",
                "9000",
                "--backend-log-level",
                "debug",
            ]
        )

    assert result == 0
    fake_util.HandleCommandLine.assert_called_once_with(
        fake_service,
        serviceClassString=service_wrapper.SERVICE_CLASS_STRING,
        argv=["service_wrapper.py", "--startup", "auto", "install"],
    )
    fake_util.SetServiceCustomOption.assert_any_call(service_wrapper.SERVICE_NAME, "ProjectRoot", "C:/work/mavris")
    fake_util.SetServiceCustomOption.assert_any_call(service_wrapper.SERVICE_NAME, "BackendHost", "0.0.0.0")
    fake_util.SetServiceCustomOption.assert_any_call(service_wrapper.SERVICE_NAME, "BackendPort", "9000")
    fake_util.SetServiceCustomOption.assert_any_call(service_wrapper.SERVICE_NAME, "BackendLogLevel", "debug")


def test_main_runs_service_dispatcher() -> None:
    fake_service = type("FakeService", (), {"_svc_name_": service_wrapper.SERVICE_NAME})
    fake_manager = SimpleNamespace(
        Initialize=MagicMock(),
        PrepareToHostSingle=MagicMock(),
        StartServiceCtrlDispatcher=MagicMock(),
    )

    with (
        patch.object(service_wrapper, "get_service_class", return_value=fake_service),
        patch.object(
            service_wrapper,
            "import_pywin32_service_modules",
            return_value=SimpleNamespace(servicemanager=fake_manager),
        ),
    ):
        result = service_wrapper.main([service_wrapper.SERVICE_DISPATCH_COMMAND])

    assert result == 0
    fake_manager.Initialize.assert_called_once()
    fake_manager.PrepareToHostSingle.assert_called_once_with(fake_service)
    fake_manager.StartServiceCtrlDispatcher.assert_called_once()


def test_main_accepts_pywin32_options_before_command() -> None:
    fake_service = type("FakeService", (), {})
    fake_util = SimpleNamespace(HandleCommandLine=MagicMock(return_value=0))

    with (
        patch.object(service_wrapper, "get_service_class", return_value=fake_service),
        patch.object(service_wrapper, "import_pywin32_service_modules", return_value=SimpleNamespace(win32serviceutil=fake_util)),
    ):
        result = service_wrapper.main(["--wait", "15", "restart"])

    assert result == 0
    fake_util.HandleCommandLine.assert_called_once_with(
        fake_service,
        serviceClassString=service_wrapper.SERVICE_CLASS_STRING,
        argv=["service_wrapper.py", "--wait", "15", "restart"],
    )
