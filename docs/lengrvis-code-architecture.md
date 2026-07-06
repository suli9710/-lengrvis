# Lengrvis Code Vendor Runtime And Architecture Boundary

Lengrvis Code is the in-product name for Lengrvis's vendored coding-engine
runtime. The source snapshot can be restored locally under
`vendor/lengrvis-code`, while the tracked provenance record lives at
`docs/vendors/lengrvis-code-vendor-manifest.md`.

The snapshot currently contains source, package metadata, tests, docs,
workspace packages, and built `dist/` output. The upstream package metadata
exposes CLI bins such as:

- `ccb` -> `dist/cli-node.js`
- `lengrvis-code-best` -> `dist/cli-node.js`
- `ccb-bun` -> `dist/cli-bun.js`

Lengrvis treats those package bin entries as the runtime boundary. Backend code
must not import or call vendored files such as `src/QueryEngine.ts`,
`src/main.tsx`, or tool implementation modules directly.

## Borrowed Architecture Points

Query engine: the vendored runtime uses a conversation-scoped query engine that
owns the message lifecycle, tool calls, streaming events, compaction, and final
assistant messages. Lengrvis borrows the shape of a single orchestration owner
per conversation, but keeps the boundary at the CLI or a future stable adapter.

Tool orchestration: the vendored runtime composes built-in tools, MCP tools,
agent tools, permission checks, and filtered tool schemas before each query.
Lengrvis borrows the pipeline idea: collect tools, filter by policy and
workspace state, then expose only the resulting tool surface to the model. Do
not bind Lengrvis tool routing to vendored internal TypeScript types.

Permission mode: the runtime exposes CLI permission modes including `default`,
`acceptEdits`, `dontAsk`, `plan`, and `auto`. Lengrvis constrains developer runs
through the workspace, `allowedTools`, and its adapter. Lengrvis must not default
to `--dangerously-skip-permissions` or `bypassPermissions`.

Agent delegation: the useful design to borrow is that an agent task is explicit,
scoped, and observable like any other tool use. Lengrvis keeps subagent
execution under its own task and review model instead of importing vendored
agent internals.

Headless stream-json: Lengrvis Code supports non-interactive streaming with
`--print --verbose --output-format stream-json`. Streaming input uses
`--input-format stream-json`, and permission prompts can be delegated over stdio
with `--permission-prompt-tool stdio`. Lengrvis consumes stdout as NDJSON and
treats stderr as diagnostics.

## Runtime Resolver

The backend adapter lives at `backend/app/integrations/lengrvis_code.py`.

Responsibilities:

- resolve the vendored source root, defaulting to `vendor/lengrvis-code`;
- allow the vendor root to be overridden with `LENGRVIS_CODE_VENDOR_ROOT`;
- prefer the Node CLI (`dist/cli-node.js`) when a built dist exists;
- build a subprocess command for headless stream-json mode;
- build a child-process environment for OpenAI-compatible mode.

OpenAI-compatible is the default credential path:

- `LENGRVIS_CODE_USE_OPENAI=1`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL` defaulting to `https://lengzhehao.com/v1`
- `OPENAI_DEFAULT_SONNET_MODEL`
- `OPENAI_DEFAULT_OPUS_MODEL`
- `OPENAI_DEFAULT_HAIKU_MODEL`
- `OPENAI_SMALL_FAST_MODEL`

The resolver strips Anthropic session credentials from the child environment by
default and does not provide an Anthropic-key fallback path.

If no built CLI exists, the adapter fails with a diagnostic instead of silently
falling back to an executable from `PATH`. External commands are allowed only
when explicitly configured with `LENGRVIS_CODE_COMMAND`.

## Backend Invocation Shape

The intended launch shape is:

```text
node vendor/lengrvis-code/dist/cli-node.js --print --verbose --output-format stream-json --bare --permission-mode acceptEdits --add-dir <workspace> --allowedTools <controlled-list> <prompt>
```

For streaming input:

```text
node vendor/lengrvis-code/dist/cli-node.js --print --verbose --output-format stream-json --bare --permission-mode acceptEdits --input-format stream-json --add-dir <workspace> --allowedTools <controlled-list>
```

DeveloperExecutionEngine uses the resolver to get the command, cwd, and
environment, then owns the process lifecycle and event parsing.

## Review Notes

- Confirm the vendor snapshot provenance and licensing expectations.
- Confirm no backend code imports vendored internal TypeScript files.
- Confirm default runtime env is OpenAI-compatible and does not carry
  `ANTHROPIC_API_KEY`.
- Confirm no default or generated command includes
  `--dangerously-skip-permissions`.
- Decide whether built `vendor/lengrvis-code/dist` should remain local or be
  committed with a `.gitignore` exception in a later change.
