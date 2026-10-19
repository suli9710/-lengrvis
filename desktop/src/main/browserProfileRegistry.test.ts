import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import { BrowserProfileRegistry } from "./browserProfileRegistry";

it("erases closed persistent profiles after reconstructing the registry", async () => {
  const directory = mkdtempSync(join(tmpdir(), "butler-profiles-"));
  try {
    const path = () => join(directory, "profiles.json");
    const erased: string[] = [];
    const registry = new BrowserProfileRegistry(path, async (partition) => { erased.push(partition); });
    const partition = `persist:lengrvis-butler-${"a".repeat(32)}`;
    registry.remember(partition);
    registry.remember(partition);
    await new BrowserProfileRegistry(path, async (value) => { erased.push(value); }).clear();
    expect(erased).toEqual([partition]);
    expect(() => registry.remember("persist:unrelated-user-profile")).toThrow();
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

it("revokes one scope's closed profiles without removing other grants", async () => {
  const directory = mkdtempSync(join(tmpdir(), "butler-scope-profiles-"));
  try {
    const path = () => join(directory, "profiles.json");
    const erased: string[] = [];
    const registry = new BrowserProfileRegistry(path, async value => { erased.push(value); });
    const one = `persist:lengrvis-butler-${"a".repeat(32)}`, two = `persist:lengrvis-butler-${"b".repeat(32)}`;
    registry.remember(one, "scope_one"); registry.remember(two, "scope_two");
    await new BrowserProfileRegistry(path, async value => { erased.push(value); }).clearScope("scope_one");
    expect(erased).toEqual([one]);
    await registry.clear();
    expect(erased).toEqual([one, two]);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});
