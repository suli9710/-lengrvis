from __future__ import annotations

LENGRVIS_CODE_DISPLAY_NAME = "Lengrvis Code"
LENGRVIS_CODE_ADAPTER_NAME = "lengrvis_code"
MAX_ADAPTER_EVENTS = 500

ERROR_LAUNCH_FAILURE = "launch_failure"
ERROR_BAD_NDJSON = "bad_ndjson"
ERROR_NON_ZERO_EXIT = "non_zero_exit"
ERROR_LENGRVIS_RESULT = "lengrvis_result_error"
ERROR_PERMISSION_DENIAL = "permission_denial"
ERROR_CANCELLED = "cancelled"
TERMINAL_ERROR_TYPES: tuple[str, ...] = (
    ERROR_LAUNCH_FAILURE,
    ERROR_BAD_NDJSON,
    ERROR_NON_ZERO_EXIT,
    ERROR_LENGRVIS_RESULT,
    ERROR_PERMISSION_DENIAL,
    ERROR_CANCELLED,
)
