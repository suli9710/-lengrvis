import { app } from "electron";
import { existsSync, unlinkSync } from "node:fs";
import { join } from "node:path";
import { writeJsonAtomically } from "./atomicJsonStore";

/** Keep browser writers excluded until both erase participants have finished. */
export class BrowserPrivacyBoundary {
  private active = 0;
  private reserved = false;

  constructor(private readonly markerPath: () => string | undefined = () =>
    app?.getPath ? join(app.getPath("userData"), "privacy-erase-pending.json") : undefined) {}

  assertAvailable(): void {
    const path = this.markerPath();
    if (this.reserved || (path && existsSync(path))) {
      throw new Error("本机数据清除尚未完成，请在隐私设置中重试。");
    }
  }

  enter(): () => void {
    this.assertAvailable();
    this.active += 1;
    return () => { this.active -= 1; };
  }

  reserve(): boolean {
    if (this.active) throw new Error("浏览器操作正在运行，请等待操作结束后重试删除本机数据。");
    const path = this.markerPath();
    const wasPending = this.reserved || Boolean(path && existsSync(path));
    if (path) writeJsonAtomically(path, { version: 1, status: "pending" });
    this.reserved = true;
    return wasPending;
  }

  release(): void {
    const path = this.markerPath();
    if (path && existsSync(path)) unlinkSync(path);
    this.reserved = false;
  }
}
