import { createHmac, timingSafeEqual } from "node:crypto";
import type { BrowserAction } from "../shared/browserTypes";

export interface BrowserExecutionClaim {
  schema: "browser-execution/v1";
  nonce: string;
  expires_at: number;
  operation: "open" | "inspect" | "act" | "adopt";
  previous_task_id?: string;
  session_id: string;
  task_id: string;
  account_id: string;
  profile_key: string;
  allowed_origins: string[];
  action: BrowserAction;
  approval_id: string;
  expected_fingerprint: string;
  scope_versions: Record<string, number>;
}

const WRITE_KINDS = new Set(["click", "fill", "submit", "scroll", "cua"]);
const ACTION_KINDS = new Set(["open", "navigate", "observe", "screenshot", "wait", ...WRITE_KINDS]);

export class BrowserExecutionGrantVerifier {
  private used = new Map<string, number>();
  constructor(private readonly token: () => string, private readonly now: () => number = Date.now) {}

  consume(value: unknown): BrowserExecutionClaim {
    if (!value || typeof value !== "object") throw new Error("Missing browser execution authorization");
    const grant = value as Record<string, unknown>;
    if (typeof grant.payload !== "string" || grant.payload.length > 200_000 || typeof grant.signature !== "string" || !/^[a-f0-9]{64}$/.test(grant.signature)) {
      throw new Error("Invalid browser execution authorization");
    }
    const key = this.token();
    if (!key) throw new Error("Desktop authorization unavailable");
    const expected = createHmac("sha256", key).update(`browser-execution/v1:${grant.payload}`).digest();
    if (!timingSafeEqual(expected, Buffer.from(grant.signature, "hex"))) throw new Error("Invalid browser execution signature");
    const claim = JSON.parse(Buffer.from(grant.payload, "base64").toString("utf8")) as BrowserExecutionClaim;
    const now = this.now();
    for (const [nonce, expiry] of this.used) if (expiry <= now) this.used.delete(nonce);
    if (claim.schema !== "browser-execution/v1" || !/^[a-f0-9]{32}$/.test(claim.nonce) ||
        !Number.isSafeInteger(claim.expires_at) || claim.expires_at <= now || claim.expires_at > now + 60_000 ||
        !["open", "inspect", "act", "adopt"].includes(claim.operation) ||
        !validId(claim.session_id) || !validId(claim.task_id) || !claim.action || !ACTION_KINDS.has(claim.action.kind) ||
        !Array.isArray(claim.allowed_origins) || !claim.allowed_origins.length || claim.allowed_origins.length > 32 ||
        !claim.allowed_origins.every(exactOrigin) || typeof claim.account_id !== "string" || typeof claim.profile_key !== "string") {
      throw new Error("Invalid or expired browser execution command");
    }
    if (this.used.has(claim.nonce)) throw new Error("Browser execution command was already used");
    if (claim.operation === "adopt" && (!validId(claim.previous_task_id) || claim.previous_task_id === claim.task_id || claim.action.kind !== "observe")) throw new Error("Invalid browser continuation owner");
    if (this.used.size >= 2000) throw new Error("Browser execution authorization capacity reached");
    if (claim.operation === "act" && WRITE_KINDS.has(claim.action.kind) && (!validId(claim.approval_id) || !/^[a-f0-9]{64}$/.test(claim.expected_fingerprint))) {
      throw new Error("Browser write lacks an approved target snapshot");
    }
    if (claim.action.url && !claim.allowed_origins.includes(new URL(claim.action.url).origin)) throw new Error("Browser command leaves its authorized origins");
    this.used.set(claim.nonce, claim.expires_at);
    return claim;
  }
}

function validId(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9_-]{1,256}$/.test(value);
}
function exactOrigin(value: unknown): boolean {
  if (typeof value !== "string") return false;
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) && url.origin === value && !url.username && !url.password;
  } catch { return false; }
}
