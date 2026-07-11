import { describe, expect, it } from "vitest";

import type { TaskEvent, TaskState } from "../../../shared/executionTypes";
import { activeOfficeAgentIds, officeAgentIdForTask, shouldRefreshOfficeAgentRuntime } from "./model";

function task(state: TaskState, agent = "", title = "执行任务", description = ""): TaskEvent {
  return {
    id: `${state}-${agent || title}`,
    title,
    description,
    state,
    agent,
    createdAt: "2026-07-11T00:00:00.000Z",
    updatedAt: "2026-07-11T00:00:01.000Z"
  };
}

describe("office agent activity", () => {
  it("does not treat the selected agent as working while the office is idle", () => {
    expect([...activeOfficeAgentIds("pm", [], false)]).toEqual([]);
    expect([...activeOfficeAgentIds("computer", [], false, true)]).toEqual(["computer"]);
  });

  it("animates only running and queued task owners", () => {
    const tasks = [
      task("running", "FileAgent"),
      task("queued", "BrowserAgent"),
      task("blocked", "ComputerAgent"),
      task("paused", "AppAgent")
    ];

    expect([...activeOfficeAgentIds("pm", tasks, false)].sort()).toEqual(["browser", "file"]);
  });

  it("falls back to the preferred agent only for an unassigned active task", () => {
    expect([...activeOfficeAgentIds("search", [task("running")], false)]).toEqual(["search"]);
    expect([...activeOfficeAgentIds("search", [task("completed")], false)]).toEqual([]);
  });

  it("keeps safety review independent from the selected agent", () => {
    expect([...activeOfficeAgentIds("pm", [], true)]).toEqual(["safety"]);
  });

  it("resolves feedback ownership from agent metadata or task copy", () => {
    expect(officeAgentIdForTask(task("completed", "DocumentAgent"))).toBe("file");
    expect(officeAgentIdForTask(task("failed", "", "浏览器任务失败"))).toBe("browser");
  });

  it("releases an agent from the working pose as soon as its task stops", () => {
    const workingRuntime = { x: 10, y: 20, activity: "正在工作", pose: "working" as const };
    const idleRuntime = { x: 10, y: 20, activity: "喝咖啡", pose: "coffee" as const };

    expect(shouldRefreshOfficeAgentRuntime(workingRuntime, false, false)).toBe(true);
    expect(shouldRefreshOfficeAgentRuntime(idleRuntime, false, false)).toBe(false);
    expect(shouldRefreshOfficeAgentRuntime(idleRuntime, true, false)).toBe(true);
    expect(shouldRefreshOfficeAgentRuntime(idleRuntime, false, true)).toBe(true);
  });
});
