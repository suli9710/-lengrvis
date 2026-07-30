import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiResponse } from "../../shared/desktopBridgeTypes";
import type { LengrvisApiClient } from "../lib/apiClient";
import { PcmRecorder, VoiceInputButton } from "./VoiceInputButton";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("VoiceInputButton", () => {
  it("turns a rejected health probe into a disabled, recoverable state", async () => {
    const api = voiceApi({
      getVoiceHealth: vi.fn().mockRejectedValue(new Error("voice IPC unavailable"))
    });
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(VoiceInputButton, { api, onTranscript: vi.fn() }));
    });

    const button = renderer.root.findByProps({ "aria-label": "语音输入暂不可用" });
    expect(button.props.disabled).toBe(true);
    expect(button.props.title).toBe("无法读取语音识别状态");
  });

  it("reports a rejected transcription without leaving the button busy", async () => {
    const recorder = {
      sampleRate: 16_000,
      stop: vi.fn().mockResolvedValue(new Uint8Array([1, 0]).buffer),
      dispose: vi.fn()
    } as unknown as PcmRecorder;
    vi.spyOn(PcmRecorder, "start").mockResolvedValue(recorder);
    const onError = vi.fn();
    const api = voiceApi({
      getVoiceHealth: vi.fn().mockResolvedValue(success({ available: true, provider: "local", detail: "" })),
      transcribeVoice: vi.fn().mockRejectedValue(new Error("transcribe IPC unavailable"))
    });
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(VoiceInputButton, { api, onTranscript: vi.fn(), onError }));
    });
    await act(async () => {
      renderer.root.findByProps({ "aria-label": "开始语音输入" }).props.onClick();
    });
    expect(renderer.root.findByProps({ "aria-label": "停止录音并识别" }).props["aria-pressed"]).toBe(true);

    await act(async () => {
      renderer.root.findByProps({ "aria-label": "停止录音并识别" }).props.onClick();
      await Promise.resolve();
    });

    expect(onError).toHaveBeenCalledWith("语音识别连接失败，请稍后重试。");
    expect(renderer.root.findByProps({ "aria-label": "开始语音输入" }).props.disabled).toBe(false);
  });
});

function voiceApi(overrides: Partial<Record<"getVoiceHealth" | "transcribeVoice", unknown>>) {
  return {
    getVoiceHealth: vi.fn().mockResolvedValue(success({ available: false, provider: "", detail: "" })),
    transcribeVoice: vi.fn().mockResolvedValue(success({ transcript: "", confidence: null, language: "zh", provider: "" })),
    ...overrides
  } as unknown as LengrvisApiClient;
}

function success<T>(data: T): ApiResponse<T> {
  return { ok: true, status: 200, data, receivedAt: "2026-07-12T00:00:00.000Z" };
}
