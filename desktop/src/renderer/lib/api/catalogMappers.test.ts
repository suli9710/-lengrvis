import { describe, expect, it } from "vitest";

import {
  mapChatMessage,
  mapInstalledApp,
  mapInstalledSkill,
  mapIntentSuggestion,
  mapSkillsCatalog,
  mapSuggestionLaunchResponse,
  normalizeTimestamp
} from "./catalogMappers";

describe("catalog mappers", () => {
  it("normalizes installed apps and chat timestamps", () => {
    expect(mapInstalledApp({ id: undefined, name: "PowerShell", allowlisted: 1 as unknown as boolean })).toEqual({
      id: "PowerShell",
      name: "PowerShell",
      path: undefined,
      command: undefined,
      source: "unknown",
      allowlisted: true
    });

    expect(normalizeTimestamp("not-a-date", "2026-01-02T03:04:05.000Z")).toBe("2026-01-02T03:04:05.000Z");
    expect(
      mapChatMessage({
        id: "msg_1",
        role: "assistant",
        author: "Lengrvis",
        content: "plain backend text",
        created_at: "2026-01-02T03:04:05Z",
        status: "failed"
      })
    ).toMatchObject({
      id: "msg_1",
      role: "assistant",
      author: "Lengrvis",
      content: "plain backend text",
      createdAt: "2026-01-02T03:04:05.000Z",
      status: "failed"
    });
  });

  it("maps skills catalog defaults and coerces safety issues", () => {
    const backendSkill = {
      name: "files",
      version: "1.0.0",
      agent_owner: "desktop",
      risk: "R2",
      root: "C:\\Skills\\files",
      manifest_path: "C:\\Skills\\files\\skill.json",
      status: "ready",
      tools: [
        {
          name: "scan",
          permissions: ["read", 7],
          execution_type: "python",
          supports_dry_run: true,
          requires_authorized_path: true
        }
      ],
      safety: {
        ok: false,
        issues: [{ severity: "info", location: "manifest", message: "Missing rollback hint" }]
      }
    };
    const skill = mapInstalledSkill(backendSkill);

    expect(skill.tools[0]).toMatchObject({
      name: "scan",
      permissions: ["read", "7"],
      executionType: "python",
      supportsDryRun: true,
      requiresAuthorizedPath: true
    });
    expect(skill.safety.issues).toEqual([
      { severity: "error", location: "manifest", message: "Missing rollback hint" }
    ]);
    expect(mapSkillsCatalog({ skills: [backendSkill], directories: ["C:\\Skills"] }).count).toBe(1);
  });

  it("maps intent suggestions and launched suggestion task updates", () => {
    expect(
      mapIntentSuggestion({
        id: "sug_1",
        title: "Open settings",
        prompt: "open settings",
        confidence: undefined,
        agent_hint: "developer"
      })
    ).toEqual({
      id: "sug_1",
      title: "Open settings",
      prompt: "open settings",
      confidence: 0,
      agentHint: "developer",
      reason: undefined
    });

    expect(
      mapIntentSuggestion({
        id: "sug_2",
        title: "Analyze spreadsheet",
        prompt: "Analyze the visible spreadsheet and summarize the important numbers.",
        confidence: 0.91,
        reason: "Spreadsheet context is visible."
      })
    ).toMatchObject({
      title: "分析表格",
      prompt: "分析当前可见的表格，并总结重要数据。",
      reason: "检测到当前窗口包含表格内容。"
    });

    const launched = mapSuggestionLaunchResponse(
      {
        run: {
          run_id: "run_123",
          engine: "developer",
          phase: "waiting_user_approval",
          message: "Update docs",
          mode: "efficiency",
          requested_engine: "auto",
          created_at: "2026-02-03T04:05:06Z",
          updated_at: "2026-02-03T04:06:07Z",
          engine_capabilities: { writes_enabled: false }
        }
      },
      "fallback"
    );

    expect(launched).toMatchObject({
      runId: "run_123",
      engine: "developer",
      message: {
        id: "run_123-suggestion-launched",
        role: "assistant",
        author: "Lengrvis",
        content: "已根据建议启动任务：Update docs",
        status: "sent"
      },
      taskUpdates: [
        {
          id: "run_123",
          runId: "run_123",
          title: "Update docs",
          description: "状态：等待用户审批",
          state: "blocked",
          agent: "开发引擎（只读）",
          createdAt: "2026-02-03T04:05:06Z",
          updatedAt: "2026-02-03T04:06:07Z"
        }
      ]
    });
  });
});
