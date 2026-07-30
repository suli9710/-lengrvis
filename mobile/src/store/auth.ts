import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";

import {
  AuthExpiredError,
  InsecureLanBaseUrlError,
  assertSafePairingSession,
  assertSafeRefreshablePairingSession,
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
const REFRESH_TOKEN_KEY = "lengrvis.mobile.session.refresh-token";
const LEGACY_ASYNC_STORAGE_KEYS = [TOKEN_KEY, REFRESH_TOKEN_KEY] as const;
const SESSION_RECOVERY_ERROR_MESSAGE = "手机没有读到可用的本地会话。";
const TOKEN_SECURE_STORE_OPTIONS: SecureStore.SecureStoreOptions = {
  requireAuthentication: true,
  authenticationPrompt: "验证身份以访问 Lengrvis 配对会话",
};
const memoryAsyncStorage = new Map<string, string>();
const memorySecureStore = new Map<string, string>();
let sessionStorageTail: Promise<void> = Promise.resolve();

type StoredSessionMetadata = Partial<Omit<PairingSession, "token" | "refreshToken">> & {
  token?: string;
  refreshToken?: string;
};

export async function loadSession(): Promise<PairingSession | null> {
  return serializeSessionStorage(loadSessionUnlocked);
}

async function loadSessionUnlocked(): Promise<PairingSession | null> {
  try {
    const raw = await asyncStorageGetItem(SESSION_KEY);
    if (!raw) {
      await clearStoredSessionStrict();
      return null;
    }
    const parsed = JSON.parse(raw) as StoredSessionMetadata;
    if (!parsed.baseUrl || !parsed.deviceId || !parsed.tokenFamilyId || !parsed.deviceCredentialId) {
      await clearStoredSessionStrict();
      return null;
    }
    const baseUrlSecurity = restoredBaseUrlSecurity(parsed);
    if (baseUrlSecurity.isInsecureLan || baseUrlSecurity.isLoopback) {
      await clearStoredSessionStrict();
      return null;
    }
    const session = {
      baseUrl: baseUrlSecurity.normalizedBaseUrl,
      baseUrlSecurity,
      deviceId: parsed.deviceId,
      ...(parsed.deviceTrust ? { deviceTrust: parsed.deviceTrust } : {}),
      ...(parsed.expiresAt ? { expiresAt: parsed.expiresAt } : {}),
      ...(parsed.refreshExpiresAt ? { refreshExpiresAt: parsed.refreshExpiresAt } : {}),
      tokenFamilyId: parsed.tokenFamilyId ?? "",
      deviceCredentialId: parsed.deviceCredentialId ?? "",
      ...(parsed.server ? { server: parsed.server } : {}),
      ...(baseUrlSecurity.backendSecurity ? { security: baseUrlSecurity.backendSecurity } : {}),
      token: "",
      refreshToken: "",
    };

    let token = await secureStoreGetItem(TOKEN_KEY);
    let refreshToken = await secureStoreGetItem(REFRESH_TOKEN_KEY);
    const migratedLegacyEmbeddedToken = !token && Boolean(parsed.token);
    const migratedLegacyEmbeddedRefreshToken = !refreshToken && Boolean(parsed.refreshToken);
    if (!token && parsed.token) {
      token = parsed.token;
    }
    if (!refreshToken && parsed.refreshToken) {
      refreshToken = parsed.refreshToken;
    }
    if (!token || !refreshToken) {
      await clearStoredSessionStrict();
      return null;
    }
    try {
      const candidateSession = { ...session, token, refreshToken };
      let safeSession: PairingSession;
      try {
        safeSession = assertSafePairingSession(candidateSession);
      } catch (error) { // broad-exception-boundary
        if (!(error instanceof AuthExpiredError)) throw error;
        safeSession = assertSafeRefreshablePairingSession(candidateSession);
      }
      safeSession = assertSafeRefreshablePairingSession(safeSession);
      await configureNativeTlsTrust(safeSession.baseUrlSecurity);
      if (migratedLegacyEmbeddedToken || migratedLegacyEmbeddedRefreshToken) {
        await secureStoreSetItem(TOKEN_KEY, safeSession.token);
        await secureStoreSetItem(REFRESH_TOKEN_KEY, safeSession.refreshToken);
        const sanitizedMetadata = { ...parsed };
        delete sanitizedMetadata.token;
        delete sanitizedMetadata.refreshToken;
        await asyncStorageSetItem(SESSION_KEY, JSON.stringify(sanitizedMetadata));
        await eraseLegacyAsyncStorageSecrets();
      }
      return safeSession;
    } catch (error) { // broad-exception-boundary
      if (error instanceof AuthExpiredError || error instanceof InsecureLanBaseUrlError) {
        await clearStoredSessionStrict();
        return null;
      }
      throw error;
    }
  } catch (error) { // broad-exception-boundary
    if (error instanceof SessionRecoveryError) {
      throw error;
    }
    await clearStoredSessionStrict();
    return null;
  }
}

export async function saveSession(session: PairingSession): Promise<void> {
  return serializeSessionStorage(() => saveSessionUnlocked(session));
}

async function saveSessionUnlocked(session: PairingSession): Promise<void> {
  const safeSession = assertSafeRefreshablePairingSession(assertSafePairingSession(session));
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
    tokenFamilyId: safeSession.tokenFamilyId,
    deviceCredentialId: safeSession.deviceCredentialId,
    ...(safeSession.deviceTrust ? { deviceTrust: safeSession.deviceTrust } : {}),
    ...(safeSession.expiresAt ? { expiresAt: safeSession.expiresAt } : {}),
    ...(safeSession.refreshExpiresAt ? { refreshExpiresAt: safeSession.refreshExpiresAt } : {}),
    ...(safeSession.server ? { server: safeSession.server } : {}),
    ...(baseUrlSecurity.backendSecurity ? { security: baseUrlSecurity.backendSecurity } : {}),
  };
  try {
    await secureStoreSetItem(TOKEN_KEY, safeSession.token);
    await secureStoreSetItem(REFRESH_TOKEN_KEY, safeSession.refreshToken);
    await asyncStorageSetItem(SESSION_KEY, JSON.stringify(metadata));
    await eraseLegacyAsyncStorageSecrets();
  } catch (error) { // broad-exception-boundary
    await clearSessionQuietly();
    throw error;
  }
}

export async function clearSession(): Promise<void> {
  return serializeSessionStorage(clearSessionUnlocked);
}

async function clearSessionUnlocked(): Promise<void> {
  clearRemoteInputGrantTokens();
  await Promise.all([
    clearNativeTlsTrust(),
    asyncStorageRemoveItem(SESSION_KEY),
    secureStoreDeleteItem(TOKEN_KEY),
    secureStoreDeleteItem(REFRESH_TOKEN_KEY),
    ...LEGACY_ASYNC_STORAGE_KEYS.map((key) => asyncStorageRemoveItem(key)),
  ]);
}

/**
 * Persist a refreshed token only while the token it replaces is still current.
 * All session mutations share the same queue, so a late network response cannot
 * overwrite a newer pairing or a user-requested clear operation.
 */
export async function replaceSessionIfTokenMatches(
  expectedToken: string,
  nextSession: PairingSession,
): Promise<boolean> {
  if (!expectedToken) return false;
  return serializeSessionStorage(async () => {
    const currentToken = await secureStoreGetItem(TOKEN_KEY);
    if (currentToken !== expectedToken) return false;
    await saveSessionUnlocked(nextSession);
    return true;
  });
}

function serializeSessionStorage<T>(operation: () => Promise<T>): Promise<T> {
  const result = sessionStorageTail.then(operation, operation);
  sessionStorageTail = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

async function eraseLegacyAsyncStorageSecrets(): Promise<void> {
  await Promise.all(LEGACY_ASYNC_STORAGE_KEYS.map((key) => asyncStorageRemoveItem(key)));
}

async function asyncStorageGetItem(key: string): Promise<string | null> {
  try {
    return await AsyncStorage.getItem(key);
  } catch (error) { // broad-exception-boundary
    if (isStorageBackendUnavailable(error)) {
      return memoryAsyncStorage.get(key) ?? null;
    }
    throw error;
  }
}

async function asyncStorageSetItem(key: string, value: string): Promise<void> {
  try {
    await AsyncStorage.setItem(key, value);
  } catch (error) { // broad-exception-boundary
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
  } catch (error) { // broad-exception-boundary
    if (isStorageBackendUnavailable(error)) {
      memoryAsyncStorage.delete(key);
      return;
    }
    throw error;
  }
}

async function secureStoreGetItem(key: string): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(key, TOKEN_SECURE_STORE_OPTIONS);
  } catch (error) { // broad-exception-boundary
    if (isStorageBackendUnavailable(error)) {
      return memorySecureStore.get(key) ?? null;
    }
    throw error;
  }
}

async function secureStoreSetItem(key: string, value: string): Promise<void> {
  try {
    await SecureStore.setItemAsync(key, value, TOKEN_SECURE_STORE_OPTIONS);
  } catch (error) { // broad-exception-boundary
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
  } catch (error) { // broad-exception-boundary
    if (isStorageBackendUnavailable(error)) {
      memorySecureStore.delete(key);
      return;
    }
    throw error;
  }
}

function isStorageBackendUnavailable(error: unknown): boolean {
  // Only fall back to the in-memory store when the NATIVE storage module is
  // genuinely absent (dev / Expo Go / a build without the module linked). A
  // broad match (e.g. "not available"/"not supported"/"module not found") would
  // also swallow real failures such as a locked keychain or an encryption error
  // and silently downgrade token storage to process memory — bypassing the
  // biometric/`requireAuthentication` protection and masking the fault. Those
  // errors must propagate instead.
  const message = error instanceof Error ? error.message : String(error);
  return /Cannot find native module|Native module ['"]?\w+['"]? is null|NativeModule[\w.]* is null|(?:RNCAsyncStorage|Expo(?:nent)?SecureStore)[\w.]* is null|TurboModuleRegistry[\s\S]*(?:could not be found|not found|is not registered)/i.test(
    message
  );
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
    await clearSessionUnlocked();
  } catch {
    // Loading a session should fail closed even if one storage backend is unavailable.
  }
}

async function clearStoredSessionStrict(): Promise<void> {
  try {
    await clearSessionUnlocked();
  } catch (error) { // broad-exception-boundary
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
