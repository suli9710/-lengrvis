import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { BrowserHostSnapshot, BrowserSession } from "../../shared/browserTypes";
import type { LengrvisApiClient } from "../lib/apiClient";
import { BrowserActivityPanel } from "./BrowserActivityPanel";

const activeSession: BrowserSession = {
  id: "session_1",
  current_url: "https://example.test/",
  title: "Example",
  status: "idle",
  mode: "watch",
  created_at: "2026-07-10T00:00:00.000Z",
  updated_at: "2026-07-10T00:00:00.000Z",
  paused: false,
  takeover: false,
  last_observation: null
};

const hostSnapshot: BrowserHostSnapshot = {
  sessions: [activeSession],
  events: [],
  activeSessionId: activeSession.id,
  visible: true,
  hostAvailable: true
};

function renderedPanel(): string {
  return renderToStaticMarkup(
    createElement(BrowserActivityPanel, {
      api: {} as LengrvisApiClient,
      sessions: [],
      events: [],
      hostSnapshot,
      activeSessionId: activeSession.id,
      error: null,
      onSessionsChange: () => undefined,
      onEventsChange: () => undefined,
      onHostSnapshotChange: () => undefined,
      onActiveSessionChange: () => undefined,
      onErrorChange: () => undefined
    })
  );
}

describe("BrowserActivityPanel", () => {
  it("keeps takeover disabled until an approved takeover path exists", () => {
    const markup = renderedPanel();
    const buttonEnd = markup.indexOf(">接管</button>");
    const buttonStart = markup.lastIndexOf("<button", buttonEnd);
    const takeoverButton = buttonStart >= 0 && buttonEnd >= 0 ? markup.slice(buttonStart, buttonEnd + ">接管</button>".length) : "";

    expect(takeoverButton).toContain("disabled");
    expect(markup).toContain("接管功能需要已兑现的后端审批；当前版本尚未启用。");
  });
});
