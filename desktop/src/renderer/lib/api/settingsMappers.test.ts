import { describe, expect, it } from "vitest";

import {
  allowedDirectoriesForSettings,
  mapSettings,
  mergeDesktopOnlySettings,
  settingsPatchFor
} from "./settingsMappers";

describe("settings mapper contracts", () => {
  it("maps backend settings defaults and keeps only persistable MCP servers", () => {
    const settings = mapSettings({
      base_url: "http://127.0.0.1:8000",
      mode: "hybrid",
      permission_mode: "trusted_edits",
      wire_api: "responses",
      requires_openai_auth: false,
      allowed_directories: ["C:\\Users\\Suli\\Workspace"],
      onnx_execution_provider: "directml",
      mcp_servers: [
        { name: "Remote", url: " https://example.test/mcp ", enabled: true },
        { name: "Local", command: "node", args: ["server.js"], transport: "stdio" },
        { name: "Blank", url: "  ", enabled: true }
      ]
    });

    expect(settings).toMatchObject({
      apiBaseUrl: "http://127.0.0.1:8000",
      workspaceRoot: "C:\\Users\\Suli\\Workspace",
      allowedDirectories: ["C:\\Users\\Suli\\Workspace"],
      mode: "hybrid",
      permissionMode: "trusted_edits",
      wireApi: "responses",
      requiresOpenAiAuth: false,
      onnxExecutionProvider: "DirectML"
    });
    expect(settings.mcpServers).toEqual([
      { name: "Remote", url: "https://example.test/mcp", command: undefined, args: undefined, transport: undefined, auth: undefined, enabled: true },
      { name: "Local", url: "", command: "node", args: ["server.js"], transport: "stdio", auth: undefined, enabled: true }
    ]);
  });

  it("builds sparse settings patches and preserves workspace-root ordering", () => {
    const baseline = mapSettings({
      base_url: "http://127.0.0.1:8000",
      allowed_directories: ["C:\\Old", "D:\\Shared"],
      mcp_servers: [{ name: "Remote", url: "https://old.example/mcp", enabled: true }]
    });
    const next = {
      ...baseline,
      workspaceRoot: "C:\\New",
      allowedDirectories: ["C:\\Old", "D:\\Shared"],
      temperature: baseline.temperature + 0.1,
      mcpServers: [
        { name: "Remote", url: "https://new.example/mcp", enabled: true },
        { name: "Blank", url: "", enabled: true }
      ]
    };

    expect(allowedDirectoriesForSettings(next, baseline)).toEqual(["C:\\New", "D:\\Shared"]);
    expect(settingsPatchFor(next, baseline)).toEqual({
      temperature: baseline.temperature + 0.1,
      allowed_directories: ["C:\\New", "D:\\Shared"],
      mcp_servers: [{ name: "Remote", url: "https://new.example/mcp", enabled: true }]
    });
  });

  it("keeps desktop-only settings from the previous local draft", () => {
    const backendSettings = mapSettings({ mode: "efficiency" });
    const previous = {
      ...backendSettings,
      autoStartBackend: true,
      telemetryEnabled: true,
      compactMode: true,
      theme: "dark" as const
    };

    expect(mergeDesktopOnlySettings(backendSettings, previous)).toMatchObject({
      autoStartBackend: true,
      telemetryEnabled: true,
      compactMode: true,
      theme: "dark"
    });
  });
});
