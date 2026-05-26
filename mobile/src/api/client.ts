export interface PairResult {
  token: string;
  token_type: "Bearer";
  device_id: string;
  expires_in: number;
  server: {
    host: string;
    port: number;
  };
}

export interface BackendApproval {
  id: string;
  task_id: string;
  step_id?: string | null;
  approval_type: string;
  message: string;
  diff_preview: unknown;
  status: "pending" | "approved" | "rejected" | "expired";
  created_at: string;
  decided_at?: string | null;
}

export interface BackendTask {
  id: string;
  user_goal: string;
  status: string;
  mode: string;
  final_summary: string;
  created_at: string;
  updated_at: string;
}

export interface BackendPlanStep {
  id: string;
  order: number;
  agent_name: string;
  tool_name: string;
  description: string;
  status: string;
  requires_approval: boolean;
  args?: Record<string, unknown>;
  expected_observation?: string;
}

export interface BackendPlan {
  id: string;
  goal: string;
  assumptions?: string[];
  steps: BackendPlanStep[];
}

export interface ApprovalDetail {
  approval: BackendApproval;
  task: BackendTask | null;
  plan: BackendPlan | null;
  preview: unknown;
}

export type ApprovalEvent =
  | { type: "connected"; device_id?: string; pending: BackendApproval[] }
  | { type: "heartbeat" }
  | { type: "approval_notification"; approval: BackendApproval }
  | { type: "approval_created"; approval: BackendApproval }
  | { type: "approval_decided"; approval: BackendApproval };

export type RemoteScreenEvent =
  | { type: "connected"; fps: number; quality: number }
  | {
      type: "frame";
      image: string;
      timestamp: string;
      width: number;
      height: number;
      original_width: number;
      original_height: number;
    }
  | { type: "error"; message: string };

export interface PairingSession {
  baseUrl: string;
  token: string;
  deviceId: string;
}

export class AuthExpiredError extends Error {
  constructor(message = "Session expired. Pair this device again.") {
    super(message);
    this.name = "AuthExpiredError";
  }
}

export async function pairWithBackend(baseUrl: string, code: string, deviceName: string): Promise<PairingSession> {
  const normalizedBaseUrl = normalizeBaseUrl(baseUrl);
  const response = await fetch(`${normalizedBaseUrl}/api/pair/confirm`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ code, device_name: deviceName }),
  });
  const payload = await parseJson<PairResult>(response);
  return {
    baseUrl: normalizedBaseUrl,
    token: payload.token,
    deviceId: payload.device_id,
  };
}

export async function listPendingApprovals(session: PairingSession): Promise<BackendApproval[]> {
  const response = await fetch(`${session.baseUrl}/api/mobile/approvals/pending`, {
    headers: authHeaders(session.token),
  });
  return parseJson<BackendApproval[]>(response);
}

export async function getApprovalDetail(session: PairingSession, approvalId: string): Promise<ApprovalDetail> {
  const response = await fetch(`${session.baseUrl}/api/mobile/approvals/${encodeURIComponent(approvalId)}`, {
    headers: authHeaders(session.token),
  });
  return parseJson<ApprovalDetail>(response);
}

export async function submitApprovalDecision(
  session: PairingSession,
  approvalId: string,
  decision: "approved" | "denied",
): Promise<BackendApproval> {
  const action = decision === "approved" ? "approve" : "reject";
  const response = await fetch(`${session.baseUrl}/api/mobile/approvals/${encodeURIComponent(approvalId)}/${action}`, {
    method: "POST",
    headers: authHeaders(session.token),
  });
  return parseJson<BackendApproval>(response);
}

export function approvalWebSocketUrl(session: PairingSession): string {
  const url = new URL("/ws/mobile/approvals", session.baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("token", session.token);
  return url.toString();
}

export function remoteScreenWebSocketUrl(session: PairingSession): string {
  const url = new URL("/ws/remote/screen", session.baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("token", session.token);
  return url.toString();
}

export function normalizeBaseUrl(value: string): string {
  const trimmed = value.trim().replace(/\/+$/, "");
  if (!trimmed) throw new Error("Enter the LAN address shown by Mavris desktop.");
  return /^https?:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`;
}

export function isLoopbackBaseUrl(value: string): boolean {
  try {
    const parsed = new URL(normalizeBaseUrl(value));
    const host = parsed.hostname.toLowerCase();
    return host === "localhost" || host === "127.0.0.1" || host === "::1";
  } catch {
    return false;
  }
}

async function parseJson<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => undefined);
  if (!response.ok) {
    const detail = data && typeof data === "object" && "detail" in data ? String((data as { detail?: unknown }).detail) : "";
    if (response.status === 401 || response.status === 403) {
      throw new AuthExpiredError(detail || undefined);
    }
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return data as T;
}

function authHeaders(token: string): HeadersInit {
  return {
    Accept: "application/json",
    Authorization: `Bearer ${token}`,
  };
}
