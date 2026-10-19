import { mkdir, readFile, rmdir, unlink, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createHash, randomUUID } from "node:crypto";
import { clearLegacyBrowserScreenshots, ownedScreenshotFiles, rejectScreenshotLinks, SCREENSHOT_ARTIFACT_NAME } from "./browserScreenshotCleanup";

const SCREENSHOT_DIRECTORY_PREFIX = "lengrvis-browser-screenshots-";
const OWNER_MARKER = ".lengrvis-screenshot-owner";
const OWNER_ID = createHash("sha256").update(homedir()).digest("hex");
const OWNER_CONTENT = JSON.stringify({ schema: 1, owner: OWNER_ID });

export interface BrowserScreenshotBudgets {
  maxArtifactBytes: number;
  maxArtifactsPerSession: number;
  maxBytesPerSession: number;
  maxBytesGlobal: number;
}

export const DEFAULT_BROWSER_SCREENSHOT_BUDGETS: BrowserScreenshotBudgets = {
  maxArtifactBytes: 8 * 1024 * 1024,
  maxArtifactsPerSession: 8,
  maxBytesPerSession: 32 * 1024 * 1024,
  maxBytesGlobal: 64 * 1024 * 1024
};

interface ScreenshotArtifact {
  path: string;
  sessionId: string;
  size: number;
  url: string;
}

export class BrowserScreenshotStore {
  private artifacts: ScreenshotArtifact[] = [];
  private queue: Promise<void> = Promise.resolve();
  private initialized = false;

  constructor(
    private readonly rootDir = createBrowserScreenshotRoot(),
    private readonly budgets: BrowserScreenshotBudgets = DEFAULT_BROWSER_SCREENSHOT_BUDGETS,
    private readonly legacyTempRoot: string | undefined = rootDir === createBrowserScreenshotRoot() ? tmpdir() : undefined
  ) {}

  initialize(): Promise<void> {
    const pending = this.queue.then(() => this.initializeOperation());
    this.queue = pending.then(() => undefined, () => undefined);
    return pending;
  }

  save(sessionId: string, png: Buffer): Promise<string> {
    const pending = this.queue.then(() => this.saveOperation(sessionId, png));
    this.queue = pending.then(() => undefined, () => undefined);
    return pending;
  }

  removeSession(sessionId: string): Promise<void> {
    const pending = this.queue.then(() => this.removeSessionOperation(sessionId));
    this.queue = pending.then(() => undefined, () => undefined);
    return pending;
  }

  clear(): Promise<void> {
    const pending = this.queue.then(async () => {
      await this.ensureOwnedRoot();
      await this.clearOwnedRoot();
      if (this.legacyTempRoot) await clearLegacyBrowserScreenshots(this.legacyTempRoot);
      this.artifacts = [];
      this.initialized = false;
    });
    this.queue = pending.then(() => undefined, () => undefined);
    return pending;
  }

  private async saveOperation(sessionId: string, png: Buffer): Promise<string> {
    const maximumSingleArtifact = Math.min(
      this.budgets.maxArtifactBytes,
      this.budgets.maxBytesPerSession,
      this.budgets.maxBytesGlobal
    );
    if (!sessionId || png.length === 0 || png.length > maximumSingleArtifact) {
      throw new Error(`Browser screenshot exceeds the ${maximumSingleArtifact}-byte artifact budget`);
    }

    await this.initializeOperation();
    await this.ensureOwnedRoot();
    const artifactPath = join(this.rootDir, `${randomUUID()}.png`);
    await writeFile(artifactPath, png, { flag: "wx", mode: 0o600 });
    const artifact: ScreenshotArtifact = {
      path: artifactPath,
      sessionId,
      size: png.length,
      url: pathToFileURL(artifactPath).toString()
    };
    this.artifacts.push(artifact);
    await this.enforceBudgets(sessionId);
    return artifact.url;
  }

  private async enforceBudgets(sessionId: string): Promise<void> {
    while (this.sessionArtifacts(sessionId).length > this.budgets.maxArtifactsPerSession) {
      await this.evict(this.sessionArtifacts(sessionId)[0]);
    }
    while (this.sessionBytes(sessionId) > this.budgets.maxBytesPerSession) {
      await this.evict(this.sessionArtifacts(sessionId)[0]);
    }
    while (this.totalBytes() > this.budgets.maxBytesGlobal) {
      await this.evict(this.artifacts[0]);
    }
  }

  private async removeSessionOperation(sessionId: string): Promise<void> {
    for (const artifact of [...this.sessionArtifacts(sessionId)]) {
      await this.evict(artifact);
    }
  }

  private async evict(artifact: ScreenshotArtifact | undefined): Promise<void> {
    if (!artifact) return;
    await ownedScreenshotFiles(this.rootDir, OWNER_MARKER);
    await unlink(artifact.path).catch(error => { if (error.code !== "ENOENT") throw error; });
    this.artifacts = this.artifacts.filter((candidate) => candidate !== artifact);
  }

  private sessionArtifacts(sessionId: string): ScreenshotArtifact[] {
    return this.artifacts.filter((artifact) => artifact.sessionId === sessionId);
  }

  private sessionBytes(sessionId: string): number {
    return this.sessionArtifacts(sessionId).reduce((total, artifact) => total + artifact.size, 0);
  }

  private totalBytes(): number {
    return this.artifacts.reduce((total, artifact) => total + artifact.size, 0);
  }

  private async initializeOperation(): Promise<void> {
    if (this.initialized) return;
    await this.ensureOwnedRoot();
    await this.clearOwnedRoot();
    if (this.legacyTempRoot) await clearLegacyBrowserScreenshots(this.legacyTempRoot);
    await this.ensureOwnedRoot();
    this.initialized = true;
  }

  private async ensureOwnedRoot(): Promise<void> {
    if (!resolve(this.rootDir) || resolve(this.rootDir) === dirname(resolve(this.rootDir))) {
      throw new Error("Invalid screenshot root");
    }
    await rejectScreenshotLinks(this.rootDir);
    await mkdir(this.rootDir, { recursive: true, mode: 0o700 });
    const files = await ownedScreenshotFiles(this.rootDir, OWNER_MARKER);
    const marker = join(this.rootDir, OWNER_MARKER);
    if (files.includes(marker)) {
      if (await readFile(marker, "utf8") !== OWNER_CONTENT) throw new Error("Screenshot directory ownership changed");
    } else {
      if (files.length) throw new Error("Screenshot directory has no ownership marker");
      await writeFile(marker, OWNER_CONTENT, { flag: "wx", mode: 0o600 });
    }
  }

  private async clearOwnedRoot(): Promise<void> {
    const files = await ownedScreenshotFiles(this.rootDir, OWNER_MARKER);
    for (const file of files.filter(path => basename(path) !== OWNER_MARKER)) await unlink(file);
    await unlink(join(this.rootDir, OWNER_MARKER));
    await rmdir(this.rootDir);
  }
}

export function createBrowserScreenshotRoot(): string {
  return join(tmpdir(), `${SCREENSHOT_DIRECTORY_PREFIX}${OWNER_ID.slice(0, 16)}`);
}

export function isBrowserScreenshotArtifactUrl(value: string | undefined): value is string {
  if (!value) return false;
  try {
    const path = fileURLToPath(value);
    return basename(dirname(path)).startsWith(SCREENSHOT_DIRECTORY_PREFIX)
      && SCREENSHOT_ARTIFACT_NAME.test(basename(path));
  } catch {
    return false;
  }
}
