import { describe, expect, it } from "vitest";

import { validateSettingsPatchRequest } from "./settings";

describe("MCP settings IPC validation", () => {
  it("preserves reviewed stdio policy and environment references", () => {
    expect(
      validateSettingsPatchRequest({
        mcp_servers: [
          {
            name: "local-tools",
            command: "node",
            args: ["server.js"],
            transport: "stdio",
            inherit_env: ["MCP_TOKEN"],
            owner: "security",
            policy_id: "SEC-MCP-1",
            allowed_tools: ["read"],
            protocol_version: "2025-11-25",
            strict_lifecycle: true,
            auth: {
              required: true,
              resource: "https://mcp.example/mcp",
              token_env: "MCP_TOKEN"
            }
          }
        ]
      })
    ).toEqual({
      mcp_servers: [
        {
          name: "local-tools",
          command: "node",
          args: ["server.js"],
          transport: "stdio",
          inherit_env: ["MCP_TOKEN"],
          owner: "security",
          policy_id: "SEC-MCP-1",
          allowed_tools: ["read"],
          protocol_version: "2025-11-25",
          strict_lifecycle: true,
          auth: {
            required: true,
            resource: "https://mcp.example/mcp",
            token_env: "MCP_TOKEN"
          }
        }
      ]
    });
  });

  it("rejects raw MCP bearer and client secrets", () => {
    expect(() =>
      validateSettingsPatchRequest({
        mcp_servers: [
          {
            name: "remote",
            url: "https://mcp.example/mcp",
            auth: { token: "must-not-cross-ipc" }
          }
        ]
      })
    ).toThrow(/field is not allowed: token/i);
    expect(() =>
      validateSettingsPatchRequest({
        mcp_servers: [
          {
            name: "remote",
            url: "https://mcp.example/mcp",
            auth: { client_secret: "must-not-cross-ipc" }
          }
        ]
      })
    ).toThrow(/field is not allowed: client_secret/i);
  });

  it("rejects direct child environment values", () => {
    expect(() =>
      validateSettingsPatchRequest({
        mcp_servers: [
          {
            name: "local",
            command: "node",
            transport: "stdio",
            env: { MCP_TOKEN: "must-not-cross-ipc" }
          }
        ]
      })
    ).toThrow(/field is not allowed: env/i);
  });
});
