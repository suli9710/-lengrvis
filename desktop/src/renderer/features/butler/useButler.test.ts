import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";
import type { ButlerMatter, ButlerOverview } from "../../../shared/butlerTypes";
import type { LengrvisApiClient } from "../../lib/apiClient";
import { useButler, type ButlerController } from "./useButler";

const matter: ButlerMatter = {
  id: "matter_a", version: 7, goal: "整理报告", source: "user", status: "waiting_input", mode: "efficiency",
  priority: 50, due_at: "", progress: "需要格式", interaction: { id: "interaction_a", kind: "information", message: "什么格式？", fields: ["format"], candidates: [], approval_id: "" },
  waiting: null, task_ids: [], run_ids: [], active_task_id: "", active_run_id: "", completion_evidence: [], created_at: "now", updated_at: "now"
};
const overview: ButlerOverview = { enabled: true, paused: false, matters: [matter], scopes: [], defaults: { event_merge_seconds: 30, background_concurrency: 2, desktop_concurrency: 1 } };

describe("butler conversation continuation", () => {
  it("opens an artifact through its selected matter version and refreshes status", async () => {
    const api = { supportsButler: () => true,
      getButlerOverview: vi.fn().mockResolvedValue({ ok: true, data: overview }),
      openButlerArtifact: vi.fn().mockResolvedValue({ ok: true, data: { opened: true } })
    } as unknown as LengrvisApiClient;
    let state!: ButlerController;
    function Harness() { state = useButler(api, "efficiency", true); return null; }
    let view!: ReactTestRenderer;
    await act(async () => { view = create(createElement(Harness)); });
    await act(async () => { await state.openArtifact(matter, "artifact_a"); });
    expect(api.openButlerArtifact).toHaveBeenCalledWith("matter_a", "artifact_a", 7);
    expect(api.getButlerOverview).toHaveBeenCalledTimes(2);
    act(() => view.unmount());
  });
  it("answers the selected versioned interaction without creating a new matter", async () => {
    const api = { supportsButler: () => true,
      getButlerOverview: vi.fn().mockResolvedValue({ ok: true, data: overview }),
      respondButlerMatter: vi.fn().mockResolvedValue({ ok: true, data: { matter, reply: "继续" } }),
      sendButlerMessage: vi.fn()
    } as unknown as LengrvisApiClient;
    let state!: ButlerController;
    function Harness() { state = useButler(api, "efficiency", true); return null; }
    let view!: ReactTestRenderer;
    await act(async () => { view = create(createElement(Harness)); });
    act(() => state.selectMatter(matter.id));
    await act(async () => { await state.submit("表格"); });
    expect(api.respondButlerMatter).toHaveBeenCalledWith("matter_a", { version: 7, interaction_id: "interaction_a", message: "表格" });
    expect(api.sendButlerMessage).not.toHaveBeenCalled();
    act(() => view.unmount());
  });
});
