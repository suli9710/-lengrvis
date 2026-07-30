import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

import type { BackendStatus } from "../shared/desktopBridgeTypes";
import { assertLoopbackBackendUrl } from "./backendUrl";

const HEALTH_ENDPOINT = "/health";
const RUNTIME_STATUS_ENDPOINT = "/api/runtime/status";
const RUNTIME_FOREGROUND_ENDPOINT = "/api/runtime/foreground";
const RUNTIME_BACKGROUND_ENDPOINT = "/api/runtime/background";
const EMERGENCY_STOP_ENDPOINT = "/api/runtime/emergency-stop";
const DESKTOP_API_TOKEN_HEADER = "X-Lengrvis-Desktop-Token";
const DEFAULT_IDENTITY_LEASE_TTL_MS = 70_000;

type BackendHealth = NonNullable<BackendStatus["health"]>;
type RuntimeMode = "foreground" | "background";

type IdentityLease = Readonly<{
  backendUrl: URL;
  epoch: number;
  expiresAtMs: number;
  origin: string;
}>;

type TokenRequestInit = Omit<RequestInit, "headers"> & {
  headers?: Record<string, string>;
};

export interface BackendControlTransportOptions {
  identityLeaseTtlMs?: number;
  now?: () => number;
}

export class BackendControlTransport {
  private identityEpoch = 0;
  private readonly identityLeaseTtlMs: number;
  private readonly now: () => number;
  private verifiedUntilMs = 0;
  private verifiedOrigin: string | null = null;

  constructor(
    private readonly getBaseUrl: () => string,
    private readonly desktopApiToken: string,
    options: BackendControlTransportOptions = {}
  ) {
    this.identityLeaseTtlMs = options.identityLeaseTtlMs ?? DEFAULT_IDENTITY_LEASE_TTL_MS;
    if (!Number.isFinite(this.identityLeaseTtlMs) || this.identityLeaseTtlMs <= 0) {
      throw new Error("Backend identity lease TTL must be a positive finite duration");
    }
    this.now = options.now ?? (() => performance.now());
  }

  getVerifiedDesktopApiToken(): string {
    if (this.verifiedOrigin === null) return "";
    if (this.now() > this.verifiedUntilMs) {
      this.invalidateIdentity();
      return "";
    }
    const currentOrigin = this.currentConfiguredOrigin();
    if (currentOrigin !== this.verifiedOrigin) {
      this.invalidateIdentity();
      return "";
    }
    return this.desktopApiToken;
  }

  invalidateIdentity(): void {
    this.identityEpoch += 1;
    this.verifiedOrigin = null;
    this.verifiedUntilMs = 0;
  }

  async probeHealth(): Promise<BackendHealth> {
    const epoch = this.beginIdentityAttempt();
    const baseUrl = this.getBaseUrl();
    return this.probeHealthAt(baseUrl, epoch, loopbackOrigin(baseUrl));
  }

  async probeStatus(): Promise<{
    health: BackendHealth;
    runtime: Partial<BackendStatus>;
  }> {
    const epoch = this.beginIdentityAttempt();
    const baseUrl = this.getBaseUrl();
    const backendUrl = loopbackBackendUrl(baseUrl);
    const origin = backendUrl?.origin ?? null;
    const health = await this.probeHealthAt(baseUrl, epoch, origin);
    const lease = backendUrl === null || origin === null
      ? null
      : this.currentIdentityLease(backendUrl, epoch, origin);
    if (!health.ok || lease === null) {
      return {
        health: health.ok && backendUrl !== null
          ? { ...health, ok: false, identityVerified: false }
          : health,
        runtime: {}
      };
    }
    const runtime = await this.fetchRuntimeStatus(lease);
    if (!this.identityLeaseIsCurrent(lease)) {
      return {
        health: { ...health, ok: false, identityVerified: false },
        runtime: {}
      };
    }
    return {
      health,
      runtime
    };
  }

  async setRuntimeMode(
    mode: RuntimeMode,
    reason: string,
    timeoutMs = 35_000
  ): Promise<Error | null> {
    let lease: IdentityLease | null = null;
    try {
      lease = await this.requireFreshLoopbackIdentity("Runtime mode desktop token request");
      const endpoint = mode === "foreground" ? RUNTIME_FOREGROUND_ENDPOINT : RUNTIME_BACKGROUND_ENDPOINT;
      const response = await this.fetchWithDesktopToken(lease, endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
        signal: AbortSignal.timeout(timeoutMs)
      });
      if (!response.ok) {
        throw new Error(`Runtime mode request failed: ${response.status} ${await runtimeModeErrorText(response)}`);
      }
      return null;
    } catch (error) { // broad-exception-boundary
      if (lease !== null) this.invalidateIdentityLease(lease);
      return error instanceof Error ? error : new Error("Runtime mode request failed");
    }
  }

  async emergencyStop(): Promise<{ ok: boolean; [key: string]: unknown }> {
    const lease = await this.requireFreshLoopbackIdentity("Emergency stop desktop token request");
    try {
      const response = await this.fetchWithDesktopToken(lease, EMERGENCY_STOP_ENDPOINT, {
        method: "POST",
        signal: AbortSignal.timeout(3000)
      });
      if (!response.ok) {
        throw new Error(`Emergency stop request failed: ${response.status}`);
      }
      const data = await response.json().catch(() => ({}));
      return data && typeof data === "object" && !Array.isArray(data)
        ? { ...data as Record<string, unknown>, ok: (data as Record<string, unknown>).ok === true }
        : { ok: false };
    } catch (error) { // broad-exception-boundary
      this.invalidateIdentityLease(lease);
      throw error;
    }
  }

  private async requireFreshLoopbackIdentity(context: string): Promise<IdentityLease> {
    const epoch = this.beginIdentityAttempt();
    const backendUrl = assertLoopbackBackendUrl(this.getBaseUrl(), context);
    const origin = backendUrl.origin;
    const health = await this.probeHealthAt(backendUrl.toString(), epoch, origin);
    const lease = this.currentIdentityLease(backendUrl, epoch, origin);
    if (!health.identityVerified || lease === null) {
      if (epoch === this.identityEpoch) this.invalidateIdentity();
      throw new Error("Backend identity challenge failed; desktop token request was not sent");
    }
    return lease;
  }

  private async probeHealthAt(
    baseUrl: string,
    epoch: number,
    eligibleOrigin: string | null
  ): Promise<BackendHealth> {
    const startedAt = Date.now();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 1500);

    try {
      const challenge = randomBytes(24).toString("base64url");
      const healthUrl = new URL(HEALTH_ENDPOINT, baseUrl);
      healthUrl.searchParams.set("desktop_challenge", challenge);
      const response = await fetch(healthUrl, {
        method: "GET",
        redirect: "error",
        signal: controller.signal
      });
      if (!responseStayedOnOrigin(response, healthUrl.origin)) {
        throw new Error("Backend health challenge left the configured origin");
      }
      const data = await response.clone().json().catch(() => ({})) as Record<string, unknown>;
      const suppliedProof = typeof data.desktop_proof === "string" ? data.desktop_proof : "";
      const expectedProof = createHmac("sha256", this.desktopApiToken)
        .update(challenge, "utf8")
        .digest("hex");
      const identityVerified = constantTimeTextEqual(suppliedProof, expectedProof);
      const guardianReady = data.mode === "guardian"
        && data.shellMode === "foreground"
        && data.fullBackendState === "running";
      const ok = response.ok && identityVerified && (data.mode !== "guardian" || guardianReady);
      const committed = this.commitIdentityAttempt(epoch, eligibleOrigin, identityVerified);
      if (!committed) {
        return { ok: false, identityVerified: false, latencyMs: Date.now() - startedAt };
      }
      return { ok, identityVerified, latencyMs: Date.now() - startedAt };
    } catch {
      this.commitIdentityAttempt(epoch, null, false);
      return { ok: false, identityVerified: false, latencyMs: Date.now() - startedAt };
    } finally {
      clearTimeout(timeout);
    }
  }

  private async fetchRuntimeStatus(lease: IdentityLease): Promise<Partial<BackendStatus>> {
    try {
      if (!this.identityLeaseIsCurrent(lease)) return {};
      const response = await fetch(new URL(RUNTIME_STATUS_ENDPOINT, lease.backendUrl), {
        method: "GET",
        headers: { [DESKTOP_API_TOKEN_HEADER]: this.desktopApiToken },
        redirect: "error",
        signal: AbortSignal.timeout(1500)
      });
      if (!response.ok) return {};
      const data = await response.json() as Record<string, unknown>;
      return {
        shellMode: data.shellMode === "foreground"
          ? "foreground"
          : data.shellMode === "background" ? "background" : undefined,
        guardianState: stringValue(data.guardianState),
        fullBackendState: stringValue(data.fullBackendState),
        fullBackendPort: typeof data.fullBackendPort === "number" ? data.fullBackendPort : undefined,
        lastWakeReason: stringValue(data.lastWakeReason)
      };
    } catch {
      return {};
    }
  }

  private beginIdentityAttempt(): number {
    this.identityEpoch += 1;
    this.verifiedOrigin = null;
    this.verifiedUntilMs = 0;
    return this.identityEpoch;
  }

  private commitIdentityAttempt(
    epoch: number,
    eligibleOrigin: string | null,
    identityVerified: boolean
  ): boolean {
    if (epoch !== this.identityEpoch) return false;
    const releaseToken = identityVerified && eligibleOrigin !== null;
    this.verifiedOrigin = releaseToken ? eligibleOrigin : null;
    this.verifiedUntilMs = releaseToken ? this.now() + this.identityLeaseTtlMs : 0;
    return true;
  }

  private currentIdentityLease(
    backendUrl: URL,
    epoch: number,
    origin: string
  ): IdentityLease | null {
    const lease = { backendUrl, epoch, expiresAtMs: this.verifiedUntilMs, origin };
    return this.identityLeaseIsCurrent(lease) ? lease : null;
  }

  private identityLeaseIsCurrent(lease: IdentityLease): boolean {
    if (lease.epoch !== this.identityEpoch) return false;
    const current = lease.expiresAtMs === this.verifiedUntilMs
      && this.now() <= lease.expiresAtMs
      && lease.origin === this.verifiedOrigin
      && lease.origin === this.currentConfiguredOrigin();
    if (!current) this.invalidateIdentity();
    return current;
  }

  private invalidateIdentityLease(lease: IdentityLease): void {
    if (lease.epoch === this.identityEpoch) this.invalidateIdentity();
  }

  private currentConfiguredOrigin(): string | null {
    try {
      return loopbackOrigin(this.getBaseUrl());
    } catch {
      return null;
    }
  }

  private fetchWithDesktopToken(
    lease: IdentityLease,
    endpoint: string,
    init: TokenRequestInit
  ): Promise<Response> {
    if (!this.identityLeaseIsCurrent(lease)) {
      throw new Error("Backend identity changed; desktop token request was not sent");
    }
    return fetch(new URL(endpoint, lease.backendUrl), {
      ...init,
      headers: {
        ...init.headers,
        [DESKTOP_API_TOKEN_HEADER]: this.desktopApiToken
      },
      redirect: "error"
    });
  }
}

function loopbackBackendUrl(baseUrl: string): URL | null {
  try {
    return assertLoopbackBackendUrl(baseUrl, "Desktop backend control");
  } catch {
    return null;
  }
}

function loopbackOrigin(baseUrl: string): string | null {
  return loopbackBackendUrl(baseUrl)?.origin ?? null;
}

function responseStayedOnOrigin(response: Response, expectedOrigin: string): boolean {
  if (response.redirected) return false;
  if (!response.url) return true;
  try {
    return new URL(response.url).origin === expectedOrigin;
  } catch {
    return false;
  }
}

function constantTimeTextEqual(left: string, right: string): boolean {
  const leftBuffer = Buffer.from(left, "utf8");
  const rightBuffer = Buffer.from(right, "utf8");
  return leftBuffer.length === rightBuffer.length && timingSafeEqual(leftBuffer, rightBuffer);
}

async function runtimeModeErrorText(response: Response): Promise<string> {
  try {
    const data = await response.clone().json() as Record<string, unknown>;
    const detail = stringValue(data.detail) ?? stringValue(data.message) ?? stringValue(data.error);
    return detail ? `(${detail})` : response.statusText;
  } catch {
    const text = await response.text().catch(() => "");
    return text.trim() || response.statusText;
  }
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}
