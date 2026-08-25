import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";

import type { LengrvisApiClient } from "../../lib/apiClient";
import { defaultSettings } from "../../store/defaults";
import { HardwareAccelerationCard } from "./HardwareAccelerationCard";

describe("HardwareAccelerationCard async recovery", () => {
  it("shows an IPC rejection and re-enables smoke actions", async () => {
    const onSmokeStatusChange = vi.fn();
    const api = {
      runHardwareAccelerationSmoke: vi.fn().mockRejectedValue(new Error("硬件冒烟 IPC 已断开"))
    } as unknown as LengrvisApiClient;
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(HardwareAccelerationCard, {
        api,
        settings: defaultSettings,
        status: null,
        loading: false,
        error: "",
        smokeStatus: "",
        smoke: null,
        runtime: "auto",
        onRuntimeChange: vi.fn(),
        onSmokeStatusChange,
        onSmokeChange: vi.fn()
      }));
    });

    await act(async () => {
      renderer.root.findAllByType("button")[0].props.onClick();
      await Promise.resolve();
    });

    expect(textContent(renderer)).toContain("硬件冒烟 IPC 已断开");
    expect(renderer.root.findAllByType("button").every((button) => button.props.disabled === false)).toBe(true);
    expect(onSmokeStatusChange).toHaveBeenLastCalledWith("硬件冒烟 IPC 已断开");
  });
});

function textContent(renderer: ReactTestRenderer): string {
  return renderer.root
    .findAll(() => true)
    .flatMap((node) => node.children)
    .filter((child): child is string => typeof child === "string")
    .join(" ");
}
