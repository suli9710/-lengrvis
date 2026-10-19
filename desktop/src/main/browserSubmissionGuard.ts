import type { OnBeforeRequestListenerDetails } from "electron";

export interface NativeFormSubmission {
  url: string;
  entries: [string, string][];
}

export type BrowserHostRequest = Pick<OnBeforeRequestListenerDetails, "url"> &
  Partial<Pick<OnBeforeRequestListenerDetails, "method" | "resourceType" | "uploadData" | "webContentsId">>;

type SubmissionDecision = "submitted" | "not_submitted" | "unknown";
interface SubmissionAttempt {
  url: string;
  body: Buffer;
  decision?: SubmissionDecision;
  settle: (decision: SubmissionDecision) => void;
}

/** One confirmed native POST, checked after page formdata handlers have run. */
export class BrowserSubmissionGuard {
  private active?: SubmissionAttempt;
  private closed = false;

  arm(expected: NativeFormSubmission) {
    if (this.closed) throw new Error("Browser session is closed");
    if (this.active && !this.active.decision) throw new Error("A browser submission is already pending");
    const url = new URL(expected.url);
    url.hash = "";
    if (!/^https?:$/.test(url.protocol) || url.username || url.password) throw new Error("Unsupported form destination");
    const body = Buffer.from(new URLSearchParams(expected.entries).toString(), "utf8");
    if (body.length > 256 * 1024) throw new Error("Form submission is too large for a concrete confirmation");
    let settle!: (decision: SubmissionDecision) => void;
    const result = new Promise<SubmissionDecision>(resolve => { settle = resolve; });
    const attempt = { url: url.href, body, settle, decision: undefined as SubmissionDecision | undefined };
    this.active = attempt;
    const finish = (decision: SubmissionDecision) => this.finish(attempt, decision);
    return {
      cancel: () => finish("unknown"),
      notSubmitted: () => finish("not_submitted"),
      wait: async (timeoutMs = 5000): Promise<SubmissionDecision> => {
        const timer = setTimeout(() => finish("unknown"), timeoutMs);
        try { return await result; } finally { clearTimeout(timer); }
      }
    };
  }

  allowRequest(request: BrowserHostRequest, networkAllowed: boolean, userControl = false): boolean {
    if (this.closed) return false;
    if (userControl) return networkAllowed;
    const attempt = this.active;
    const readOnly = request.method === "GET" || request.method === "HEAD";
    if (!attempt) return networkAllowed && readOnly;
    if (request.resourceType !== "mainFrame") {
      // Native forms do not need script/iframe writes. A formdata handler must
      // not redirect this approved operation into an unbound write channel.
      return networkAllowed && readOnly;
    }
    if (attempt.decision) return networkAllowed && readOnly;
    const chunks = request.uploadData ?? [];
    const boundedBytes = chunks.length > 0 && chunks.every(chunk =>
      !chunk.file && !chunk.blobUUID && Buffer.isBuffer(chunk.bytes)
    ) && chunks.reduce((size, chunk) => size + chunk.bytes.length, 0) <= 256 * 1024;
    const matches = networkAllowed && request.method === "POST" && request.url === attempt.url &&
      boundedBytes && Buffer.concat(chunks.map(chunk => chunk.bytes)).equals(attempt.body);
    this.finish(attempt, matches ? "submitted" : "not_submitted");
    return matches;
  }

  release(): void {
    if (this.active) this.finish(this.active, "unknown");
    this.active = undefined;
  }

  close(): void {
    this.closed = true;
    this.release();
  }

  private finish(attempt: SubmissionAttempt, decision: SubmissionDecision): void {
    if (attempt.decision) return;
    attempt.decision = decision;
    attempt.body.fill(0);
    attempt.url = "";
    attempt.settle(decision);
  }
}
