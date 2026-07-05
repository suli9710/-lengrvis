import { describe, expect, it } from "vitest";

import {
  isBrowserAction,
  mapBrowserActivityEnvelope,
  mapBrowserActivityEvent,
  mapBrowserPage,
  mapBrowserReplayExport,
  mapBrowserSession
} from "./browserMappers";

describe("browser mappers", () => {
  it("maps page snapshots with link fallbacks", () => {
    expect(
      mapBrowserPage({
        ok: true,
        url: "https://example.test",
        title: "Example",
        text: "Hello",
        links: [{ url: "https://example.test/a" }, { title: "B", url: "https://example.test/b" }],
        truncated: true,
        adapter: "playwright"
      })
    ).toEqual({
      ok: true,
      url: "https://example.test",
      title: "Example",
      text: "Hello",
      links: [
        { title: "https://example.test/a", url: "https://example.test/a" },
        { title: "B", url: "https://example.test/b" }
      ],
      truncated: true,
      adapter: "playwright",
      error: undefined
    });
  });

  it("maps sessions and activity events with safe defaults", () => {
    expect(
      mapBrowserSession({
        id: "session_1",
        url: "https://example.test",
        title: "Example",
        status: "running",
        mode: "agent",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:01:00Z",
        paused: false,
        takeover: true,
        last_observation: { ok: true }
      })
    ).toMatchObject({
      id: "session_1",
      current_url: "https://example.test",
      title: "Example",
      status: "running",
      mode: "agent",
      paused: false,
      takeover: true,
      last_observation: { ok: true }
    });

    expect(
      mapBrowserActivityEvent({
        id: "event_1",
        session_id: "session_1",
        task_id: null,
        step_id: "step_1",
        type: "click",
        action: { kind: "click", selector: "button" },
        risk_level: "low",
        ok: undefined,
        created_at: "2026-01-01T00:02:00Z"
      })
    ).toMatchObject({
      id: "event_1",
      session_id: "session_1",
      task_id: undefined,
      step_id: "step_1",
      type: "click",
      action: { kind: "click", selector: "button" },
      risk_level: "low",
      ok: true,
      created_at: "2026-01-01T00:02:00Z"
    });
  });

  it("maps activity envelopes and replay exports", () => {
    expect(
      mapBrowserActivityEnvelope({
        ok: true,
        event: {
          id: "event_2",
          session_id: "session_2",
          type: "observe",
          ok: true,
          created_at: "2026-01-01T00:03:00Z"
        }
      })
    ).toMatchObject({
      id: "event_2",
      session_id: "session_2",
      type: "observe",
      ok: true
    });

    expect(
      mapBrowserReplayExport({
        ok: true,
        url: "https://example.test/replay",
        path: "replay.json",
        session: { id: "session_2", current_url: "https://example.test" },
        events: [{ id: "event_2", session_id: "session_2", type: "observe", ok: true }]
      })
    ).toMatchObject({
      ok: true,
      url: "https://example.test/replay",
      path: "replay.json",
      session: { id: "session_2", current_url: "https://example.test" },
      events: [{ id: "event_2", session_id: "session_2", type: "observe", ok: true }]
    });
  });

  it("recognizes browser actions by kind", () => {
    expect(isBrowserAction({ kind: "observe" })).toBe(true);
    expect(isBrowserAction({ selector: "button" })).toBe(false);
    expect(isBrowserAction(null)).toBe(false);
  });
});
