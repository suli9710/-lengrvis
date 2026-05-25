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

export type ApprovalEvent =
  | { type: "connected"; device_id?: string; pending: BackendApproval[] }
  | { type: "heartbeat" }
  | { type: "approval_created"; approval: BackendApproval }
  | { type: "approval_decided"; approval: BackendApproval };

export interface PairingSession {
  baseUrl: string;
  token: string;
  deviceId: string;
}

export async function pairWithBackend(baseUrl: string, code: string, deviceName: string): Promise<PairingSession> {
  const response = await fetch(`${normalizeBaseUrl(baseUrl)}/api/pair`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ code, device_name: deviceName }),
  });
  const payload = await parseJson<PairResult>(response);
  return {
    baseUrl: normalizeBaseUrl(baseUrl),
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

export async function submitApprovalDecision(
  session: PairingSession,
  approvalId: string,
  decision: "approved" | "denied",
): Promise<BackendApproval> {
  const response = await fetch(`${session.baseUrl}/api/mobile/approvals/${encodeURIComponent(approvalId)}/decision`, {
    method: "POST",
    headers: {
      ...authHeaders(session.token),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ decision }),
  });
  return parseJson<BackendApproval>(response);
}

export function approvalWebSocketUrl(session: PairingSession): string {
  const url = new URL("/ws/mobile/approvals", session.baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("token", session.token);
  return url.toString();
}

export function normalizeBaseUrl(value: string): string {
  const trimmed = value.trim().replace(/\/+$/, "");
  if (!trimmed) return "http://127.0.0.1:8000";
  return /^https?:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`;
}

async function parseJson<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => undefined);
  if (!response.ok) {
    const detail = data && typeof data === "object" && "detail" in data ? String((data as { detail?: unknown }).detail) : "";
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
