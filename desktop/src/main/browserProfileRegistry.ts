import { app, session } from "electron";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { writeJsonAtomically } from "./atomicJsonStore";

export class BrowserProfileRegistry {
  constructor(
    private readonly filePath: () => string = () => join(app.getPath("userData"), "butler-browser-profiles.json"),
    private readonly erasePartition: (partition: string) => Promise<void> = async (partition) => {
      const target = session.fromPartition(partition);
      await target.clearStorageData();
      await target.clearCache();
    }
  ) {}

  remember(partition: string, scopeId = ""): void {
    if (!validPartition(partition)) throw new Error("Invalid managed browser profile");
    const values = this.list();
    if (!values.some(value => value.partition === partition)) {
      if (values.length >= 128) throw new Error("Managed browser profile limit reached");
      writeJsonAtomically(this.filePath(), [...values, { partition, scopeId }]);
    }
  }

  async clear(): Promise<void> {
    const values = this.list();
    for (const value of values) await this.erasePartition(value.partition);
    if (existsSync(this.filePath())) rmSync(this.filePath());
  }

  async clearScope(scopeId: string): Promise<void> {
    const values = this.list().filter(value => value.scopeId === scopeId || !value.scopeId);
    for (const value of values) await this.erasePartition(value.partition);
    const removed = new Set(values.map(value => value.partition));
    writeJsonAtomically(this.filePath(), this.list().filter(value => !removed.has(value.partition)));
  }

  private list(): Array<{ partition: string; scopeId: string }> {
    const path = this.filePath();
    if (!existsSync(path)) return [];
    const values: unknown = JSON.parse(readFileSync(path, "utf8"));
    if (!Array.isArray(values) || values.length > 128) throw new Error("Invalid managed browser profile inventory");
    return values.map(value => {
      if (validPartition(value)) return { partition: value, scopeId: "" };
      if (!value || typeof value !== "object" || !validPartition(value.partition) || typeof value.scopeId !== "string") throw new Error("Invalid managed browser profile inventory");
      return { partition: value.partition, scopeId: value.scopeId };
    });
  }
}

function validPartition(value: unknown): value is string {
  return typeof value === "string" && /^persist:lengrvis-butler-[a-f0-9]{32}$/.test(value);
}
