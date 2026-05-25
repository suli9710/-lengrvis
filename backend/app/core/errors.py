from __future__ import annotations


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class SecurityError(AppError):
    def __init__(self, message: str, code: str = "security_error") -> None:
        super().__init__(code=code, message=message, status_code=403)


class StateTransitionError(AppError, ValueError):
    def __init__(self, source: str, target: str) -> None:
        self.source = source
        self.target = target
        super().__init__(
            code="invalid_state_transition",
            message=f"Invalid state transition {source} -> {target}",
            status_code=409,
        )
