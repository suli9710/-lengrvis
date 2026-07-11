import { describe, expect, it } from "vitest";

import {
  mapApproval,
  mapCommandExecutionResult,
  mapCommandInfo,
  mapRunCreateResponse,
  mapRunTaskEvent,
  mapTaskEvent
} from "../mappers";
import { mergeTaskSnapshots } from "../../../appViewModel";

describe("execution mapper contracts", () => {
  it("maps run create responses from nested run state and falls back to backend capabilities", () => {
    const response = mapRunCreateResponse(
      {
        run: {
          run_id: "run_123",
          engine: "developer",
          phase: "awaiting_approval",
          message: "Review generated edits",
          mode: "efficiency",
          requested_engine: "auto",
          created_at: "2026-06-20T08:00:00Z",
          updated_at: "2026-06-20T08:01:00Z",
          engine_capabilities: {
            writes_enabled: false
          }
        }
      },
      "Fallback title"
    );

    expect(response.runId).toBe("run_123");
    const taskUpdates = response.taskUpdates ?? [];
    expect(taskUpdates).toHaveLength(1);
    expect(taskUpdates[0]).toMatchObject({
      id: "run_123",
      runId: "run_123",
      title: "Review generated edits",
      state: "blocked",
      agent: "开发引擎（只读）",
      createdAt: "2026-06-20T08:00:00Z",
      updatedAt: "2026-06-20T08:01:00Z"
    });
  });

  it("maps run task events with cleanup payload and completion-evidence fallback fields", () => {
    const event = mapRunTaskEvent({
      run_id: "run_cleanup",
      engine: "developer",
      phase: "completed",
      message: "Clean temporary files",
      mode: "efficiency",
      requested_engine: "developer",
      created_at: "2026-06-20T09:00:00Z",
      updated_at: "2026-06-20T09:02:00Z",
      engine_capabilities: {
        writes_enabled: true
      },
      diff_preview: {
        cleanup_plan: {
          id: "cleanup_1",
          title: "Temp cleanup",
          risk_warnings: ["Permanent delete requires review"],
          items: [
            {
              id: "item_1",
              path: "C:\\Temp\\old.log",
              action: "delete",
              disposition: "permanent_delete",
              size_bytes: 42
            }
          ]
        }
      },
      task_id: "task_cleanup",
      completion_evidence: {
        level: "completed_result",
        result_verified: true,
        result_artifacts: [{ kind: "tool_result", label: "Successful tool result", redacted: true }],
        missing: [],
        signoff: false
      },
      result_quality: {
        state: "verified_result",
        label: "Verified result",
        summary: "A result is recorded and verified.",
        result_verified: true,
        can_treat_as_done: true,
        needs_review: false,
        missing_checks: [],
        next_step: "Review the verified result.",
        signoff: false,
        redacted: true,
        privacy_note: "Private details are hidden."
      },
      result_verified: true,
      completed_result: {
        summary: "Removed the selected temporary file"
      }
    });

    expect(event).toMatchObject({
      id: "run_cleanup",
      runId: "run_cleanup",
      sourceTaskId: "task_cleanup",
      title: "Clean temporary files",
      state: "completed",
      agent: "开发执行引擎",
      createdAt: "2026-06-20T09:00:00Z",
      updatedAt: "2026-06-20T09:02:00Z"
    });
    expect(event.cleanupPlan).toMatchObject({
      id: "cleanup_1",
      title: "Temp cleanup",
      riskWarnings: ["Permanent delete requires review"],
      items: [
        {
          id: "item_1",
          path: "C:\\Temp\\old.log",
          disposition: "permanent_delete",
          sizeBytes: 42
        }
      ]
    });
    expect(event.completionEvidence).toMatchObject({
      resultVerified: true,
      missing: []
    });
    expect(event.resultQuality).toMatchObject({
      state: "verified_result",
      resultVerified: true,
      canTreatAsDone: true,
      missingChecks: []
    });
  });

  it("maps task result quality and merges run/task snapshots by source task id", () => {
    const task = mapTaskEvent({
      id: "task_shared",
      user_goal: "检查电脑",
      status: "completed",
      mode: "efficiency",
      final_summary: "系统检查完成",
      created_at: "2026-06-20T09:00:00Z",
      updated_at: "2026-06-20T09:04:00Z",
      completion_evidence: {
        level: "visible_progress",
        result_verified: false,
        result_artifacts: [{ kind: "tool_result", label: "Successful tool result", redacted: true }],
        missing: ["final result verification"],
        signoff: false
      },
      result_quality: {
        state: "visible_progress",
        label: "Progress awaiting verification",
        summary: "The task shows progress.",
        result_verified: false,
        can_treat_as_done: false,
        needs_review: true,
        missing_checks: ["final result review"],
        next_step: "Open the task explanation.",
        signoff: false,
        redacted: true
      }
    });
    const run = mapRunTaskEvent({
      run_id: "run_shared",
      task_id: "task_shared",
      engine: "os",
      phase: "completed",
      message: "检查电脑",
      mode: "efficiency",
      requested_engine: "auto",
      created_at: "2026-06-20T09:00:00Z",
      updated_at: "2026-06-20T09:05:00Z",
      result_quality: {
        state: "visible_progress",
        result_verified: false,
        can_treat_as_done: false,
        needs_review: true,
        missing_checks: ["final result review"],
        next_step: "Open the task explanation.",
        signoff: false,
        redacted: true
      }
    });

    expect(task).toMatchObject({
      id: "task_shared",
      sourceTaskId: "task_shared",
      resultQuality: {
        state: "visible_progress",
        canTreatAsDone: false,
        missingChecks: ["final result review"]
      }
    });
    expect(run).toMatchObject({
      id: "run_shared",
      runId: "run_shared",
      sourceTaskId: "task_shared"
    });
    expect(mergeTaskSnapshots([run], [task])).toHaveLength(1);
    expect(mergeTaskSnapshots([run], [task])[0]).toMatchObject({
      id: "run_shared",
      sourceTaskId: "task_shared",
      resultQuality: {
        state: "visible_progress"
      }
    });
  });

  it("maps rollback metadata to truthful terminal states", () => {
    const base = {
      id: "task_rollback",
      user_goal: "整理文件",
      status: "failed",
      mode: "efficiency",
      final_summary: "Rollback completed",
      created_at: "2026-07-11T09:00:00Z",
      updated_at: "2026-07-11T09:01:00Z"
    };

    const restored = mapTaskEvent({
      ...base,
      metadata: {
        rollback: { state: "succeeded", attempted: 2, succeeded: 2, failed: 0 }
      }
    });
    const needsRepair = mapTaskEvent({
      ...base,
      metadata: {
        rollback: { state: "partial", attempted: 2, succeeded: 1, failed: 1 }
      }
    });

    expect(restored.state).toBe("rolled_back");
    expect(restored.rollback).toMatchObject({ state: "succeeded", attempted: 2, succeeded: 2 });
    expect(needsRepair.state).toBe("repair_required");
    expect(needsRepair.rollback).toMatchObject({ state: "partial", failed: 1 });
  });

  it("maps rejected cleanup approvals to denied requests with cleanup plan payloads", () => {
    const approval = mapApproval({
      id: "approval_1",
      task_id: "task_1",
      step_id: "step_1",
      approval_type: "cleanup_plan",
      message: "Please review cleanup",
      diff_preview: {
        plan: {
          id: "cleanup_approval",
          title: "Cleanup review",
          items: [
            {
              id: "delete_1",
              path: "C:\\Temp\\cache.bin",
              action: "delete",
              disposition: "permanent_delete"
            }
          ]
        }
      },
      risk_level: "R1_LOW",
      status: "rejected",
      created_at: "2026-06-20T10:00:00Z",
      expires_at: "2026-06-20T10:15:00Z"
    });

    expect(approval).toMatchObject({
      id: "approval_1",
      taskId: "task_1",
      stepId: "step_1",
      title: "清理计划审批",
      status: "denied",
      riskLevel: "high",
      createdAt: "2026-06-20T10:00:00Z",
      expiresAt: "2026-06-20T10:15:00Z"
    });
    expect(approval.cleanupPlan).toMatchObject({
      id: "cleanup_approval",
      items: [
        {
          id: "delete_1",
          path: "C:\\Temp\\cache.bin",
          disposition: "permanent_delete"
        }
      ]
    });
  });

  it("maps expired and unknown approval statuses to non-actionable states", () => {
    const baseApproval = {
      id: "approval_status",
      task_id: "task_status",
      step_id: "step_status",
      approval_type: "tool_call",
      message: "Review action",
      diff_preview: {},
      risk_level: "R2_REVERSIBLE_MODIFY",
      created_at: "2026-06-20T10:00:00Z"
    };

    expect(mapApproval({ ...baseApproval, status: "pending" }).status).toBe("pending");
    expect(mapApproval({ ...baseApproval, status: "approved" }).status).toBe("approved");
    expect(mapApproval({ ...baseApproval, status: "rejected" }).status).toBe("denied");
    expect(mapApproval({ ...baseApproval, status: "expired" }).status).toBe("expired");
    expect(mapApproval({ ...baseApproval, status: "paused_by_backend" }).status).toBe("unavailable");
  });

  it("maps command info and execution results to the shared execution shape", () => {
    expect(
      mapCommandInfo({
        name: "workspace.cleanup.preview",
        description: "Preview cleanup",
        category: "cleanup",
        input_schema: {
          type: "object",
          properties: {
            root: { type: "string" }
          }
        }
      })
    ).toMatchObject({
      name: "workspace.cleanup.preview",
      title: "workspace.cleanup.preview",
      description: "Preview cleanup",
      category: "cleanup",
      inputSchema: {
        type: "object"
      }
    });

    expect(
      mapCommandExecutionResult({
        ok: true,
        command: "workspace.cleanup.preview",
        result: { count: 2 },
        diagnostics: ["dry-run", 2],
        next_action: "review_cleanup_plan"
      })
    ).toEqual({
      ok: true,
      command: "workspace.cleanup.preview",
      result: { count: 2 },
      diagnostics: ["dry-run", "2"],
      nextAction: "review_cleanup_plan"
    });
  });
});
