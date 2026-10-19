import { lstat, readdir, realpath, rmdir, unlink } from "node:fs/promises";
import { isAbsolute, join, parse, relative, resolve, sep } from "node:path";

export const SCREENSHOT_ARTIFACT_NAME = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.png$/i;
const LEGACY_DIRECTORY_NAME = /^lengrvis-browser-screenshots-([1-9][0-9]*)-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function rejectScreenshotLinks(path: string): Promise<void> {
  const target = resolve(path);
  let current = parse(target).root;
  for (const segment of target.slice(current.length).split(sep).filter(Boolean)) {
    current = join(current, segment);
    try {
      if ((await lstat(current)).isSymbolicLink()) throw new Error("Screenshot cleanup refuses linked paths");
    } catch (error) { // broad-exception-boundary: missing path ends safe ownership walk; other errors propagate.
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      break;
    }
  }
}

export async function ownedScreenshotFiles(root: string, markerName?: string): Promise<string[]> {
  await rejectScreenshotLinks(root);
  const canonical = await realpath(root);
  const files: string[] = [];
  for (const name of await readdir(root)) {
    const path = join(root, name);
    const inside = relative(canonical, resolve(path));
    const info = await lstat(path);
    if (inside.startsWith(`..${sep}`) || inside === ".." || isAbsolute(inside)
      || (!SCREENSHOT_ARTIFACT_NAME.test(name) && name !== markerName)
      || !info.isFile() || info.isSymbolicLink() || info.nlink !== 1
      || (process.getuid && info.uid !== process.getuid())) {
      throw new Error("Screenshot cleanup refuses unowned artifacts or linked paths");
    }
    files.push(path);
  }
  return files;
}

/** Upgrade cleanup for the former per-process directories in the user's TEMP. */
export async function clearLegacyBrowserScreenshots(tempRoot: string): Promise<void> {
  await rejectScreenshotLinks(tempRoot);
  for (const name of await readdir(tempRoot)) {
    const match = LEGACY_DIRECTORY_NAME.exec(name);
    if (!match || isLiveProcess(Number(match[1]))) continue;
    const root = join(tempRoot, name);
    // TEMP belongs to the current OS user; on Unix additionally verify UID.
    // Dead PID + exact directory/PNG UUID layout identifies old app artifacts.
    const info = await lstat(root);
    if (!info.isDirectory() || info.isSymbolicLink() || (process.getuid && info.uid !== process.getuid())) {
      throw new Error("Screenshot cleanup refuses an unowned legacy directory");
    }
    const files = await ownedScreenshotFiles(root);
    for (const file of files) await unlink(file);
    await rmdir(root);
  }
}

function isLiveProcess(pid: number): boolean {
  if (!Number.isSafeInteger(pid) || pid <= 0) return true;
  try { process.kill(pid, 0); return true; }
  catch (error) { // broad-exception-boundary: process probe treats only ESRCH as stopped.
    return (error as NodeJS.ErrnoException).code !== "ESRCH";
  }
}
