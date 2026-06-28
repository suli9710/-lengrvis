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
import { clearNativeTlsTrust, configureNativeTlsTrust } from "../api/client/nativeTlsTrust";

const SESSION_KEY = "lengrvis.mobile.session";
const TOKEN_KEY = "lengrvis.mobile.session.token";
const LEGACY_ASYNC_STORAGE_KEYS = [TOKEN_KEY] as const;
const SESSION_RECOVERY_ERROR_MESSAGE = "手机没有读到可用的本地会话。";
const memoryAsyncStorage = new Map<string, string>();
const memorySecureStore = new Map<string, string>();

type StoredSessionMetadata = Partial<Omit<PairingSession, "token">> & {
  token?: string;
};

export async function loadSession(): Promise<PairingSession | null> {
  try {
    const raw = await asyncStorageGetItem(SESSION_KEY);
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
      ...(parsed.deviceTrust ? { deviceTrust: parsed.deviceTrust } : {}),
      ...(parsed.expiresAt ? { expiresAt: parsed.expiresAt } : {}),
      ...(parsed.server ? { server: parsed.server } : {}),
      ...(baseUrlSecurity.backendSecurity ? { security: baseUrlSecurity.backendSecurity } : {}),
      token: "",
    };

    let token = await secureStoreGetItem(TOKEN_KEY);
    const migratedLegacyEmbeddedToken = !token && Boolean(parsed.token);
    if (!token && parsed.token) {
      token = parsed.token;
    }
    if (!token) {
      await clearStoredSessionStrict();
      return null;
    }
    try {
      const safeSession = assertSafePairingSession({ ...session, token });
      await configureNativeTlsTrust(safeSession.baseUrlSecurity);
      await saveSession(safeSession);
      if (migratedLegacyEmbeddedToken) {
        await eraseLegacyAsyncStorageSecrets();
      }
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
  await configureNativeTlsTrust(baseUrlSecurity);
  const metadata = {
    baseUrl: baseUrlSecurity.normalizedBaseUrl,
    baseUrlSecurity,
    deviceId: safeSession.deviceId,
    ...(safeSession.deviceTrust ? { deviceTrust: safeSession.deviceTrust } : {}),
    ...(safeSession.expiresAt ? { expiresAt: safeSession.expiresAt } : {}),
    ...(safeSession.server ? { server: safeSession.server } : {}),
    ...(baseUrlSecurity.backendSecurity ? { security: baseUrlSecurity.backendSecurity } : {}),
  };
  try {
    await secureStoreSetItem(TOKEN_KEY, safeSession.token);
    await asyncStorageSetItem(SESSION_KEY, JSON.stringify(metadata));
    await eraseLegacyAsyncStorageSecrets();
  } catch (error) {
    await clearSessionQuietly();
    throw error;
  }
}

export async function clearSession(): Promise<void> {
  clearRemoteInputGrantTokens();
  await Promise.all([
    clearNativeTlsTrust(),
    asyncStorageRemoveItem(SESSION_KEY),
    secureStoreDeleteItem(TOKEN_KEY),
    ...LEGACY_ASYNC_STORAGE_KEYS.map((key) => asyncStorageRemoveItem(key)),
  ]);
}

async function eraseLegacyAsyncStorageSecrets(): Promise<void> {
  await Promise.all(LEGACY_ASYNC_STORAGE_KEYS.map((key) => asyncStorageRemoveItem(key)));
}

async function asyncStorageGetItem(key: string): Promise<string | null> {
  try {
    return await AsyncStorage.getItem(key);
  } catch (error) {
    if (isStorageBackendUnavailable(error)) {
      return memoryAsyncStorage.get(key) ?? null;
    }
    throw error;
  }
}

async function asyncStorageSetItem(key: string, value: string): Promise<void> {
  try {
    await AsyncStorage.setItem(key, value);
  } catch (error) {
    if (isStorageBackendUnavailable(error)) {
      memoryAsyncStorage.set(key, value);
      return;
    }
    throw error;
  }
}

async function asyncStorageRemoveItem(key: string): Promise<void> {
  try {
    await AsyncStorage.removeItem(key);
  } catch (error) {
    if (isStorageBackendUnavailable(error)) {
      memoryAsyncStorage.delete(key);
      return;
    }
    throw error;
  }
}

async function secureStoreGetItem(key: string): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(key);
  } catch (error) {
    if (isStorageBackendUnavailable(error)) {
      return memorySecureStore.get(key) ?? null;
    }
    throw error;
  }
}

async function secureStoreSetItem(key: string, value: string): Promise<void> {
  try {
    await SecureStore.setItemAsync(key, value);
  } catch (error) {
    if (isStorageBackendUnavailable(error)) {
      memorySecureStore.set(key, value);
      return;
    }
    throw error;
  }
}

async function secureStoreDeleteItem(key: string): Promise<void> {
  try {
    await SecureStore.deleteItemAsync(key);
  } catch (error) {
    if (isStorageBackendUnavailable(error)) {
      memorySecureStore.delete(key);
      return;
    }
    throw error;
  }
}

function isStorageBackendUnavailable(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /Native\s*module|NativeModule|NativeModuleError|RNCAsyncStorage|AsyncStorage.*null|Expo(?:nent)?SecureStore|SecureStore.*unavailable|Cannot find native module|TurboModuleRegistry.*not found|module.*not found|not available|not supported/i.test(message);
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
