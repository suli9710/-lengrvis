import { mkdir, rm, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { randomUUID } from "node:crypto";

const SCREENSHOT_DIRECTORY_PREFIX = "lengrvis-browser-screenshots-";

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

  constructor(
    private readonly rootDir = createBrowserScreenshotRoot(),
    private readonly budgets: BrowserScreenshotBudgets = DEFAULT_BROWSER_SCREENSHOT_BUDGETS
  ) {}

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
      this.artifacts = [];
      await rm(this.rootDir, { recursive: true, force: true });
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

    await mkdir(this.rootDir, { recursive: true });
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
    this.artifacts = this.artifacts.filter((candidate) => candidate !== artifact);
    await unlink(artifact.path).catch(() => undefined);
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
}

export function createBrowserScreenshotRoot(): string {
  return join(tmpdir(), `${SCREENSHOT_DIRECTORY_PREFIX}${process.pid}-${randomUUID()}`);
}

export function isBrowserScreenshotArtifactUrl(value: string | undefined): value is string {
  if (!value) return false;
  try {
    const path = fileURLToPath(value);
    return basename(dirname(path)).startsWith(SCREENSHOT_DIRECTORY_PREFIX)
      && /^[0-9a-f-]{36}\.png$/i.test(basename(path));
  } catch {
    return false;
  }
}
