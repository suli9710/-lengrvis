Task mode: $task_mode
Authorized directories: $authorized_directories
Plan step description: $plan_step_description
Proposed tool: $proposed_tool
Proposed args: $proposed_args
Risk level: $risk_level
Allowed tools you may suggest: $allowed_tools
$observation_block

Respond with JSON: kind in {propose_tool, request_revision, done}. If propose_tool, include tool_name + args + rationale. If request_revision, include rationale + follow_up_question. If done, just include rationale.
