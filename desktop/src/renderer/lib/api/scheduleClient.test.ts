import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createScheduleEndpoint,
  deleteScheduleEndpoint,
  enableScheduleEndpoint,
  listSchedulesEndpoint,
  type ScheduleEndpointRequest,
  type ScheduleInput
} from "./scheduleClient";

const schedule: ScheduleInput = {
  cron: "0 9 * * *",
  goal: "Daily review",
  mode: "efficiency"
};

describe("schedule client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    (window as unknown as { lengrvis?: unknown }).lengrvis = undefined;
  });

  it("maps the browser fallback interface to backend requests", async () => {
    const request = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    const endpointRequest = request as ScheduleEndpointRequest;

    await listSchedulesEndpoint(endpointRequest);
    await createScheduleEndpoint(endpointRequest, schedule);
    await deleteScheduleEndpoint(endpointRequest, "schedule/one");
    await enableScheduleEndpoint(endpointRequest, "schedule/one", true);

    expect(request.mock.calls.map(([input]) => input)).toEqual([
      { endpoint: "/api/schedules" },
      { endpoint: "/api/schedules", method: "POST", body: schedule },
      { endpoint: "/api/schedules/schedule/one", method: "DELETE" },
      {
        endpoint: "/api/schedules/schedule/one/enable",
        method: "POST",
        body: { enabled: true }
      }
    ]);
  });

  it("prefers the Electron adapter", async () => {
    const response = { ok: true, status: 200 };
    const schedules = {
      list: vi.fn().mockResolvedValue(response),
      create: vi.fn().mockResolvedValue(response),
      delete: vi.fn().mockResolvedValue(response),
      enable: vi.fn().mockResolvedValue(response)
    };
    (window as unknown as { lengrvis?: { schedules: typeof schedules } }).lengrvis = { schedules };
    const request = vi.fn();
    const endpointRequest = request as ScheduleEndpointRequest;

    await listSchedulesEndpoint(endpointRequest);
    await createScheduleEndpoint(endpointRequest, schedule);
    await deleteScheduleEndpoint(endpointRequest, "schedule-one");
    await enableScheduleEndpoint(endpointRequest, "schedule-one", false);

    expect(request).not.toHaveBeenCalled();
    expect(schedules.create).toHaveBeenCalledWith(schedule);
    expect(schedules.delete).toHaveBeenCalledWith("schedule-one");
    expect(schedules.enable).toHaveBeenCalledWith({ scheduleId: "schedule-one", enabled: false });
  });
});
