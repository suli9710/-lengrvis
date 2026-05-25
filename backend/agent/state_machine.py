from __future__ import annotations


class StateMachine:
    allowed = {
        "idle": {"queued"},
        "queued": {"running"},
        "running": {"completed", "failed"},
        "completed": set(),
        "failed": set(),
    }

    def __init__(self) -> None:
        self.state = "idle"

    def transition(self, state: str) -> None:
        if state not in self.allowed.get(self.state, set()):
            raise ValueError(f"Invalid transition {self.state} -> {state}")
        self.state = state


AgentStateMachine = StateMachine
TaskStateMachine = StateMachine

