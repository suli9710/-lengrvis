from __future__ import annotations

import subprocess

from app.core import process_tree


class FakeProcess:
    pid = 4242

    def __init__(self) -> None:
        self.killed = False

    def poll(self):  # noqa: ANN201
        return None

    def wait(self, timeout=None):  # noqa: ANN001, ANN201
        return -9

    def kill(self) -> None:
        self.killed = True


def test_windows_kill_process_tree_uses_taskkill(monkeypatch):
    calls: list[list[str]] = []
    process = FakeProcess()

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(process_tree.os, "name", "nt")
    monkeypatch.setattr(process_tree.subprocess, "run", fake_run)

    process_tree.kill_process_tree(process)  # type: ignore[arg-type]

    assert calls == [["taskkill", "/F", "/T", "/PID", "4242"]]
    assert process.killed is False


def test_posix_kill_process_tree_uses_process_group(monkeypatch):
    calls: list[tuple[int, int]] = []
    process = FakeProcess()

    monkeypatch.setattr(process_tree.os, "name", "posix")
    monkeypatch.setattr(process_tree.os, "killpg", lambda pid, sig: calls.append((pid, sig)), raising=False)

    process_tree.kill_process_tree(process)  # type: ignore[arg-type]

    assert calls == [(4242, getattr(process_tree.signal, "SIGKILL", process_tree.signal.SIGTERM))]
    assert process.killed is False


def test_process_tree_popen_kwargs_creates_new_windows_process_group(monkeypatch):
    monkeypatch.setattr(process_tree.os, "name", "nt")
    monkeypatch.setattr(process_tree.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(process_tree.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    kwargs = process_tree.process_tree_popen_kwargs(hide_window=True)

    assert kwargs["creationflags"] & 0x200
    assert kwargs["creationflags"] & 0x08000000


def test_process_tree_popen_kwargs_starts_posix_session(monkeypatch):
    monkeypatch.setattr(process_tree.os, "name", "posix")

    assert process_tree.process_tree_popen_kwargs() == {"start_new_session": True}
