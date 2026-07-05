import { describe, expect, it } from "vitest";

import {
  localModelEvidenceValueLabel,
  mapLocalLlmHealth,
  mapLocalModelSetupPlan,
  mapLocalModelSetupStepState
} from "./localModelMappers";

describe("local model mappers", () => {
  it("maps local LLM health from legacy top-level backend fields", () => {
    expect(
      mapLocalLlmHealth({
        available: true,
        kind: "ollama",
        base_url: "http://127.0.0.1:11434",
        models: ["llama3"],
        probe_order: ["bundled", "ollama"],
        readiness: {
          can_install: true,
          recommended_model: "llama3",
          reason: "ready",
          checks: [{ key: "memory", label: "Memory", ok: true, actual: "16GB", required: "8GB" }],
          memory_total_bytes: 16,
          disk_free_bytes: 32,
          cpu_logical_cores: 8,
          gpu_summary: "integrated"
        }
      })
    ).toMatchObject({
      available: true,
      selectedBackend: {
        kind: "ollama",
        baseUrl: "http://127.0.0.1:11434",
        models: ["llama3"],
        model: "llama3"
      },
      probeOrder: ["bundled", "ollama"],
      readiness: {
        canInstall: true,
        recommendedModel: "llama3",
        checks: [{ key: "memory", label: "Memory", ok: true, actual: "16GB", required: "8GB" }],
        memoryTotalBytes: 16,
        diskFreeBytes: 32,
        cpuLogicalCores: 8,
        gpuSummary: "integrated"
      }
    });
  });

  it("maps setup plans with bundle manifest, repair action, verification, and evidence", () => {
    const plan = mapLocalModelSetupPlan({
      ready: false,
      can_install: true,
      model: "llama3",
      installed: false,
      running: true,
      models: ["llama3"],
      has_model: true,
      runtime_source: "bundled",
      bundled_runtime_available: true,
      bundled_runtime_path: "C:\\runtime",
      bundled_models_available: true,
      bundled_models_path: "C:\\models",
      bundled_model_available: true,
      bundled_model_configured: false,
      bundle_manifest: {
        present: true,
        valid: true,
        path: "manifest.json",
        model: "llama3",
        accepted_licenses: true,
        runtime_files: "12" as unknown as number,
        models_files: 3
      },
      steps: [
        { key: "runtime", label: "Runtime", state: "done", detail: "ok" },
        { key: "model", label: "Model", state: "unknown", detail: "missing" }
      ],
      next_action: "download_model",
      repair_action: {
        code: "download",
        label: "Download",
        detail: "Download model"
      },
      verification: {
        ready: false,
        next_action: "download_model",
        paths_redacted: false,
        privacy_fallback: "stay local"
      },
      evidence: [
        { key: "runtime", ok: true, detail: "ok", value: "bundled" },
        { key: "checks", ok: false, detail: "needs work", failed_checks: ["disk", "memory"] }
      ]
    });

    expect(plan).toMatchObject({
      ready: false,
      canInstall: true,
      model: "llama3",
      runtimeSource: "bundled",
      bundledRuntimeAvailable: true,
      bundleManifest: {
        present: true,
        valid: true,
        path: "manifest.json",
        model: "llama3",
        acceptedLicenses: true,
        runtimeFiles: 12,
        modelsFiles: 3
      },
      steps: [
        { key: "runtime", state: "done" },
        { key: "model", state: "pending" }
      ],
      nextAction: "download_model",
      repairAction: {
        code: "download"
      },
      verification: {
        ready: false,
        pathsRedacted: false,
        privacyFallback: "stay local"
      },
      evidence: [
        { key: "runtime", valueLabel: "bundled" },
        { key: "checks", valueLabel: "2 checks need attention" }
      ]
    });
  });

  it("normalizes setup step states and evidence labels", () => {
    expect(mapLocalModelSetupStepState("blocked")).toBe("blocked");
    expect(mapLocalModelSetupStepState("unknown")).toBe("pending");
    expect(localModelEvidenceValueLabel({ key: "configured", ok: false, detail: "", configured: false })).toBe("not configured");
    expect(localModelEvidenceValueLabel({ key: "plain", ok: true, detail: "" })).toBe("ok");
  });
});
