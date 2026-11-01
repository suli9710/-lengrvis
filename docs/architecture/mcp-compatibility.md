# MCP compatibility

Last verified: 2026-11-01.

Lengrvis currently implements the **2025-11-25** protocol. A configurable
`protocol_version` is a compatibility declaration, not an implementation switch.
Unsupported versions are rejected before network access or subprocess startup,
including when `strict_lifecycle` is disabled. Server status exposes the supported
versions and an `unsupported_protocol` state.

| Protocol / feature | Support |
| --- | --- |
| 2025-11-25 Streamable HTTP | Implemented: initialize/initialized, session headers, tool/resource discovery, schema validation, progress/cancellation, bounded SSE responses |
| 2025-11-25 stdio | Implemented, subject to local execution and release-isolation policy |
| Legacy HTTP+SSE transport | Unsupported; use the implemented Streamable HTTP transport |
| 2026-07-28 | Unsupported; rejected explicitly |
| OAuth discovery / full OAuth client lifecycle | Not claimed by this compatibility matrix; configured credentials are not evidence of OAuth conformance |

The [2026-07-28 specification release](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
removes initialize/initialized and protocol sessions, introduces self-describing
requests, method/name routing headers and Multi Round-Trip Requests, and changes
authorization behavior. Supporting it requires a separate implementation or an
SDK migration; replacing the version constant would falsely advertise support.

A future migration should preserve the current policy, schema, response-size,
SSRF, credential and execution-isolation checks. Validate old-version compatibility
and new-version discovery/tool invocation against corresponding protocol fixtures,
then add only the optional extensions the product actually uses. OAuth work should
follow the [MCP security guidance](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices),
including issuer/audience separation, redirect validation and discovery SSRF controls.
