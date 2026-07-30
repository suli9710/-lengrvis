import { describe, expect, it } from "vitest";
import {
  dedupeFrames,
  mapBoundaryEvents,
  mapRiskSeverity,
  mapTaskState,
  normalizeTimestamp,
  runEngineAgentName
} from "../mappers";

describe("runEngineAgentName", () => {
  it("labels developer engine as read-only when writes are disabled", () => {
    expect(runEngineAgentName("developer", { writes_enabled: false })).toBe("开发引擎（只读）");
    expect(runEngineAgentName("developer", { writes_enabled: true })).toBe("开发执行引擎");
    expect(runEngineAgentName("os")).toBe("电脑执行引擎");
  });

  it("prefers supervisor worker hint over generic engine label", () => {
    expect(runEngineAgentName("os", { supervisor_agent_hint: "BrowserAgent" })).toBe("浏览器 Agent");
    expect(runEngineAgentName("os", { supervisor_agent_hint: "SearchAgent" })).toBe("搜索 Agent");
  });
});

describe("mapTaskState", () => {
  it("maps backend statuses onto UI task states", () => {
    expect(mapTaskState("completed")).toBe("completed");
    expect(mapTaskState("rolled_back")).toBe("rolled_back");
    expect(mapTaskState("repair_required")).toBe("repair_required");
    expect(mapTaskState("failed")).toBe("failed");
    expect(mapTaskState("denied")).toBe("denied");
    expect(mapTaskState("cancelled")).toBe("cancelled");
    expect(mapTaskState("paused")).toBe("paused");
    expect(mapTaskState("waiting_user_approval")).toBe("blocked");
    expect(mapTaskState("awaiting_approval")).toBe("blocked");
    expect(mapTaskState("running")).toBe("running");
    expect(mapTaskState("anything-else")).toBe("running");
  });
});

describe("mapRiskSeverity", () => {
  it("maps risk levels onto severities", () => {
    expect(mapRiskSeverity("R4_DESTRUCTIVE")).toBe("critical");
    expect(mapRiskSeverity("R3_SYSTEM")).toBe("high");
    expect(mapRiskSeverity("R2_WRITE")).toBe("medium");
    expect(mapRiskSeverity("R1_NETWORK")).toBe("low");
    expect(mapRiskSeverity("R0_READ_ONLY")).toBe("low");
  });
});

describe("normalizeTimestamp", () => {
  it("normalizes parseable timestamps to ISO format", () => {
    expect(normalizeTimestamp("2026-06-12T01:02:03Z", "FB")).toBe("2026-06-12T01:02:03.000Z");
  });

  it("returns the fallback for unparseable or empty values", () => {
    expect(normalizeTimestamp("not-a-date", "FB")).toBe("FB");
    expect(normalizeTimestamp("", "FB")).toBe("FB");
    expect(normalizeTimestamp(42, "FB")).toBe("FB");
    expect(normalizeTimestamp(undefined, "FB")).toBe("FB");
  });
});

describe("mapBoundaryEvents", () => {
  it("maps well-formed events and fills defaults", () => {
    const events = mapBoundaryEvents([
      { id: "b1", kind: "scope", title: "t", detail: "d", severity: "warn", step_id: "s1", created_at: "2026-01-01T00:00:00Z" },
      {}
    ]);
    expect(events).toHaveLength(2);
    expect(events[0]).toMatchObject({ id: "b1", kind: "scope", severity: "warn", stepId: "s1" });
    expect(events[1].kind).toBe("boundary");
    expect(events[1].severity).toBe("info");
    expect(events[1].id).toBeTruthy();
  });

  it("returns an empty list for non-array input", () => {
    expect(mapBoundaryEvents(undefined)).toEqual([]);
    expect(mapBoundaryEvents("nope")).toEqual([]);
    expect(mapBoundaryEvents({})).toEqual([]);
  });
});

describe("dedupeFrames", () => {
  it("drops frames with duplicate phase/capturedAt/url triples", () => {
    const frames = [
      { phase: "before", capturedAt: "t1", url: "u1" },
      { phase: "before", capturedAt: "t1", url: "u1" },
      { phase: "after", capturedAt: "t1", url: "u1" },
      { phase: "before", capturedAt: "t2", url: "u1" }
    ];
    expect(dedupeFrames(frames)).toHaveLength(3);
  });
});
