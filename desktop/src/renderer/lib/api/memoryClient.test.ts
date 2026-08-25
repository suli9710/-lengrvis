import { afterEach, describe, expect, it, vi } from "vitest";

import {
  forgetMemoryEndpoint,
  listMemoriesEndpoint,
  promoteMemoryEndpoint,
  recallMemoryEndpoint,
  revokeMemoryEndpoint,
  saveMemoryEndpoint,
  type MemoryEndpointRequest
} from "./memoryClient";

describe("memory client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    (window as unknown as { lengrvis?: unknown }).lengrvis = undefined;
  });

  it("maps defaults and field names for the backend adapter", async () => {
    const request = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    const endpointRequest = request as MemoryEndpointRequest;

    await listMemoriesEndpoint(endpointRequest);
    await saveMemoryEndpoint(endpointRequest, "Remember this");
    await recallMemoryEndpoint(endpointRequest, "Remember");
    await promoteMemoryEndpoint(endpointRequest, "memory-one", { resolveConflict: true });
    await revokeMemoryEndpoint(endpointRequest, "memory-one");
    await forgetMemoryEndpoint(endpointRequest, "memory/one");

    expect(request.mock.calls.map(([input]) => input)).toEqual([
      { endpoint: "/api/memories" },
      {
        endpoint: "/api/memories",
        method: "POST",
        body: { content: "Remember this", tags: [], task_id: "", kind: "fact" }
      },
      {
        endpoint: "/api/memories/recall",
        method: "POST",
        body: { query: "Remember", k: 5, tags: [] }
      },
      {
        endpoint: "/api/memories/memory-one/promote",
        method: "POST",
        body: { reviewed_by: "desktop-user", conflict_status: "resolved" }
      },
      {
        endpoint: "/api/memories/memory-one/revoke",
        method: "POST",
        body: { reviewed_by: "desktop-user" }
      },
      { endpoint: "/api/memories/memory/one", method: "DELETE" }
    ]);
  });

  it("uses Electron for mutations while listing remains backend-owned", async () => {
    const response = { ok: true, status: 200 };
    const memories = {
      save: vi.fn().mockResolvedValue(response),
      recall: vi.fn().mockResolvedValue(response),
      promote: vi.fn().mockResolvedValue(response),
      revoke: vi.fn().mockResolvedValue(response),
      forget: vi.fn().mockResolvedValue(response)
    };
    (window as unknown as { lengrvis?: { memories: typeof memories } }).lengrvis = { memories };
    const request = vi.fn().mockResolvedValue(response);
    const endpointRequest = request as MemoryEndpointRequest;

    await listMemoriesEndpoint(endpointRequest);
    await saveMemoryEndpoint(endpointRequest, "Remember this", {
      tags: ["work"],
      taskId: "task-one",
      kind: "decision"
    });
    await recallMemoryEndpoint(endpointRequest, "Remember", { k: 8, tags: ["work"] });
    await promoteMemoryEndpoint(endpointRequest, "memory-one", { reviewedBy: "reviewer", resolveConflict: true });
    await revokeMemoryEndpoint(endpointRequest, "memory-one", { reviewedBy: "reviewer" });
    await forgetMemoryEndpoint(endpointRequest, "memory-one");

    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith({ endpoint: "/api/memories" });
    expect(memories.save).toHaveBeenCalledWith({
      content: "Remember this",
      tags: ["work"],
      taskId: "task-one",
      kind: "decision"
    });
    expect(memories.recall).toHaveBeenCalledWith({ query: "Remember", k: 8, tags: ["work"] });
    expect(memories.promote).toHaveBeenCalledWith({
      memoryId: "memory-one",
      reviewedBy: "reviewer",
      resolveConflict: true
    });
    expect(memories.revoke).toHaveBeenCalledWith({ memoryId: "memory-one", reviewedBy: "reviewer" });
    expect(memories.forget).toHaveBeenCalledWith("memory-one");
  });
});
