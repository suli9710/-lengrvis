Task mode: $task_mode
Authorized directories: $authorized_directories
Plan step description: $plan_step_description
Proposed tool: $proposed_tool
Proposed args: $proposed_args
Risk level: $risk_level
Allowed tools you may suggest: $allowed_tools
$observation_block

Decide the next action:
- If the proposed tool and args are valid, complete, and inside authorized directories, confirm them with propose_tool.
- If the last observation reports a failure, never repeat the identical call. Diagnose from the error text and either propose ONE corrected alternative from your allowed tools, or request_revision with a follow_up_question naming exactly what is missing.
- Common failures and recoveries: path or file not found -> propose a read-only search or list tool first to find the real path; permission denied or path outside authorized directories -> request_revision asking for an authorized location; ambiguous target with multiple matches -> request_revision listing the candidates; missing required arg -> fill it from the step description only when certain, otherwise request_revision.
- Use done only when the step's outcome is already satisfied by a previous observation.

Respond with JSON: kind in {propose_tool, request_revision, done}. If propose_tool, include tool_name + args + rationale. If request_revision, include rationale + follow_up_question. If done, just include rationale.
