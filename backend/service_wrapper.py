from __future__ import annotations

import logging
import os
import platform
import sys
import threading
import time
from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from importlib import import_module
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"
SERVICE_LOG_FILENAME = "mavris-service.log"
SERVICE_NAME = "MavrisBackend"
SERVICE_DISPLAY_NAME = "Mavris Backend Service"
SERVICE_DESCRIPTION = "Runs the Mavris FastAPI backend as a Windows service."
SERVICE_CLASS_STRING = "backend.service_wrapper.MavrisBackendService"
EVENT_LOG_SOURCE = SERVICE_DISPLAY_NAME
DEFAULT_STOP_TIMEOUT_SECONDS = 30
DEFAULT_START_TIMEOUT_SECONDS = 30
SERVICE_DISPATCH_COMMAND = "runservice"
SERVICE_OPTION_PROJECT_ROOT = "ProjectRoot"
SERVICE_OPTION_BACKEND_HOST = "BackendHost"
SERVICE_OPTION_BACKEND_PORT = "BackendPort"
SERVICE_OPTION_BACKEND_LOG_LEVEL = "BackendLogLevel"
SERVICE_COMMANDS = {
    "debug",
    "install",
    "query",
    "remove",
    "restart",
    "runservice",
    "start",
    "status",
    "stop",
    "uninstall",
    "update",
}
PYWIN32_OPTIONS_WITH_VALUE = {
    "--password",
    "--perfmondll",
    "--perfmonini",
    "--startup",
    "--username",
    "--wait",
}
PYWIN32_FLAG_OPTIONS = {"--interactive"}

LOGGER = logging.getLogger("mavris.service")


@dataclass(frozen=True)
class BackendConfig:
    host: str
    port: int
    log_level: str


@dataclass(frozen=True)
class Pywin32ServiceModules:
    servicemanager: ModuleType
    win32event: ModuleType
    win32service: ModuleType
    win32serviceutil: ModuleType


class UnsupportedService:
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY_NAME
    _svc_description_ = SERVICE_DESCRIPTION


def _service_script_path() -> Path:
    return Path(__file__).resolve()


def _service_exe_args() -> str:
    return f'"{_service_script_path()}" {SERVICE_DISPATCH_COMMAND}'


def get_backend_config() -> BackendConfig:
    host = os.environ.get("MAVRIS_BACKEND_HOST") or _get_service_option(
        SERVICE_OPTION_BACKEND_HOST,
        "127.0.0.1",
    )
    try:
        port = int(
            os.environ.get("MAVRIS_BACKEND_PORT")
            or _get_service_option(SERVICE_OPTION_BACKEND_PORT, "8000")
        )
    except ValueError:
        LOGGER.warning("Invalid MAVRIS_BACKEND_PORT; falling back to 8000.")
        port = 8000
    log_level = os.environ.get("MAVRIS_BACKEND_LOG_LEVEL") or _get_service_option(
        SERVICE_OPTION_BACKEND_LOG_LEVEL,
        "info",
    )
    return BackendConfig(host=host, port=port, log_level=log_level)


def import_pywin32_service_modules() -> Pywin32ServiceModules | None:
    try:
        import servicemanager  # type: ignore[import-not-found]
        import win32event  # type: ignore[import-not-found]
        import win32service  # type: ignore[import-not-found]
        import win32serviceutil  # type: ignore[import-not-found]
    except ImportError:
        return None
    return Pywin32ServiceModules(
        servicemanager=servicemanager,
        win32event=win32event,
        win32service=win32service,
        win32serviceutil=win32serviceutil,
    )


def _get_service_option(option: str, default: str) -> str:
    if platform.system() != "Windows":
        return default
    modules = import_pywin32_service_modules()
    if modules is None:
        return default
    try:
        value = modules.win32serviceutil.GetServiceCustomOption(SERVICE_NAME, option, default)
    except Exception:  # noqa: BLE001
        return default
    return str(value) if value not in (None, "") else default


def apply_service_runtime_options() -> None:
    if platform.system() != "Windows":
        return
    modules = import_pywin32_service_modules()
    if modules is None:
        return
    try:
        project_root = modules.win32serviceutil.GetServiceCustomOption(
            SERVICE_NAME,
            SERVICE_OPTION_PROJECT_ROOT,
            str(PROJECT_ROOT),
        )
    except Exception:  # noqa: BLE001
        project_root = str(PROJECT_ROOT)

    root_path = Path(str(project_root)).resolve()
    if root_path.exists():
        os.chdir(root_path)
        for import_root in (root_path / "backend", root_path):
            if str(import_root) not in sys.path:
                sys.path.insert(0, str(import_root))
        os.environ["MARVIS_CONFIG_DIR"] = str(root_path)

    option_to_env = {
        SERVICE_OPTION_BACKEND_HOST: "MAVRIS_BACKEND_HOST",
        SERVICE_OPTION_BACKEND_PORT: "MAVRIS_BACKEND_PORT",
        SERVICE_OPTION_BACKEND_LOG_LEVEL: "MAVRIS_BACKEND_LOG_LEVEL",
    }
    for option, env_key in option_to_env.items():
        try:
            value = modules.win32serviceutil.GetServiceCustomOption(SERVICE_NAME, option, "")
        except Exception:  # noqa: BLE001
            continue
        if value not in (None, ""):
            os.environ[env_key] = str(value)


def _set_windows_event_source(source: str) -> None:
    if platform.system() != "Windows":
        return
    try:
        import win32evtlogutil  # type: ignore[import-not-found]
    except ImportError:
        return
    try:
        win32evtlogutil.AddSourceToRegistry(source, msgDLL=None, eventLogType="Application")
    except Exception:  # noqa: BLE001
        LOGGER.debug("Could not register Windows Event Log source.", exc_info=True)


def configure_logging(
    *,
    log_dir: Path = DEFAULT_LOG_DIR,
    logger: logging.Logger = LOGGER,
    event_log: bool = True,
) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    log_path = log_dir / SERVICE_LOG_FILENAME
    if not any(isinstance(handler, RotatingFileHandler) and getattr(handler, "baseFilename", None) == str(log_path) for handler in logger.handlers):
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        logger.addHandler(file_handler)

    if event_log and platform.system() == "Windows" and import_pywin32_service_modules() is not None:
        try:
            from logging.handlers import NTEventLogHandler

            if not any(isinstance(handler, NTEventLogHandler) for handler in logger.handlers):
                _set_windows_event_source(EVENT_LOG_SOURCE)
                event_handler = NTEventLogHandler(EVENT_LOG_SOURCE)
                event_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
                logger.addHandler(event_handler)
        except Exception:  # noqa: BLE001
            logger.debug("Windows Event Log handler is unavailable.", exc_info=True)

    return logger


def load_backend_app() -> Any:
    backend_main = import_module("backend.main")
    return backend_main.app


def create_uvicorn_server(*, uvicorn_module: Any = uvicorn) -> uvicorn.Server:
    config = get_backend_config()
    uvicorn_config = uvicorn_module.Config(
        load_backend_app(),
        host=config.host,
        port=config.port,
        log_level=config.log_level,
        timeout_graceful_shutdown=10,
    )
    return uvicorn_module.Server(uvicorn_config)


class ServiceRunner:
    def __init__(
        self,
        *,
        server_factory: Callable[[], Any] = create_uvicorn_server,
        logger: logging.Logger = LOGGER,
    ) -> None:
        self._server_factory = server_factory
        self._logger = logger
        self.server: Any | None = None
        self.thread: threading.Thread | None = None
        self._startup_error: BaseException | None = None
        self._server_exited = False

    def start(self, *, timeout: int = DEFAULT_START_TIMEOUT_SECONDS) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.server = self._server_factory()
        self._startup_error = None
        self._server_exited = False
        self.thread = threading.Thread(target=self._run_server, name="mavris-uvicorn", daemon=True)
        self.thread.start()
        self._wait_until_started(timeout=timeout)

    def _run_server(self) -> None:
        assert self.server is not None
        config = get_backend_config()
        self._logger.info("Starting Mavris backend on %s:%s.", config.host, config.port)
        try:
            self.server.run()
        except Exception as exc:  # noqa: BLE001
            self._startup_error = exc
            self._logger.exception("Mavris backend server failed.")
        finally:
            self._server_exited = True
            self._logger.info("Mavris backend server stopped.")

    def _wait_until_started(self, *, timeout: int) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._startup_error is not None:
                raise RuntimeError("Mavris backend failed during startup.") from self._startup_error
            if self.server is not None and bool(getattr(self.server, "started", False)):
                return
            if self._server_exited:
                raise RuntimeError("Mavris backend exited before startup completed.")
            time.sleep(0.1)

        self.stop(timeout=5)
        raise TimeoutError(f"Mavris backend did not start within {timeout} seconds.")

    def stop(self, *, timeout: int = DEFAULT_STOP_TIMEOUT_SECONDS) -> bool:
        self._logger.info("Stopping Mavris backend service.")
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is None:
            return True
        self.thread.join(timeout=timeout)
        if self.thread.is_alive():
            self._logger.warning("Mavris backend did not stop within %s seconds.", timeout)
            return False
        return True

    def wait(self) -> None:
        if self.thread is not None:
            self.thread.join()


def get_service_class(
    *,
    runner_factory: Callable[[], ServiceRunner] = ServiceRunner,
) -> type[Any]:
    if platform.system() != "Windows":
        return UnsupportedService

    modules = import_pywin32_service_modules()
    if modules is None:
        return UnsupportedService

    class MavrisBackendServiceImpl(modules.win32serviceutil.ServiceFramework):  # type: ignore[name-defined]
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY_NAME
        _svc_description_ = SERVICE_DESCRIPTION
        _exe_name_ = sys.executable
        _exe_args_ = _service_exe_args()

        def __init__(self, args: list[str]) -> None:
            super().__init__(args)
            self.stop_event = modules.win32event.CreateEvent(None, 0, 0, None)
            self.runner = runner_factory()

        def SvcStop(self) -> None:
            configure_logging()
            LOGGER.info("Service stop requested.")
            self.ReportServiceStatus(modules.win32service.SERVICE_STOP_PENDING)
            stopped = self.runner.stop()
            if not stopped:
                message = f"{SERVICE_DISPLAY_NAME} did not stop within {DEFAULT_STOP_TIMEOUT_SECONDS} seconds."
                LOGGER.error(message)
                modules.servicemanager.LogErrorMsg(message)
                raise TimeoutError(message)
            modules.win32event.SetEvent(self.stop_event)
            self.ReportServiceStatus(modules.win32service.SERVICE_STOPPED)

        def SvcDoRun(self) -> None:
            apply_service_runtime_options()
            configure_logging()
            LOGGER.info("Service run requested.")
            modules.servicemanager.LogInfoMsg(f"{SERVICE_DISPLAY_NAME} is starting.")
            self.ReportServiceStatus(modules.win32service.SERVICE_START_PENDING)
            try:
                self.runner.start()
                self.ReportServiceStatus(modules.win32service.SERVICE_RUNNING)
                modules.servicemanager.LogInfoMsg(f"{SERVICE_DISPLAY_NAME} is running.")
                self.runner.wait()
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Service run failed.")
                modules.servicemanager.LogErrorMsg(f"{SERVICE_DISPLAY_NAME} failed: {exc}")
                raise
            finally:
                self.ReportServiceStatus(modules.win32service.SERVICE_STOPPED)

    MavrisBackendServiceImpl.__name__ = "MavrisBackendService"
    MavrisBackendServiceImpl.__qualname__ = "MavrisBackendService"
    MavrisBackendServiceImpl.__module__ = __name__
    return MavrisBackendServiceImpl


def _split_command(argv: list[str]) -> tuple[str, list[str]]:
    for index, token in enumerate(argv):
        if token.lower() in SERVICE_COMMANDS:
            return token, [*argv[:index], *argv[index + 1 :]]
    return "query", argv


def _parse_cli(argv: list[str]) -> Namespace:
    command, remainder = _split_command(argv)
    parser = ArgumentParser(add_help=True)
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--backend-host", default=None)
    parser.add_argument("--backend-port", default=None)
    parser.add_argument("--backend-log-level", default=None)
    parsed, service_args = parser.parse_known_args(remainder)
    parsed.command = command
    parsed.service_args = service_args
    return parsed


def _normalize_command(command: str) -> str:
    lowered = command.lower()
    if lowered == "uninstall":
        return "remove"
    return lowered


def _persist_service_options(args: Namespace, modules: Pywin32ServiceModules) -> None:
    options = {
        SERVICE_OPTION_PROJECT_ROOT: args.project_root,
        SERVICE_OPTION_BACKEND_HOST: args.backend_host or os.environ.get("MAVRIS_BACKEND_HOST", "127.0.0.1"),
        SERVICE_OPTION_BACKEND_PORT: args.backend_port or os.environ.get("MAVRIS_BACKEND_PORT", "8000"),
        SERVICE_OPTION_BACKEND_LOG_LEVEL: args.backend_log_level or os.environ.get("MAVRIS_BACKEND_LOG_LEVEL", "info"),
    }
    for key, value in options.items():
        modules.win32serviceutil.SetServiceCustomOption(SERVICE_NAME, key, str(value))


def _split_pywin32_options(tokens: list[str]) -> tuple[list[str], list[str]]:
    pywin32_options: list[str] = []
    service_args: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        option_name = token.split("=", 1)[0]
        if token in PYWIN32_FLAG_OPTIONS:
            pywin32_options.append(token)
        elif option_name in PYWIN32_OPTIONS_WITH_VALUE:
            pywin32_options.append(token)
            if "=" not in token and index + 1 < len(tokens):
                index += 1
                pywin32_options.append(tokens[index])
        else:
            service_args.append(token)
        index += 1
    return pywin32_options, service_args


def _status_name(state: int) -> str:
    names = {
        1: "STOPPED",
        2: "START_PENDING",
        3: "STOP_PENDING",
        4: "RUNNING",
        5: "CONTINUE_PENDING",
        6: "PAUSE_PENDING",
        7: "PAUSED",
    }
    return names.get(state, f"UNKNOWN({state})")


def _query_status(service_name: str, modules: Pywin32ServiceModules) -> int:
    try:
        status = modules.win32serviceutil.QueryServiceStatus(service_name)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not query {service_name}: {exc}", file=sys.stderr)
        return 1
    state = int(status[1])
    print(f"{service_name}: {_status_name(state)}")
    return 0


def _run_service_dispatcher(service_class: type[Any], modules: Pywin32ServiceModules) -> int:
    modules.servicemanager.Initialize()
    modules.servicemanager.PrepareToHostSingle(service_class)
    modules.servicemanager.StartServiceCtrlDispatcher()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parsed = _parse_cli(argv)
    service_class = get_service_class()
    if service_class is UnsupportedService:
        print(
            f"{SERVICE_DISPLAY_NAME} is only available on Windows with pywin32 installed.",
            file=sys.stderr,
        )
        return 1

    modules = import_pywin32_service_modules()
    if modules is None:
        print("pywin32 service modules are not available.", file=sys.stderr)
        return 1

    command = _normalize_command(parsed.command)
    if command == SERVICE_DISPATCH_COMMAND:
        return _run_service_dispatcher(service_class, modules)
    if command in {"query", "status"}:
        return _query_status(service_class._svc_name_, modules)
    pywin32_options, service_args = _split_pywin32_options(parsed.service_args)
    command_argv = ["service_wrapper.py", *pywin32_options, command, *service_args]
    result = int(
        modules.win32serviceutil.HandleCommandLine(
            service_class,
            serviceClassString=SERVICE_CLASS_STRING,
            argv=command_argv,
        )
        or 0
    )
    if result == 0 and command in {"install", "update"}:
        _persist_service_options(parsed, modules)
    return result


MavrisBackendService = get_service_class()


if __name__ == "__main__":
    raise SystemExit(main())
