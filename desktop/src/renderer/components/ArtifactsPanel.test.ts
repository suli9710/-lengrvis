import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TaskArtifactsSummary, TaskEvent } from "../../shared/executionTypes";
import type { LengrvisApiClient } from "../lib/apiClient";
import { ArtifactsPanel } from "./ArtifactsPanel";

const renderers: ReactTestRenderer[] = [];
afterEach(async () => {
  for (const renderer of renderers.splice(0)) await act(async () => renderer.unmount());
  vi.restoreAllMocks();
});

const tasks: TaskEvent[] = ["first", "second"].map(id => ({
  id, title: id, description: "", agent: "FileAgent", state: "completed",
  createdAt: "2026-11-01T00:00:00Z", updatedAt: "2026-11-01T00:00:00Z"
}));

function summary(taskId: string): TaskArtifactsSummary {
  return {
    taskId,
    counts: { total: 1, existing: 1, missing: 0, generated: 1, changed: 0 },
    artifacts: [{ path: `C:\\Reports\\${taskId}.txt`, kind: "output", toolName: "", stepId: "",
      createdAt: "", exists: true, isDir: false, sizeBytes: 40 }]
  };
}

async function renderPanel(listTaskArtifacts: ReturnType<typeof vi.fn>) {
  let renderer!: ReactTestRenderer;
  await act(async () => {
    renderer = create(createElement(ArtifactsPanel, {
      tasks, api: { listTaskArtifacts } as unknown as LengrvisApiClient
    }));
  });
  renderers.push(renderer);
  return renderer;
}

describe("ArtifactsPanel", () => {
  it("ignores an old task response and does not display its files under the newly selected task", async () => {
    let finishFirst!: (value: unknown) => void;
    const listTaskArtifacts = vi.fn()
      .mockReturnValueOnce(new Promise(resolve => { finishFirst = resolve; }))
      .mockResolvedValueOnce({ ok: true, data: summary("second") });
    const renderer = await renderPanel(listTaskArtifacts);
    expect(JSON.stringify(renderer.toJSON())).toContain("正在读取任务成果物");
    await act(async () => renderer.root.findByProps({ "aria-label": "选择任务" }).props.onChange({ currentTarget: { value: "second" } }));
    await act(async () => finishFirst({ ok: true, data: summary("first") }));
    expect(JSON.stringify(renderer.toJSON())).toContain("second.txt");
    expect(JSON.stringify(renderer.toJSON())).not.toContain("first.txt");
  });

  it("clears old files while loading a new task and recovers from a rejected fetch", async () => {
    let rejectSecond!: (error: Error) => void;
    const listTaskArtifacts = vi.fn()
      .mockResolvedValueOnce({ ok: true, data: summary("first") })
      .mockReturnValueOnce(new Promise((_resolve, reject) => { rejectSecond = reject; }))
      .mockResolvedValueOnce({ ok: true, data: summary("second") });
    const renderer = await renderPanel(listTaskArtifacts);
    expect(JSON.stringify(renderer.toJSON())).toContain("first.txt");
    await act(async () => renderer.root.findByProps({ "aria-label": "选择任务" }).props.onChange({ currentTarget: { value: "second" } }));
    expect(JSON.stringify(renderer.toJSON())).not.toContain("first.txt");
    await act(async () => rejectSecond(new Error("private transport detail")));
    expect(JSON.stringify(renderer.toJSON())).toContain("无法读取任务成果物，请重试。");
    expect(JSON.stringify(renderer.toJSON())).not.toContain("private transport detail");
    const refresh = renderer.root.findByProps({ "aria-label": "刷新成果物" });
    expect(refresh.props.disabled).toBe(false);
    await act(async () => refresh.props.onClick());
    expect(JSON.stringify(renderer.toJSON())).toContain("second.txt");
  });
});
