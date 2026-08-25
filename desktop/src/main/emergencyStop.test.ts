import { describe, expect, it, vi } from "vitest";

import { emergencyStopAgentWork, GLOBAL_EMERGENCY_STOP_SHORTCUT } from "./emergencyStop";

describe("emergencyStopAgentWork", () => {
  it("stops every local browser session and cancels backend work", async () => {
    const stop = vi.fn(async (sessionId: string) => ({ ok: sessionId !== "broken" }));
    const backend = { emergencyStop: vi.fn(async () => ({ ok: true, cancelled_task_ids: ["task-1"] })) };

    const result = await emergencyStopAgentWork(
      { getSnapshot: () => ({ sessions: [{ id: "task-1" }, { id: "broken" }] }), stop },
      backend
    );

    expect(stop).toHaveBeenCalledTimes(2);
    expect(backend.emergencyStop).toHaveBeenCalledOnce();
    expect(result).toMatchObject({ ok: false, browser_sessions_stopped: 1, browser_session_failures: 1 });
  });

  it("still attempts the backend when a local stop rejects", async () => {
    const backend = { emergencyStop: vi.fn(async () => ({ ok: true })) };
    const result = await emergencyStopAgentWork(
      {
        getSnapshot: () => ({ sessions: [{ id: "session-1" }] }),
        stop: vi.fn(async () => { throw new Error("closed"); })
      },
      backend
    );

    expect(backend.emergencyStop).toHaveBeenCalledOnce();
    expect(result.ok).toBe(false);
  });

  it("uses a dedicated global shortcut", () => {
    expect(GLOBAL_EMERGENCY_STOP_SHORTCUT).toContain("Shift+Escape");
  });
});
