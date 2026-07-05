import { describe, expect, it } from "vitest";

import {
  mapHardwareAccelerationSmoke,
  mapHardwareAccelerationStatus,
  normalizeExecutionProvider,
  normalizeHardwareRuntime
} from "./hardwareAccelerationMappers";

describe("hardware acceleration mappers", () => {
  it("maps status payloads with WinML provider flags and error fallbacks", () => {
    const status = mapHardwareAccelerationStatus({
      available: true,
      kind: "onnx",
      model_path: "models/local.onnx",
      execution_provider: "DmlExecutionProvider",
      available_providers: ["DmlExecutionProvider", "CPUExecutionProvider"],
      generation_runtime: "onnxruntime-genai",
      runtime_package: "onnxruntime-directml",
      configured_provider: "directml",
      selected_provider: "DmlExecutionProvider",
      runtime_packages: {
        onnxruntime: { available: true, module: "onnxruntime", version: "1.20.0" }
      },
      winml: {
        available: true,
        provider_available: true,
        packages: ["winml"],
        errors: { python: "missing" }
      },
      error: "top-level failure",
      llm: {
        runtime: "winml",
        available: true,
        model_path: "models/llm.onnx",
        configured_provider: "winml",
        selected_provider: "WindowsMLExecutionProvider",
        errors: ["fallback"]
      },
      text_embedding: {
        available: false,
        component: "text_embedding",
        model_path: "models/embeddings.onnx",
        execution_provider: "CPUExecutionProvider",
        available_providers: ["CPUExecutionProvider"],
        error: "embedding backend unavailable"
      },
      image_embedding: {
        available: true,
        component: "image_embedding",
        model_path: "models/clip.onnx",
        selected_provider: "OpenVINOExecutionProvider"
      },
      ocr: {
        available: true,
        component: "ocr",
        selected_backend: "openvino",
        runtime: "OpenVINO",
        model: "models/ocr"
      }
    });

    expect(status).toMatchObject({
      available: true,
      kind: "onnx",
      modelPath: "models/local.onnx",
      executionProvider: "DmlExecutionProvider",
      availableProviders: ["DmlExecutionProvider", "CPUExecutionProvider"],
      generationRuntime: "onnxruntime-genai",
      runtimePackage: "onnxruntime-directml",
      configuredProvider: "directml",
      selectedProvider: "DmlExecutionProvider",
      runtimePackages: {
        onnxruntime: { available: true, module: "onnxruntime", version: "1.20.0" }
      },
      winml: {
        available: true,
        provider: "WindowsMLExecutionProvider",
        providerAvailable: true,
        packages: ["winml"],
        errors: { python: "missing" }
      },
      errors: ["top-level failure"],
      llm: {
        runtime: "winml",
        available: true,
        modelPath: "models/llm.onnx",
        configuredProvider: "winml",
        selectedProvider: "WindowsMLExecutionProvider",
        errors: ["fallback"]
      },
      textEmbedding: {
        available: false,
        component: "text_embedding",
        modelPath: "models/embeddings.onnx",
        executionProvider: "CPUExecutionProvider",
        availableProviders: ["CPUExecutionProvider"],
        errors: ["embedding backend unavailable"],
        error: "embedding backend unavailable"
      },
      imageEmbedding: {
        available: true,
        component: "image_embedding",
        modelPath: "models/clip.onnx",
        selectedProvider: "OpenVINOExecutionProvider"
      },
      ocr: {
        available: true,
        component: "ocr",
        selectedBackend: "openvino",
        runtime: "OpenVINO",
        model: "models/ocr"
      }
    });
  });

  it("maps smoke payloads with safe defaults and provider option stringification", () => {
    const smoke = mapHardwareAccelerationSmoke({
      ok: 1 as unknown as boolean,
      available: "" as unknown as boolean,
      status: "missing" as never,
      operation: "legacy_probe" as never,
      errors: ["not ready"],
      count: "3" as unknown as number,
      dim: "768" as unknown as number,
      backend: {
        available_providers: ["CPUExecutionProvider"],
        provider_options: {
          arena: "enabled",
          device_id: 0 as unknown as string
        }
      },
      text_embedding: {
        available: false,
        error: "not configured"
      }
    });

    expect(smoke).toMatchObject({
      ok: true,
      available: false,
      status: "unavailable",
      operation: "warmup",
      errors: ["not ready"],
      count: 3,
      dim: 768,
      backend: {
        kind: "",
        model_path: "",
        execution_provider: "",
        available_providers: ["CPUExecutionProvider"],
        generation_runtime: "",
        provider_options: {
          arena: "enabled",
          device_id: "0"
        }
      },
      textEmbedding: {
        available: false,
        error: "not configured"
      }
    });
  });

  it("maps OCR smoke payloads that omit standard ONNX smoke fields", () => {
    expect(
      mapHardwareAccelerationSmoke(
        {
          ok: true,
          selected_backend: "openvino",
          runtime: "OpenVINO",
          model: "models/ocr",
          smoke: "synthetic_image",
          error: ""
        },
        "test_ocr"
      )
    ).toMatchObject({
      ok: true,
      available: true,
      status: "ready",
      operation: "test_ocr",
      selectedBackend: "openvino",
      runtime: "OpenVINO",
      model: "models/ocr",
      smoke: "synthetic_image",
      errors: []
    });
  });

  it("normalizes provider and runtime aliases", () => {
    expect(normalizeExecutionProvider("auto")).toBe("");
    expect(normalizeExecutionProvider("winml")).toBe("WinML");
    expect(normalizeExecutionProvider("windows_ml")).toBe("WinML");
    expect(normalizeExecutionProvider("dml")).toBe("DirectML");
    expect(normalizeExecutionProvider("openvino")).toBe("OpenVINO");
    expect(normalizeExecutionProvider("cpu")).toBe("CPU");

    expect(normalizeHardwareRuntime("auto")).toBe("");
    expect(normalizeHardwareRuntime("windowsml")).toBe("WinML");
    expect(normalizeHardwareRuntime("directml")).toBe("DirectML");
    expect(normalizeHardwareRuntime("openvino")).toBe("OpenVINO");
    expect(normalizeHardwareRuntime("custom")).toBe("custom");
  });
});
