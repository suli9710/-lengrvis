import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiResponse } from "../../../shared/desktopBridgeTypes";
import { LengrvisApiClient } from "./client";

describe("LengrvisApiClient endpoint group delegates", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    (window as unknown as { lengrvis?: unknown }).lengrvis = undefined;
  });

  it("keeps chat request compatibility", async () => {
    const client = new LengrvisApiClient();
    const request = vi.spyOn(client, "request").mockResolvedValue({
      ok: true,
      status: 200,
      receivedAt: "2026-01-01T00:00:00Z",
      data: {
        task_id: "task_123",
        status: "running",
        message: "Working",
        delegated: true,
        agent: "PlannerAgent"
      }
    } as ApiResponse<unknown>);

    const response = await client.sendChat({ content: "Plan it", mode: "hybrid" });

    expect(request).toHaveBeenCalledWith({
      endpoint: "/api/chat",
      method: "POST",
      body: { message: "Plan it", mode: "hybrid" }
    });
    expect(response.data).toMatchObject({
      taskUpdates: [{ id: "task_123", state: "running", agent: "PlannerAgent" }]
    });
  });

  it("keeps perception suggestion launch request compatibility", async () => {
    const client = new LengrvisApiClient();
    const request = vi.spyOn(client, "request").mockResolvedValue({
      ok: true,
      status: 200,
      receivedAt: "2026-01-01T00:00:00Z",
      data: {
        run_id: "run_suggest",
        engine: "os",
        phase: "queued",
        message: "Check downloads"
      }
    } as ApiResponse<unknown>);

    const response = await client.launchPerceptionSuggestion({
      suggestionId: "downloads/cleanup",
      prompt: "Check downloads",
      mode: "privacy"
    });

    expect(request).toHaveBeenCalledWith({
      endpoint: "/api/perception/suggestions/downloads%2Fcleanup/launch",
      method: "POST",
      body: {
        suggestion_id: "downloads/cleanup",
        prompt: "Check downloads",
        mode: "privacy"
      },
      timeoutMs: 10_000
    });
    expect(response.data).toMatchObject({
      runId: "run_suggest",
      taskUpdates: [{ id: "run_suggest", runId: "run_suggest" }]
    });
  });

  it("normalizes rejected direct perception IPC into a sanitized API response", async () => {
    (window as unknown as { lengrvis?: unknown }).lengrvis = {
      perception: {
        launchSuggestion: vi.fn().mockRejectedValue(new Error("C:\\Users\\secret\\bridge.log"))
      }
    };
    const client = new LengrvisApiClient();

    const response = await client.launchPerceptionSuggestion({
      suggestionId: "downloads/cleanup",
      prompt: "Check downloads",
      mode: "privacy"
    });

    expect(response).toMatchObject({
      ok: false,
      status: 0,
      error: {
        code: "IPC_REQUEST_FAILED",
        message: "Lengrvis 桌面连接暂时不可用，请重启应用后再试。"
      }
    });
    expect(JSON.stringify(response)).not.toContain("Users");
  });

  it("normalizes rejected direct skill IPC without exposing bridge details", async () => {
    (window as unknown as { lengrvis?: unknown }).lengrvis = {
      skills: {
        importPackage: vi.fn().mockRejectedValue(new Error("C:\\Users\\secret\\skill.zip")),
        refresh: vi.fn().mockRejectedValue(new Error("registry token=secret"))
      }
    };
    const client = new LengrvisApiClient();

    const [importResponse, refreshResponse] = await Promise.all([
      client.importSkill("C:\\safe\\skill.zip"),
      client.refreshSkills()
    ]);

    for (const response of [importResponse, refreshResponse]) {
      expect(response).toMatchObject({
        ok: false,
        status: 0,
        error: { code: "IPC_REQUEST_FAILED" }
      });
      expect(JSON.stringify(response)).not.toContain("secret");
    }
  });

  it("keeps run start request compatibility", async () => {
    const client = new LengrvisApiClient();
    const request = vi.spyOn(client, "request").mockResolvedValue({
      ok: true,
      status: 200,
      receivedAt: "2026-01-01T00:00:00Z",
      data: {
        run_id: "run_123",
        engine: "developer",
        phase: "awaiting_approval"
      }
    } as ApiResponse<unknown>);

    const response = await client.startRun({ content: "Build report", mode: "privacy" });

    expect(request).toHaveBeenCalledWith({
      endpoint: "/api/runs",
      method: "POST",
      body: { message: "Build report", mode: "privacy", engine: "auto" }
    });
    expect(response.data).toMatchObject({
      runId: "run_123",
      engine: "developer",
      taskUpdates: [{ id: "run_123", runId: "run_123", state: "blocked" }]
    });
  });

  it("keeps cleanup plan request compatibility", async () => {
    const client = new LengrvisApiClient();
    const request = vi.spyOn(client, "request").mockResolvedValue({
      ok: true,
      status: 200,
      receivedAt: "2026-01-01T00:00:00Z",
      data: {
        id: "cleanup_123",
        title: "Cleanup",
        items: []
      }
    } as ApiResponse<unknown>);

    const response = await client.planCleanup({
      roots: ["C:\\Temp"],
      thresholdMb: 20,
      includeCaches: true,
      itemIds: ["item_1"],
      preferTrash: true
    });

    expect(request).toHaveBeenCalledWith({
      endpoint: "/api/files/cleanup/plan",
      method: "POST",
      body: {
        roots: ["C:\\Temp"],
        threshold_mb: 20,
        include_caches: true,
        item_ids: ["item_1"],
        prefer_trash: true
      },
      timeoutMs: 30_000
    });
    expect(response.data).toMatchObject({
      id: "cleanup_123",
      title: "Cleanup",
      items: []
    });
  });

  it("keeps approval and command request compatibility", async () => {
    const client = new LengrvisApiClient();
    const request = vi.spyOn(client, "request")
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        receivedAt: "2026-01-01T00:00:00Z",
        data: {
          id: "approval_123",
          approval_type: "tool_execution",
          message: "Allow command?",
          diff_preview: { action: "preview" },
          status: "approved",
          created_at: "2026-01-01T00:00:00Z"
        }
      } as ApiResponse<unknown>)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        receivedAt: "2026-01-01T00:00:00Z",
        data: {
          ok: true,
          command: "system.ping",
          title: "Ping",
          next_action: "done"
        }
      } as ApiResponse<unknown>);

    const approval = await client.submitApprovalDecision({ approvalId: "approval_123", decision: "approved" });
    const command = await client.executeCommand("system.ping", { fast: true });

    expect(request).toHaveBeenNthCalledWith(1, {
      endpoint: "/api/approvals/approval_123/approve",
      method: "POST"
    });
    expect(request).toHaveBeenNthCalledWith(2, {
      endpoint: "/api/commands/execute",
      method: "POST",
      body: { name: "system.ping", args: { fast: true } }
    });
    expect(approval.data).toMatchObject({ id: "approval_123", status: "approved" });
    expect(command.data).toMatchObject({ ok: true, command: "system.ping", nextAction: "done" });
  });

  it("keeps skills request compatibility", async () => {
    const client = new LengrvisApiClient();
    const request = vi.spyOn(client, "request")
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        receivedAt: "2026-01-01T00:00:00Z",
        data: {
          skills: [],
          count: 0,
          directories: ["C:\\Skills"],
          install_directory: "C:\\Skills"
        }
      } as ApiResponse<unknown>)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        receivedAt: "2026-01-01T00:00:00Z",
        data: {
          ok: true,
          tool_count: 4,
          skill_count: 2
        }
      } as ApiResponse<unknown>);

    const catalog = await client.listSkills();
    const refresh = await client.refreshSkills();

    expect(request).toHaveBeenNthCalledWith(1, { endpoint: "/api/skills" });
    expect(request).toHaveBeenNthCalledWith(2, { endpoint: "/api/skills/refresh", method: "POST" });
    expect(catalog.data).toMatchObject({ count: 0, installDirectory: "C:\\Skills" });
    expect(refresh.data).toMatchObject({ ok: true, toolCount: 4, skillCount: 2 });
  });

  it("keeps commerce license activation request compatibility", async () => {
    const client = new LengrvisApiClient();
    const request = vi.spyOn(client, "request").mockResolvedValue({
      ok: true,
      status: 200,
      receivedAt: "2026-01-01T00:00:00Z",
      data: {
        state: "active",
        present: true,
        active: true,
        expired: false,
        verifier_configured: true,
        requested_env_plan: "team",
        license_id: "lic_123",
        plan: "pro"
      }
    } as ApiResponse<unknown>);

    const response = await client.activateCommerceLicense("act_123", "0.1.1");

    expect(request).toHaveBeenCalledWith({
      endpoint: "/api/commerce/license/activate",
      method: "POST",
      body: { activation_key: "act_123", app_version: "0.1.1" }
    });
    expect(response.data).toMatchObject({
      state: "active",
      active: true,
      requestedEnvPlan: "pro",
      licenseId: "lic_123",
      plan: "pro"
    });
  });

  it("keeps document ask request compatibility", async () => {
    const client = new LengrvisApiClient();
    const request = vi.spyOn(client, "request").mockResolvedValue({
      ok: true,
      status: 200,
      receivedAt: "2026-01-01T00:00:00Z",
      data: {
        answer: "done",
        source_chunks: [{ id: "chunk_1", snippet: "source text", path: "a.md" }]
      }
    } as ApiResponse<unknown>);

    const response = await client.askDocument({
      path: "C:\\Docs\\sample.pdf",
      question: "Summarize",
      topK: 4
    });

    expect(request).toHaveBeenCalledWith({
      endpoint: "/api/documents/ask",
      method: "POST",
      body: {
        path: "C:\\Docs\\sample.pdf",
        question: "Summarize",
        top_k: 4
      },
      timeoutMs: 30_000
    });
    expect(response.data).toMatchObject({
      answer: "done",
      citations: [{ id: "chunk_1", text: "source text", path: "a.md" }]
    });
  });

  it("keeps local model pull request compatibility", async () => {
    const client = new LengrvisApiClient();
    const request = vi.spyOn(client, "request").mockResolvedValue({
      ok: true,
      status: 200,
      receivedAt: "2026-01-01T00:00:00Z",
      data: { ok: true, message: "pulled" }
    } as ApiResponse<unknown>);

    await client.pullOllama("  llama3.2  ");

    expect(request).toHaveBeenCalledWith({
      endpoint: "/api/settings/ollama/pull",
      method: "POST",
      body: { model: "llama3.2" },
      timeoutMs: 120_000
    });
  });

  it("keeps context usage request compatibility", async () => {
    const client = new LengrvisApiClient();
    const request = vi.spyOn(client, "request").mockResolvedValue({
      ok: true,
      status: 200,
      receivedAt: "2026-01-01T00:00:00Z",
      data: {
        used_tokens: 512,
        effective_context_window: 2048,
        warning: { threshold: 1024 }
      }
    } as ApiResponse<unknown>);

    const response = await client.getContextUsage("task_123");

    expect(request).toHaveBeenCalledWith({
      endpoint: "/api/context/usage",
      query: { task_id: "task_123" },
      timeoutMs: 2500
    });
    expect(response.data).toMatchObject({
      usedTokens: 512,
      effectiveContextWindow: 2048
    });
  });

  it("keeps settings load and save request compatibility", async () => {
    const client = new LengrvisApiClient();
    const request = vi.spyOn(client, "request")
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        receivedAt: "2026-01-01T00:00:00Z",
        data: {
          model: "gpt-test",
          allowed_directories: ["C:\\Work"]
        }
      } as ApiResponse<unknown>)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        receivedAt: "2026-01-01T00:00:00Z",
        data: { required: false }
      } as ApiResponse<unknown>)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        receivedAt: "2026-01-01T00:00:00Z",
        data: {
          model: "gpt-test",
          allowed_directories: ["C:\\Work"]
        }
      } as ApiResponse<unknown>);

    const loaded = await client.getSettings();
    const saved = await client.saveSettings(loaded.data!);

    expect(request).toHaveBeenNthCalledWith(1, { endpoint: "/api/settings" });
    expect(request).toHaveBeenNthCalledWith(2, {
      endpoint: "/api/settings/confirm-sensitive-change",
      method: "POST",
      body: {}
    });
    expect(request).toHaveBeenNthCalledWith(3, {
      endpoint: "/api/settings",
      method: "POST",
      body: {}
    });
    expect(saved.data).toMatchObject({
      model: "gpt-test",
      workspaceRoot: "C:\\Work"
    });
  });

  it("keeps file library request compatibility", async () => {
    const client = new LengrvisApiClient();
    const request = vi.spyOn(client, "request").mockResolvedValue({
      ok: true,
      status: 200,
      receivedAt: "2026-01-01T00:00:00Z",
      data: {
        section: "gallery",
        items: [],
        count: 0,
        total: 0,
        scanned: 3
      }
    } as ApiResponse<unknown>);

    const response = await client.listLocalLibrary("gallery", "invoice", 12);

    expect(request).toHaveBeenCalledWith({
      endpoint: "/api/library",
      query: { section: "gallery", q: "invoice", limit: 12 },
      timeoutMs: 20_000
    });
    expect(response.data).toMatchObject({
      section: "gallery",
      scanned: 3
    });
  });

  it("keeps system info fanout compatibility", async () => {
    const client = new LengrvisApiClient();
    const request = vi.spyOn(client, "request").mockImplementation((apiRequest) => {
      const responses: Record<string, ApiResponse<unknown>> = {
        "/api/system/info": {
          ok: true,
          status: 200,
          receivedAt: "2026-01-01T00:00:00Z",
          data: { system: "Windows", machine: "AMD64" }
        },
        "/api/system/diagnostics": {
          ok: true,
          status: 200,
          receivedAt: "2026-01-01T00:00:00Z",
          data: { info: {}, disks: [], suggestions: [] }
        },
        "/api/system/processes": {
          ok: true,
          status: 200,
          receivedAt: "2026-01-01T00:00:00Z",
          data: { processes: [{ pid: 7, name: "lengrvis.exe" }] }
        },
        "/api/system/startup-items": {
          ok: true,
          status: 200,
          receivedAt: "2026-01-01T00:00:00Z",
          data: { startup_items: [{ name: "Lengrvis" }] }
        },
        "/api/apps": {
          ok: true,
          status: 200,
          receivedAt: "2026-01-01T00:00:00Z",
          data: { apps: [{ id: "app_1", name: "Lengrvis", source: "registry" }] }
        }
      };
      return Promise.resolve(responses[apiRequest.endpoint]);
    }) as unknown as typeof client.request;

    const response = await client.getSystemInfo();

    expect(request).toHaveBeenCalledWith({ endpoint: "/api/system/info" });
    expect(request).toHaveBeenCalledWith({ endpoint: "/api/system/diagnostics" });
    expect(request).toHaveBeenCalledWith({ endpoint: "/api/system/processes", query: { limit: 8 } });
    expect(request).toHaveBeenCalledWith({ endpoint: "/api/system/startup-items" });
    expect(request).toHaveBeenCalledWith({ endpoint: "/api/apps" });
    expect(response.data).toMatchObject({
      platform: "Windows",
      arch: "AMD64",
      installedApps: [{ id: "app_1", name: "Lengrvis" }]
    });
  });
});
