import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiResponse } from "../../../shared/desktopBridgeTypes";
import { LengrvisApiClient } from "./client";

describe("LengrvisApiClient hardware acceleration smoke", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    (window as unknown as { lengrvis?: unknown }).lengrvis = undefined;
  });

  it("maps image embedding smoke requests to the backend image_path body", async () => {
    const client = new LengrvisApiClient();
    const request = vi.spyOn(client, "request").mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        available: true,
        status: "ready",
        operation: "test_image_embedding",
        dim: 512
      }
    } as ApiResponse<unknown>);

    const response = await client.runHardwareAccelerationSmoke({
      operation: "test_image_embedding",
      imagePath: "C:\\tmp\\image.png",
      modelPath: "C:\\models\\clip.onnx"
    });

    expect(request).toHaveBeenCalledWith({
      endpoint: "/api/settings/onnx/test-image-embedding",
      method: "POST",
      body: {
        image_path: "C:\\tmp\\image.png",
        model_path: "C:\\models\\clip.onnx"
      },
      timeoutMs: 10_000
    });
    expect(response.data).toMatchObject({
      ok: true,
      available: true,
      status: "ready",
      operation: "test_image_embedding",
      dim: 512
    });
  });

  it("keeps Electron OCR smoke operation when the backend omits operation and status fields", async () => {
    const smoke = vi.fn().mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        selected_backend: "openvino",
        runtime: "OpenVINO",
        model: "models/ocr",
        smoke: "synthetic_image"
      }
    });
    (window as unknown as { lengrvis?: { hardwareAcceleration: { smoke: typeof smoke } } }).lengrvis = {
      hardwareAcceleration: { smoke }
    };

    const client = new LengrvisApiClient();
    const response = await client.runHardwareAccelerationSmoke({
      operation: "test_ocr",
      imagePath: "C:\\tmp\\ocr.png"
    });

    expect(smoke).toHaveBeenCalledWith({
      operation: "test_ocr",
      prompt: undefined,
      maxTokens: undefined,
      texts: undefined,
      modelPath: undefined,
      imagePath: "C:\\tmp\\ocr.png"
    });
    expect(response.data).toMatchObject({
      ok: true,
      available: true,
      status: "ready",
      operation: "test_ocr",
      selectedBackend: "openvino",
      runtime: "OpenVINO",
      model: "models/ocr",
      smoke: "synthetic_image"
    });
  });
});
