import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";

import type { SystemInfo } from "../../shared/systemTypes";
import { SystemInfoPanel } from "./SystemInfoPanel";

describe("SystemInfoPanel Windows settings action", () => {
  it("shows a sanitized error and re-enables the action after an IPC rejection", async () => {
    const onOpenSettings = vi.fn().mockRejectedValue(new Error("ipc://secret-host disconnected"));
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(SystemInfoPanel, {
        info: systemInfo(),
        onRefresh: vi.fn().mockResolvedValue(undefined),
        onOpenSettings
      }));
    });

    const button = renderer.root.findByProps({ "data-testid": "system-settings-button" });
    await act(async () => {
      button.props.onClick();
      await Promise.resolve();
    });

    expect(onOpenSettings).toHaveBeenCalledWith("ms-settings:display");
    expect(renderer.root.findByProps({ "data-testid": "system-settings-error" }).children.join(""))
      .toBe("无法打开 Windows 设置，请稍后重试。");
    expect(textContent(renderer)).not.toContain("secret-host");
    expect(renderer.root.findByProps({ "data-testid": "system-settings-button" }).props.disabled)
      .toBe(false);
  });
});

function systemInfo(): SystemInfo {
  return {
    appVersion: "0.1.2",
    electronVersion: "39.0.0",
    chromeVersion: "142.0.0",
    nodeVersion: "22.0.0",
    platform: "win32",
    arch: "x64",
    backendBaseUrl: "http://127.0.0.1:8000",
    diagnostics: {
      info: {},
      disks: [],
      network: {},
      topProcesses: [],
      suggestions: ["Check display scaling"]
    }
  };
}

function textContent(renderer: ReactTestRenderer): string {
  return renderer.root
    .findAll(() => true)
    .flatMap((node) => node.children)
    .filter((child): child is string => typeof child === "string")
    .join(" ");
}
