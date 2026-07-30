import { randomBytes } from "node:crypto";
import {
  chmodSync,
  closeSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  renameSync,
  unlinkSync,
  writeSync
} from "node:fs";
import { dirname, join } from "node:path";

export const APPROVAL_SESSION_GENERATION_FILE = "approval_session_generation.secret";
export const APPROVAL_SESSION_POWER_EVENTS = ["lock-screen", "unlock-screen", "suspend", "resume"] as const;

const GENERATION_BYTES = 32;
const GENERATION_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const SESSION_BINDING_LABEL = "desktop-approval-session-v1";

export type ApprovalSessionPowerEvent = (typeof APPROVAL_SESSION_POWER_EVENTS)[number];

export interface ApprovalSessionPowerMonitor {
  on(event: ApprovalSessionPowerEvent, listener: () => void): unknown;
  removeListener(event: ApprovalSessionPowerEvent, listener: () => void): unknown;
}

export interface ApprovalSessionGenerationOptions {
  dataDir: string;
  generate?: () => string;
  persistGeneration?: (filePath: string, generation: string) => void;
}

export interface ApprovalSessionVisibilityGeneration {
  activate(): void;
  deactivate(): void;
}

export type ApprovalSessionVisibility = "foreground" | "background";

export class ApprovalSessionGenerationInvalidationError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "ApprovalSessionGenerationInvalidationError";
  }
}

export class ApprovalSessionGenerationManager {
  private generation = "";
  private initialized = false;
  private readonly generationPath: string;
  private readonly generate: () => string;
  private readonly persistGeneration: (filePath: string, generation: string) => void;

  constructor(options: ApprovalSessionGenerationOptions) {
    this.generationPath = join(options.dataDir, APPROVAL_SESSION_GENERATION_FILE);
    this.generate = options.generate ?? (() => randomBytes(GENERATION_BYTES).toString("base64url"));
    this.persistGeneration = options.persistGeneration ?? writeGenerationFileAtomic;
  }

  initialize(): void {
    if (this.initialized) return;
    this.replaceGeneration();
    this.initialized = true;
  }

  rotate(): void {
    if (!this.initialized) {
      throw new Error("Approval session generation has not been initialized by the primary app instance");
    }
    if (!this.generation) {
      this.deactivate();
      return;
    }
    this.replaceGeneration();
  }

  activate(): void {
    if (!this.initialized) {
      throw new Error("Approval session generation has not been initialized by the primary app instance");
    }
    if (this.generation) return;
    this.replaceGeneration();
  }

  deactivate(): void {
    if (!this.initialized) {
      throw new Error("Approval session generation has not been initialized by the primary app instance");
    }
    this.generation = "";
    removeStaleGeneration(invalidateCanonicalGeneration(this.generationPath));
  }

  private replaceGeneration(): void {
    this.generation = "";
    const stalePath = invalidateCanonicalGeneration(this.generationPath);
    try {
      const nextGeneration = this.generate();
      if (!GENERATION_PATTERN.test(nextGeneration)) {
        throw new Error("Approval session generation source returned a malformed value");
      }
      this.persistGeneration(this.generationPath, nextGeneration);
      this.generation = nextGeneration;
    } finally {
      removeStaleGeneration(stalePath);
    }
  }

  bindSigningPayload(payload: string): string {
    if (!this.generation || !payload) {
      throw new Error("Approval session generation is unavailable");
    }
    return bindApprovalSessionGeneration(payload, this.generation);
  }
}

/**
 * Serializes foreground/background mode calls while revoking desktop signing
 * synchronously on every background request. A later visibility request wins:
 * a stale foreground completion can never reactivate signing or reshow the UI.
 */
export class ApprovalSessionVisibilityCoordinator {
  private desiredVisibility: ApprovalSessionVisibility = "foreground";
  private transitionTail: Promise<void> = Promise.resolve();

  constructor(private readonly generation: ApprovalSessionVisibilityGeneration) {}

  isForegroundRequested(): boolean {
    return this.desiredVisibility === "foreground";
  }

  enterBackground(transition: () => Promise<void>): Promise<boolean> {
    this.desiredVisibility = "background";
    // This must happen before the first await and before the window is hidden.
    this.generation.deactivate();
    return this.enqueue("background", transition);
  }

  enterForeground(transition: () => Promise<void>): Promise<boolean> {
    this.desiredVisibility = "foreground";
    return this.enqueue("foreground", transition);
  }

  private enqueue(
    visibility: ApprovalSessionVisibility,
    transition: () => Promise<void>
  ): Promise<boolean> {
    const result = this.transitionTail.then(async () => {
      await transition();
      if (this.desiredVisibility !== visibility) return false;
      if (visibility === "foreground") {
        this.generation.activate();
      }
      return true;
    });
    this.transitionTail = result.then(
      () => undefined,
      () => undefined
    );
    return result;
  }
}

export function bindApprovalSessionGeneration(payload: string, generation: string): string {
  if (!payload || !GENERATION_PATTERN.test(generation)) {
    throw new Error("Approval session signing input is malformed");
  }
  return `${payload}\n${SESSION_BINDING_LABEL}\n${generation}`;
}

export function registerApprovalSessionPowerRotation(
  monitor: ApprovalSessionPowerMonitor,
  rotate: (event: ApprovalSessionPowerEvent) => void,
  onError: (event: ApprovalSessionPowerEvent, error: unknown) => void
): () => void {
  const listeners = new Map<ApprovalSessionPowerEvent, () => void>();
  for (const event of APPROVAL_SESSION_POWER_EVENTS) {
    const listener = () => {
      try {
        rotate(event);
      } catch (error) { // broad-exception-boundary: power events route every rotation failure to fail-closed shutdown.
        onError(event, error);
      }
    };
    listeners.set(event, listener);
    monitor.on(event, listener);
  }
  return () => {
    for (const [event, listener] of listeners) {
      monitor.removeListener(event, listener);
    }
    listeners.clear();
  };
}

function writeGenerationFileAtomic(filePath: string, generation: string): void {
  mkdirSync(dirname(filePath), { recursive: true });
  const unique = `${process.pid}.${randomBytes(8).toString("hex")}`;
  const tmpPath = `${filePath}.${unique}.tmp`;
  const fd = openSync(tmpPath, "wx", 0o600);
  try {
    writeSync(fd, `${generation}\n`, undefined, "utf-8");
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
  try {
    chmodSync(tmpPath, 0o600);
    renameSync(tmpPath, filePath);
    chmodSync(filePath, 0o600);
  } catch (error) { // broad-exception-boundary: atomic persistence cleans up before preserving the original failure.
    try {
      unlinkSync(tmpPath);
    } catch {
      // The temporary file may already have become the canonical generation.
    }
    // Never restore a stale generation after a failed rotation. A missing
    // canonical file makes the backend fail closed.
    throw error;
  }
}

function invalidateCanonicalGeneration(filePath: string): string | null {
  if (!existsSync(filePath)) return null;
  const stalePath = `${filePath}.${process.pid}.${randomBytes(8).toString("hex")}.stale`;
  try {
    renameSync(filePath, stalePath);
    return stalePath;
  } catch (renameError) {
    // A synchronous empty canonical value is also strictly invalid to the
    // backend. This fallback covers filesystems that reject replacement rename.
    try {
      const fd = openSync(filePath, "w", 0o600);
      try {
        fsyncSync(fd);
      } finally {
        closeSync(fd);
      }
      chmodSync(filePath, 0o600);
      return null;
    } catch (truncateError) {
      throw new ApprovalSessionGenerationInvalidationError(
        "Existing approval session generation could not be invalidated; the primary app must terminate",
        { cause: { renameError, truncateError } }
      );
    }
  }
}

function removeStaleGeneration(stalePath: string | null): void {
  if (!stalePath) return;
  try {
    unlinkSync(stalePath);
  } catch {
    // This non-authoritative path is ignored by the backend. Never restore it.
  }
}
