import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";

import {
  AuthExpiredError,
  InsecureLanBaseUrlError,
  assertSafePairingSession,
  describeBaseUrlSecurity,
  mergeBaseUrlSecurityMetadata,
  normalizePairingSecurityMetadata,
  clearRemoteInputGrantTokens,
  type PairingSecurityMetadata,
  type PairingSession,
} from "../api/client";

const SESSION_KEY = "lengrvis.mobile.session";
const TOKEN_KEY = "lengrvis.mobile.session.token";
const SESSION_RECOVERY_ERROR_MESSAGE = "手机没有读到可用的本地会话。";

type StoredSessionMetadata = Partial<Omit<PairingSession, "token">> & {
  token?: string;
};

export async function loadSession(): Promise<PairingSession | null> {
  try {
    const raw = await AsyncStorage.getItem(SESSION_KEY);
    if (!raw) {
      await clearStoredSessionStrict();
      return null;
    }
    const parsed = JSON.parse(raw) as StoredSessionMetadata;
    if (!parsed.baseUrl || !parsed.deviceId) {
      await clearStoredSessionStrict();
      return null;
    }
    const baseUrlSecurity = restoredBaseUrlSecurity(parsed);
    if (baseUrlSecurity.isInsecureLan) {
      await clearStoredSessionStrict();
      return null;
    }
    const session = {
      baseUrl: baseUrlSecurity.normalizedBaseUrl,
      baseUrlSecurity,
      deviceId: parsed.deviceId,
      ...(parsed.expiresAt ? { expiresAt: parsed.expiresAt } : {}),
      ...(parsed.server ? { server: parsed.server } : {}),
      ...(baseUrlSecurity.backendSecurity ? { security: baseUrlSecurity.backendSecurity } : {}),
      token: "",
    };

    let token = await SecureStore.getItemAsync(TOKEN_KEY);
    if (!token && parsed.token) {
      token = parsed.token;
    }
    if (!token) {
      await clearStoredSessionStrict();
      return null;
    }
    try {
      const safeSession = assertSafePairingSession({ ...session, token });
      await saveSession(safeSession);
      return safeSession;
    } catch (error) {
      if (error instanceof AuthExpiredError || error instanceof InsecureLanBaseUrlError) {
        await clearStoredSessionStrict();
        return null;
      }
      throw error;
    }
  } catch (error) {
    if (error instanceof SessionRecoveryError) {
      throw error;
    }
    await clearStoredSessionStrict();
    return null;
  }
}

export async function saveSession(session: PairingSession): Promise<void> {
  const safeSession = assertSafePairingSession(session);
  const sessionSecurity = safeSession.security ?? safeSession.baseUrlSecurity?.backendSecurity;
  const baseUrlSecurity = mergeBaseUrlSecurityMetadata(
    describeBaseUrlSecurity(safeSession.baseUrl),
    normalizePairingSecurityMetadata(sessionSecurity, safeSession.baseUrlSecurity),
  );
  if (baseUrlSecurity.isInsecureLan) {
    throw new InsecureLanBaseUrlError(baseUrlSecurity);
  }
  const metadata = {
    baseUrl: baseUrlSecurity.normalizedBaseUrl,
    baseUrlSecurity,
    deviceId: safeSession.deviceId,
    ...(safeSession.expiresAt ? { expiresAt: safeSession.expiresAt } : {}),
    ...(safeSession.server ? { server: safeSession.server } : {}),
    ...(baseUrlSecurity.backendSecurity ? { security: baseUrlSecurity.backendSecurity } : {}),
  };
  try {
    await SecureStore.setItemAsync(TOKEN_KEY, safeSession.token);
    await AsyncStorage.setItem(SESSION_KEY, JSON.stringify(metadata));
  } catch (error) {
    await clearSessionQuietly();
    throw error;
  }
}

export async function clearSession(): Promise<void> {
  clearRemoteInputGrantTokens();
  await Promise.all([
    AsyncStorage.removeItem(SESSION_KEY),
    SecureStore.deleteItemAsync(TOKEN_KEY),
  ]);
}

function restoredBaseUrlSecurity(parsed: StoredSessionMetadata) {
  const baseUrlSecurity = describeBaseUrlSecurity(parsed.baseUrl ?? "");
  return mergeBaseUrlSecurityMetadata(baseUrlSecurity, storedSecurityMetadata(parsed, baseUrlSecurity));
}

function storedSecurityMetadata(parsed: StoredSessionMetadata, baseUrlSecurity: ReturnType<typeof describeBaseUrlSecurity>): PairingSecurityMetadata | undefined {
  return normalizePairingSecurityMetadata(
    parsed.security ??
      parsed.baseUrlSecurity?.backendSecurity ??
      (parsed.baseUrlSecurity?.serverTls ? { tls: parsed.baseUrlSecurity.serverTls } : undefined),
    baseUrlSecurity,
  );
}

async function clearSessionQuietly(): Promise<void> {
  try {
    await clearSession();
  } catch {
    // Loading a session should fail closed even if one storage backend is unavailable.
  }
}

async function clearStoredSessionStrict(): Promise<void> {
  try {
    await clearSession();
  } catch (error) {
    throw new SessionRecoveryError(error);
  }
}

class SessionRecoveryError extends Error {
  constructor(cause?: unknown) {
    super(SESSION_RECOVERY_ERROR_MESSAGE);
    this.name = "SessionRecoveryError";
    this.cause = cause;
  }
}
