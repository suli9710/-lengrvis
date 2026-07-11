import { describe, expect, it, vi } from "vitest";

import { BackendLifecycleCoordinator } from "./backendLifecycle";

describe("BackendLifecycleCoordinator", () => {
  it("coalesces concurrent start requests into one operation", async () => {
    let releaseStart: ((value: string) => void) | undefined;
    const startOperation = vi.fn(() => new Promise<string>((resolve) => {
      releaseStart = resolve;
    }));
    const stopOperation = vi.fn(async () => "stopped");
    const lifecycle = new BackendLifecycleCoordinator(startOperation, stopOperation);

    const first = lifecycle.start();
    const second = lifecycle.start();

    await Promise.resolve();
    expect(startOperation).toHaveBeenCalledTimes(1);
    releaseStart?.("running");
    await expect(Promise.all([first, second])).resolves.toEqual(["running", "running"]);
  });

  it("waits for a pending start before stopping and restarts only after stop completes", async () => {
    let releaseFirstStart: ((value: string) => void) | undefined;
    let releaseSecondStart: ((value: string) => void) | undefined;
    let releaseStop: ((value: string) => void) | undefined;
    const startOperation = vi.fn()
      .mockImplementationOnce(() => new Promise<string>((resolve) => {
        releaseFirstStart = resolve;
      }))
      .mockImplementationOnce(() => new Promise<string>((resolve) => {
        releaseSecondStart = resolve;
      }));
    const stopOperation = vi.fn(() => new Promise<string>((resolve) => {
      releaseStop = resolve;
    }));
    const lifecycle = new BackendLifecycleCoordinator(startOperation, stopOperation);

    const initialStart = lifecycle.start();
    const stop = lifecycle.stop();
    const restart = lifecycle.start();
    await Promise.resolve();

    expect(startOperation).toHaveBeenCalledTimes(1);
    expect(stopOperation).not.toHaveBeenCalled();

    releaseFirstStart?.("first-running");
    await initialStart;
    await Promise.resolve();
    expect(stopOperation).toHaveBeenCalledTimes(1);
    expect(startOperation).toHaveBeenCalledTimes(1);

    releaseStop?.("stopped");
    await stop;
    await Promise.resolve();
    expect(startOperation).toHaveBeenCalledTimes(2);

    releaseSecondStart?.("second-running");
    await expect(restart).resolves.toBe("second-running");
  });
});
