import { describe, expect, it } from "vitest";

import { normalizeRunStreamEvent } from "./runEvents";

describe("normalizeRunStreamEvent", () => {
  it("maps lengrvis write permission denials to approval_needed", () => {
    const event = normalizeRunStreamEvent("run_1", {
      event: "tool.result",
      payload: {
        tool_name: "lengrvis_code",
        output: {
          error_classification: "permission_denial",
          permission_denials: [{ tool_name: "Write", reason: "default permission mode requires user approval" }],
        },
      },
    });

    expect(event.kind).toBe("approval_needed");
    expect(event.content).toContain("Write");
  });

  it("maps tool.proposed Write events to approval_needed", () => {
    const event = normalizeRunStreamEvent("run_2", {
      event: "tool.proposed",
      payload: { tool_name: "Write", engine: "developer" },
    });

    expect(event.kind).toBe("approval_needed");
    expect(event.content).toContain("Write");
  });
});
