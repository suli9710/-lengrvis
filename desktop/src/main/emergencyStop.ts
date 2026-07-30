export const GLOBAL_EMERGENCY_STOP_SHORTCUT = "CommandOrControl+Alt+Shift+Escape";

export interface EmergencyStopBrowserHost {
  getSnapshot: () => { sessions: Array<{ id: string }> };
  stop: (sessionId: string) => Promise<{ ok: boolean; error?: string }>;
}

export interface EmergencyStopBackend {
  emergencyStop: () => Promise<{ ok: boolean; failed_tasks?: unknown[]; [key: string]: unknown }>;
}

export interface EmergencyStopResult {
  ok: boolean;
  browser_sessions_stopped: number;
  browser_session_failures: number;
  backend: { ok: boolean; [key: string]: unknown } | null;
  error?: string;
}

/**
 * Stop local browser sessions immediately, then cancel backend task owners.
 * The two controls are deliberately independent: a backend outage must not
 * prevent the local browser surface from becoming inert.
 */
export async function emergencyStopAgentWork(
  browserHost: EmergencyStopBrowserHost,
  backend: EmergencyStopBackend
): Promise<EmergencyStopResult> {
  const sessionIds = browserHost.getSnapshot().sessions.map((session) => session.id).filter(Boolean);
  const browserResults = await Promise.allSettled(sessionIds.map((sessionId) => browserHost.stop(sessionId)));
  const browserFailures = browserResults.filter(
    (result) => result.status === "rejected" || (result.status === "fulfilled" && !result.value.ok)
  ).length;

  let backendResult: { ok: boolean; [key: string]: unknown } | null = null;
  let backendError = "";
  try {
    backendResult = await backend.emergencyStop();
    if (!backendResult.ok) backendError = "Backend emergency stop reported incomplete cancellation";
  } catch {
    backendError = "Backend emergency stop was unavailable";
  }

  const ok = browserFailures === 0 && Boolean(backendResult?.ok) && !backendError;
  return {
    ok,
    browser_sessions_stopped: sessionIds.length - browserFailures,
    browser_session_failures: browserFailures,
    backend: backendResult,
    ...(backendError ? { error: backendError } : {})
  };
}
