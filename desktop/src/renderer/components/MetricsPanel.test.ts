import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MetricsPanel } from "./MetricsPanel";
import { mapLocalMetrics } from "../lib/api/diagnosticMappers";
import type { LengrvisApiClient } from "../lib/apiClient";

const renderers: ReactTestRenderer[] = [];
afterEach(async () => {
  for (const renderer of renderers.splice(0)) await act(async () => renderer.unmount());
  vi.restoreAllMocks();
});
async function renderPanel(getLocalMetrics: ReturnType<typeof vi.fn>) {
  let renderer!: ReactTestRenderer;
  await act(async () => {
    renderer = create(createElement(MetricsPanel, { api: { getLocalMetrics } as unknown as LengrvisApiClient }));
  });
  renderers.push(renderer);
  return renderer;
}
function card(renderer: ReactTestRenderer, label: string) {
  const node = renderer.root.findAllByProps({ className: "metric-card" }).find(
    item => item.findByProps({ className: "metric-card__label" }).children.includes(label)
  );
  if (!node) throw new Error(`Missing metric ${label}`);
  return {
    value: node.findByProps({ className: "metric-card__value" }).children.join(""),
    hint: node.findByProps({ className: "metric-card__hint muted" }).children.join("")
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

describe("MetricsPanel", () => {
  it("shows goal completion and rollback recovery as distinct outcomes", async () => {
    const data = mapLocalMetrics({ tasks: {
      total: 4, terminal: 4, succeeded: 1, success_rate: 0.25,
      rolled_back: 2, rollback_attempts: 3, rollback_recovery_rate: 2 / 3
    } });
    const renderer = await renderPanel(vi.fn().mockResolvedValue({ ok: true, data }));
    expect(card(renderer, "任务目标完成率")).toEqual({ value: "25.0%", hint: "1/4 个已结束任务完成目标；回滚不计为目标完成" });
    expect(card(renderer, "回滚恢复率")).toEqual({ value: "66.7%", hint: "2/3 次回滚尝试成功恢复；不代表原任务目标完成" });
  });

  it("labels empty samples as no data rather than 0% or 100%", async () => {
    const renderer = await renderPanel(vi.fn().mockResolvedValue({ ok: true, data: mapLocalMetrics({}) }));
    expect(card(renderer, "任务目标完成率").value).toBe("无数据");
    expect(card(renderer, "回滚恢复率").value).toBe("无数据");
  });

  it("does not overwrite a newer statistics window with a late old response", async () => {
    const old = deferred<unknown>();
    const getLocalMetrics = vi.fn().mockReturnValueOnce(old.promise).mockResolvedValueOnce({ ok: true,
      data: mapLocalMetrics({ window_days: 14, tasks: { terminal: 2, succeeded: 1, success_rate: 0.5 } }) });
    const renderer = await renderPanel(getLocalMetrics);
    await act(async () => renderer.root.findByProps({ "aria-label": "统计窗口" }).props.onChange({ currentTarget: { value: "14" } }));
    expect(card(renderer, "任务目标完成率").value).toBe("50.0%");
    await act(async () => old.resolve({ ok: true, data: mapLocalMetrics({ window_days: 7, tasks: { terminal: 1, succeeded: 1, success_rate: 1 } }) }));
    expect(card(renderer, "任务目标完成率").value).toBe("50.0%");
    expect(getLocalMetrics.mock.calls.map(call => call[0])).toEqual([7, 14]);
  });

  it("recovers from a rejected fetch without leaving refresh disabled", async () => {
    const getLocalMetrics = vi.fn().mockRejectedValueOnce(new Error("private transport detail"))
      .mockResolvedValueOnce({ ok: true, data: mapLocalMetrics({}) });
    const renderer = await renderPanel(getLocalMetrics);
    expect(JSON.stringify(renderer.toJSON())).toContain("无法读取本机指标，请重试。");
    expect(JSON.stringify(renderer.toJSON())).not.toContain("private transport detail");
    expect(renderer.root.findByProps({ "aria-label": "刷新指标" }).props.disabled).toBe(false);
    await act(async () => renderer.root.findByProps({ "aria-label": "刷新指标" }).props.onClick());
    expect(card(renderer, "任务目标完成率").value).toBe("无数据");
  });
});
