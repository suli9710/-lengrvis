# Claude Code Vendor Runtime And Architecture Borrowing

Mavris vendors an authorized fixed Claude Code 2.6.5 source snapshot at
`vendor/claude-code`. The source was copied from
`C:\Users\Suli\Documents\claude-code-2.6.5\claude-code-2.6.5` without changing
Claude Code internals. The local provenance record is
`vendor/claude-code/MAVRIS_VENDOR_MANIFEST.md`.

The snapshot currently contains source, package metadata, tests, docs, and
workspace packages, but no built `dist/` directory. `package.json` exposes the
public CLI bins as:

- `ccb` -> `dist/cli-node.js`
- `claude-code-best` -> `dist/cli-node.js`
- `ccb-bun` -> `dist/cli-bun.js`

Mavris should treat those package bin entries as the runtime boundary. Backend
code must not import or call vendored files such as `src/QueryEngine.ts`,
`src/main.tsx`, or tool implementation modules directly.

## Borrowed Architecture Points

QueryEngine: Claude Code uses a conversation-scoped query engine that owns the
message lifecycle, tool calls, streaming events, compaction, and final assistant
messages. Mavris borrows the shape of a single orchestration owner per
conversation, but keeps the boundary at the CLI or a future stable adapter.

Tool orchestration: Claude Code composes built-in tools, MCP tools, agent tools,
permission checks, and filtered tool schemas before each query. Mavris should
borrow the pipeline idea: collect tools, filter by policy and workspace state,
then expose only the resulting tool surface to the model. Do not bind Mavris
tool routing to Claude Code's internal `Tool` TypeScript types.

Permission mode: Claude Code exposes CLI permission modes including `default`,
`acceptEdits`, `dontAsk`, `plan`, and `auto`. Mavris defaults developer runs to
`acceptEdits` while constraining the workspace and `allowedTools` from the
Mavris adapter. Mavris must not default to `--dangerously-skip-permissions` or
`bypassPermissions`.

AgentTool: Claude Code models subagent delegation as a tool. The useful design
to borrow is that an agent task is explicit, scoped, and observable like any
other tool use. Mavris should keep subagent execution under its own task and
review model rather than importing Claude Code's `AgentTool` internals.

Headless stream-json: Claude Code supports non-interactive streaming with
`--print --verbose --output-format stream-json`. Streaming input uses
`--input-format stream-json`, and permission prompts can be delegated over stdio
with `--permission-prompt-tool stdio`. Mavris should consume stdout as NDJSON and
treat stderr as diagnostics.

## Runtime Resolver

The backend adapter lives at `backend/app/integrations/claude_code.py`.

Responsibilities:

- resolve the vendored source root, defaulting to `vendor/claude-code`;
- allow the vendor root to be overridden with `MARVIS_CLAUDE_CODE_VENDOR_ROOT`;
- prefer the Node CLI (`dist/cli-node.js`) when a built dist exists;
- build a subprocess command for headless stream-json mode;
- build a child-process environment for OpenAI-compatible mode.

OpenAI-compatible is the default credential path:

- `CLAUDE_CODE_USE_OPENAI=1`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL` defaulting to `https://api.openai.com/v1`
- `OPENAI_DEFAULT_SONNET_MODEL`
- `OPENAI_DEFAULT_OPUS_MODEL`
- `OPENAI_DEFAULT_HAIKU_MODEL`
- `OPENAI_SMALL_FAST_MODEL`

The resolver strips Anthropic session credentials from the child environment by
default and does not provide an Anthropic-key fallback path.

If no built CLI exists, the adapter fails with a diagnostic instead of silently
falling back to a `claude`, `ccb`, or `claude-code-best` executable from `PATH`.
External commands are allowed only when explicitly configured with
`MARVIS_CLAUDE_CODE_COMMAND`. Generated `dist/` output may be ignored by the repo
root `.gitignore`, so review whether distribution artifacts should stay local or
need an explicit tracking exception.

## Backend Invocation Shape

The intended launch shape is:

```text
node vendor/claude-code/dist/cli-node.js --print --verbose --output-format stream-json --bare --permission-mode acceptEdits --add-dir <workspace> --allowedTools <controlled-list> <prompt>
```

For streaming input:

```text
node vendor/claude-code/dist/cli-node.js --print --verbose --output-format stream-json --bare --permission-mode acceptEdits --input-format stream-json --add-dir <workspace> --allowedTools <controlled-list>
```

Agent 2 can use the resolver to get the command, cwd, and environment, then own
the process lifecycle and event parsing inside DeveloperExecutionEngine.

## Review Notes

- Confirm the vendor snapshot provenance and licensing expectations.
- Confirm no backend code imports Claude Code internal TypeScript files.
- Confirm default runtime env is OpenAI-compatible and does not carry
  `ANTHROPIC_API_KEY`.
- Confirm no default or generated command includes
  `--dangerously-skip-permissions`.
- Decide whether built `vendor/claude-code/dist` should remain local or be
  committed with a `.gitignore` exception in a later change.
