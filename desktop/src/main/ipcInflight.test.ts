import { describe, expect, it } from "vitest";

import { abortInflightApiGroup, acquireInflightGroupSignal } from "./ipcInflight";

describe("ipcInflight", () => {
  it("shares a controller only while grouped requests are active", () => {
    const first = acquireInflightGroupSignal("workspace-refresh");
    const second = acquireInflightGroupSignal("workspace-refresh");

    expect(first).toBeDefined();
    expect(second?.signal).toBe(first?.signal);

    first?.release();
    const third = acquireInflightGroupSignal("workspace-refresh");
    expect(third?.signal).toBe(second?.signal);

    second?.release();
    third?.release();
    const afterCompletion = acquireInflightGroupSignal("workspace-refresh");
    expect(afterCompletion?.signal).not.toBe(first?.signal);
    afterCompletion?.release();
  });

  it("aborts every active request in a group and allows a fresh group later", () => {
    const first = acquireInflightGroupSignal("task-snapshot");
    const second = acquireInflightGroupSignal("task-snapshot");

    abortInflightApiGroup("task-snapshot");

    expect(first?.signal.aborted).toBe(true);
    expect(second?.signal.aborted).toBe(true);
    const replacement = acquireInflightGroupSignal("task-snapshot");
    expect(replacement?.signal.aborted).toBe(false);
    expect(replacement?.signal).not.toBe(first?.signal);

    first?.release();
    second?.release();
    replacement?.release();
  });
});
