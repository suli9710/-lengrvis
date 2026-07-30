from __future__ import annotations

KNOWN_SUPERVISOR_WORKER_AGENTS = frozenset(
    {
        "ComputerAgent",
        "FileAgent",
        "BrowserAgent",
        "SearchAgent",
        "AppAgent",
        "DocumentAgent",
        "MemoryAgent",
    }
)


def normalize_supervisor_agent_hint(agent_hint: str | None) -> str:
    hint = str(agent_hint or "").strip()
    if hint in KNOWN_SUPERVISOR_WORKER_AGENTS:
        return hint
    return ""
