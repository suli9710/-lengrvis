from __future__ import annotations

import http.server
import json
import os
import socketserver
import threading
from pathlib import Path

import pytest

from app.config import AppSettings
from app.policy.risk import RiskLevel
from app.skills.loader import load_skill_package, scan_skill_directories
from app.skills.schemas import SkillLoadError
from app.tools.registry import register_all_tools


def _make_http_skill_handler():
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(body)
            response = {
                "ok": True,
                "echo": payload.get("args", {}).get("text", ""),
                "saw_context": "context" in payload,
            }
            encoded = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


@pytest.fixture
def http_skill_server():
    server = socketserver.TCPServer(("127.0.0.1", 0), _make_http_skill_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _write_demo_skill(root: Path) -> Path:
    skill_root = root / "demo_skill"
    skill_root.mkdir()
    shell_entry = "handlers/shell_pid.ps1" if os.name == "nt" else "handlers/shell_pid.sh"
    (skill_root / "skill.yaml").write_text(
        f"""
name: demo-skill
version: "1.0.0"
agent_owner: FileAgent
risk: R0_READ_ONLY
tools:
  - name: skill.demo.echo
    description: Echo text from a Python skill handler.
    risk: R0_READ_ONLY
    input_schema:
      type: object
      properties:
        text:
          type: string
      required:
        - text
    execution:
      type: python
      entry: handlers/echo.py
  - name: skill.demo.shell_pid
    description: Return the shell handler process id.
    execution:
      type: shell
      entry: {shell_entry}
""".strip(),
        encoding="utf-8",
    )
    handlers = skill_root / "handlers"
    handlers.mkdir()
    (handlers / "echo.py").write_text(
        """
import json
import sys

payload = json.loads(sys.stdin.read())
text = payload.get("args", {}).get("text", "")
print(json.dumps({"ok": True, "echo": text, "context": payload.get("context", {})}))
""".strip(),
        encoding="utf-8",
    )
    if os.name == "nt":
        (handlers / "shell_pid.ps1").write_text(
            "$input | Out-Null\n@{ ok = $true; pid = $PID } | ConvertTo-Json -Compress\n",
            encoding="utf-8",
        )
    else:
        shell_file = handlers / "shell_pid.sh"
        shell_file.write_text(
            "cat >/dev/null\nprintf '{\"ok\":true,\"pid\":%s}\\n' \"$$\"\n",
            encoding="utf-8",
        )
        shell_file.chmod(0o755)
    return skill_root


def test_skill_yaml_loads_declared_schema_and_tool_definition(tmp_path: Path):
    skill_root = _write_demo_skill(tmp_path)

    package = load_skill_package(skill_root)

    assert package.definition.name == "demo-skill"
    assert package.definition.version == "1.0.0"
    assert package.definition.agent_owner == "FileAgent"
    assert len(package.tool_definitions) == 2
    echo_tool = next(tool for tool in package.tool_definitions if tool.name == "skill.demo.echo")
    assert echo_tool.risk_level == RiskLevel.R0_READ_ONLY
    assert echo_tool.agent_owner == "FileAgent"
    assert echo_tool.input_schema["required"] == ["text"]


def test_loader_scans_skill_directory_and_runtime_registers_tool(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_demo_skill(skills_dir)
    settings = AppSettings(
        provider_name="mock",
        data_dir=str(tmp_path / "data"),
        skill_directories=[str(skills_dir)],
    )

    packages = scan_skill_directories([skills_dir])
    registry = register_all_tools(settings=settings)
    tool = registry.get("skill.demo.echo")
    result = tool.execute({"text": "hello skill"}, {"allowed_directories": [str(tmp_path)], "settings": settings})

    assert [package.definition.name for package in packages] == ["demo-skill"]
    assert result["ok"] is True
    assert result["echo"] == "hello skill"
    assert result["context"]["allowed_directories"] == [str(tmp_path)]


def test_shell_handler_runs_in_bounded_child_process(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_demo_skill(skills_dir)
    registry = register_all_tools(
        settings=AppSettings(provider_name="mock", skill_directories=[str(skills_dir)]),
    )

    result = registry.get("skill.demo.shell_pid").execute({}, {})

    assert result["ok"] is True
    assert result["pid"] != os.getpid()


def test_invalid_skill_definition_fails_clearly(tmp_path: Path):
    skill_root = tmp_path / "bad_skill"
    skill_root.mkdir()
    (skill_root / "skill.yaml").write_text(
        """
name: bad skill
version: "1.0"
agent_owner: FileAgent
tools:
  - name: skill.bad
    execution:
      type: python
      entry: handlers/missing.py
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(SkillLoadError, match="Invalid skill.yaml"):
        load_skill_package(skill_root)


def test_unsafe_local_entry_is_rejected_before_execution(tmp_path: Path):
    skill_root = tmp_path / "unsafe_skill"
    skill_root.mkdir()
    (skill_root / "skill.yaml").write_text(
        """
name: unsafe-skill
version: "1.0"
agent_owner: FileAgent
tools:
  - name: skill.unsafe.escape
    execution:
      type: python
      entry: ../outside.py
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(SkillLoadError, match="path traversal|Unsafe skill definition"):
        load_skill_package(skill_root)


def test_handler_timeout_returns_inline_error(tmp_path: Path):
    skill_root = tmp_path / "slow_skill"
    skill_root.mkdir()
    (skill_root / "skill.yaml").write_text(
        """
name: slow-skill
version: "1.0"
agent_owner: FileAgent
tools:
  - name: skill.slow.wait
    execution:
      type: python
      entry: slow.py
      timeout_seconds: 0.1
""".strip(),
        encoding="utf-8",
    )
    (skill_root / "slow.py").write_text(
        "import time\ntime.sleep(2)\nprint('{}')\n",
        encoding="utf-8",
    )

    package = load_skill_package(skill_root)
    result = package.tool_definitions[0].execute({}, {})

    assert "timed out" in result["error"]


def test_http_handler_skill_executes_through_http_sandbox(tmp_path: Path, http_skill_server: str):
    skill_root = tmp_path / "http_skill"
    skill_root.mkdir()
    (skill_root / "skill.yaml").write_text(
        f"""
name: http-skill
version: "1.0"
agent_owner: SearchAgent
tools:
  - name: skill.http.echo
    execution:
      type: http
      entry: {http_skill_server}
""".strip(),
        encoding="utf-8",
    )

    package = load_skill_package(skill_root)
    result = package.tool_definitions[0].execute({"text": "via http"}, {})

    assert result == {"ok": True, "echo": "via http", "saw_context": True}


def test_http_handler_rejects_non_loopback_entry(tmp_path: Path):
    skill_root = tmp_path / "remote_http_skill"
    skill_root.mkdir()
    (skill_root / "skill.yaml").write_text(
        """
name: remote-http-skill
version: "1.0"
agent_owner: SearchAgent
tools:
  - name: skill.http.remote
    execution:
      type: http
      entry: https://example.com/skill
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(SkillLoadError, match="loopback"):
        load_skill_package(skill_root)


def test_repository_demo_skill_loads_and_executes(test_data_dir: Path):
    packages = scan_skill_directories([test_data_dir / "skills"])
    demo = next(package for package in packages if package.definition.name == "demo-echo")
    result = demo.tool_definitions[0].execute({"text": "from fixture"}, {})

    assert demo.tool_definitions[0].name == "skill.demo.echo"
    assert result == {"ok": True, "echo": "from fixture"}
