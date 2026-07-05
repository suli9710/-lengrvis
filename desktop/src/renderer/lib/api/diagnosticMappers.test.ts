import { describe, expect, it } from "vitest";

import {
  allBooleanSignalsMatch,
  externalReviewStatusAllowsSharing,
  mapDiagnostic,
  mapDiagnosticExportResult,
  mapLocalMetrics,
  mapProcess,
  mapStartupItem,
  mapSupportPackageRedaction,
  numberRecord,
  plainRecord
} from "./diagnosticMappers";

describe("diagnostic mappers", () => {
  it("maps local metrics defaults and numeric counters", () => {
    expect(mapLocalMetrics({}, 14)).toEqual({
      windowDays: 14,
      generatedAt: "",
      tasks: {
        total: 0,
        terminal: 0,
        succeeded: 0,
        successRate: null,
        byStatus: {}
      },
      runs: {
        total: 0,
        byPhase: {}
      },
      recovery: {
        reflectionsStarted: 0,
        runsWithReflection: 0,
        recoveryTriggerRate: null,
        decidedActions: {},
        askUserShare: null
      },
      llm: {
        calls: 0,
        anomalies: 0,
        anomalyRate: null,
        estimatedCalls: 0,
        byFinishReason: {}
      }
    });

    expect(
      mapLocalMetrics({
        window_days: 7,
        generated_at: "2026-02-03T04:05:06Z",
        tasks: { total: 4, terminal: 3, succeeded: 2, success_rate: 0.5, by_status: { completed: 2 } },
        runs: { total: 5, by_phase: { running: 1 } },
        recovery: {
          reflections_started: 1,
          runs_with_reflection: 2,
          recovery_trigger_rate: 0.4,
          decided_actions: { retry: 1 },
          ask_user_share: 0.25
        },
        llm: { calls: 9, anomalies: 1, anomaly_rate: 0.1, estimated_calls: 10, by_finish_reason: { stop: 8 } }
      })
    ).toMatchObject({
      windowDays: 7,
      generatedAt: "2026-02-03T04:05:06Z",
      tasks: { total: 4, successRate: 0.5, byStatus: { completed: 2 } },
      runs: { total: 5, byPhase: { running: 1 } },
      recovery: { reflectionsStarted: 1, runsWithReflection: 2, decidedActions: { retry: 1 } },
      llm: { calls: 9, anomalies: 1, byFinishReason: { stop: 8 } }
    });
  });

  it("maps process, startup, diagnostics, and export payloads", () => {
    expect(mapProcess({ pid: 42, name: "python", cpu_percent: 2.5, memory_bytes: 2048 })).toEqual({
      pid: 42,
      name: "python",
      username: undefined,
      cpuPercent: 2.5,
      memoryBytes: 2048,
      status: undefined
    });
    expect(mapStartupItem({ name: "Agent", command: "agent.exe" })).toEqual({
      name: "Agent",
      path: undefined,
      command: "agent.exe",
      source: "unknown"
    });

    const diagnostic = mapDiagnostic(
      {
        info: { platform: "win32" },
        disks: [{ device: "C:", mountpoint: "C:\\", usage: { total: 100, free: 25 } }],
        network: { online: true },
        battery: null,
        top_processes: [{ pid: 7, name: "node", cpu_percent: 1, memory_bytes: 4096, status: "running" }],
        suggestions: ["Check logs"],
        product: { name: "Lengrvis", version: "0.1.1" },
        update_channel: {
          configured: true,
          status: "offline",
          next_steps: ["restart", 3],
          release_notes: { available: true, path: "notes.md" }
        },
        local_paths: { data_dir: "C:\\Data", log_dirs: ["C:\\Logs"] },
        audit: { verification: { ok: true }, latest_event: { id: "evt_1" } },
        recent_counts: { ok: 2, bad: "nope" },
        recent_failure_counts: { failed: "3" },
        diagnostic_hints: ["Review support package"],
        diagnostic_scope: "local_only"
      },
      [{ name: "Startup", source: "registry" }]
    );

    expect(diagnostic).toMatchObject({
      info: { platform: "win32" },
      disks: [{ device: "C:", mountpoint: "C:\\", usage: { total: 100, free: 25 } }],
      network: { online: true },
      topProcesses: [{ pid: 7, name: "node", cpuPercent: 1, memoryBytes: 4096, status: "running" }],
      startupItems: [{ name: "Startup", source: "registry" }],
      suggestions: ["Check logs"],
      product: { name: "Lengrvis", version: "0.1.1" },
      updateChannel: {
        configured: true,
        status: "offline",
        nextSteps: ["restart", "3"],
        releaseNotes: { available: true, path: "notes.md" }
      },
      localPaths: { dataDir: "C:\\Data", logDirs: ["C:\\Logs"] },
      audit: { verification: { ok: true }, latestEvent: { id: "evt_1" } },
      recentCounts: { ok: 2 },
      recentFailureCounts: { failed: 3 },
      diagnosticHints: ["Review support package"],
      diagnosticScope: "local_only"
    });

    expect(mapDiagnosticExportResult({ path: "pkg.zip", filename: "pkg.zip", bytes: 12 })).toEqual({
      ok: true,
      path: "pkg.zip",
      filename: "pkg.zip",
      createdAt: "",
      bytes: 12,
      scope: "local_only",
      error: undefined
    });
  });

  it("keeps support package sharing fail-closed until all safety signals agree", () => {
    expect(mapSupportPackageRedaction()).toBeUndefined();
    expect(
      mapSupportPackageRedaction({
        public_safe: true,
        review_before_external_sharing: false,
        external_sharing_allowed: true,
        fail_closed: false,
        current_response: {
          public_safe: true,
          contains_local_paths: false,
          external_review_required: false
        },
        external_review: {
          status: "approved",
          required_before_external_sharing: false,
          public_safe: true,
          external_sharing_allowed: true,
          fail_closed: false,
          checklist: ["reviewed"]
        }
      })
    ).toMatchObject({
      publicSafe: true,
      reviewBeforeExternalSharing: false,
      externalSharingAllowed: true,
      failClosed: false,
      externalSharingSafe: true,
      safetySignalsConsistent: true,
      blockingReasons: [],
      externalReview: { checklistCount: 1 }
    });

    const blocked = mapSupportPackageRedaction({
      public_safe: true,
      review_before_external_sharing: false,
      external_sharing_allowed: true,
      fail_closed: false,
      current_response: {
        public_safe: true,
        contains_local_paths: true,
        external_review_required: false
      },
      external_review: {
        status: "manual_review_required",
        required_before_external_sharing: true,
        public_safe: false,
        external_sharing_allowed: false,
        fail_closed: true
      }
    });

    expect(blocked?.externalSharingSafe).toBe(false);
    expect(blocked?.blockingReasons).toEqual(
      expect.arrayContaining([
        "current_response_contains_local_paths",
        "external_review_public_safe_false",
        "external_review_required",
        "external_review_status_not_approved",
        "safety_signals_inconsistent_or_incomplete"
      ])
    );
    expect(externalReviewStatusAllowsSharing("safe_to_share")).toBe(true);
    expect(externalReviewStatusAllowsSharing("manual_review_required")).toBe(false);
    expect(allBooleanSignalsMatch([true, true])).toBe(true);
    expect(allBooleanSignalsMatch([true, false])).toBe(false);
    expect(plainRecord({ ok: true })).toEqual({ ok: true });
    expect(plainRecord(null)).toBeUndefined();
    expect(numberRecord({ ok: "2", bad: "x", also: 3 })).toEqual({ ok: 2, also: 3 });
  });
});
