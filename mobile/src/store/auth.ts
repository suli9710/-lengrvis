import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";

import {
  describeBaseUrlSecurity,
  mergeBaseUrlSecurityMetadata,
  normalizePairingSecurityMetadata,
  type PairingSecurityMetadata,
  type PairingSession,
} from "../api/client";

const SESSION_KEY = "lengrvis.mobile.session";
const TOKEN_KEY = "lengrvis.mobile.session.token";

type StoredSessionMetadata = Partial<Omit<PairingSession, "token">> & {
  token?: string;
};

export async function loadSession(): Promise<PairingSession | null> {
  const raw = await AsyncStorage.getItem(SESSION_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as StoredSessionMetadata;
    if (!parsed.baseUrl || !parsed.deviceId) return null;
    const baseUrlSecurity = restoredBaseUrlSecurity(parsed);
    const session = {
      baseUrl: baseUrlSecurity.normalizedBaseUrl,
      baseUrlSecurity,
      deviceId: parsed.deviceId,
      ...(parsed.server ? { server: parsed.server } : {}),
      ...(baseUrlSecurity.backendSecurity ? { security: baseUrlSecurity.backendSecurity } : {}),
      token: "",
    };

    let token = await SecureStore.getItemAsync(TOKEN_KEY);
    if (!token && parsed.token) {
      token = parsed.token;
      await saveSession({ ...session, token });
    } else if (token) {
      await saveSession({ ...session, token });
    }
    return token ? { ...session, token } : null;
  } catch {
    return null;
  }
}

export async function saveSession(session: PairingSession): Promise<void> {
  const sessionSecurity = session.security ?? session.baseUrlSecurity?.backendSecurity;
  const baseUrlSecurity = mergeBaseUrlSecurityMetadata(
    describeBaseUrlSecurity(session.baseUrl),
    normalizePairingSecurityMetadata(sessionSecurity, session.baseUrlSecurity),
  );
  const metadata = {
    baseUrl: baseUrlSecurity.normalizedBaseUrl,
    baseUrlSecurity,
    deviceId: session.deviceId,
    ...(session.server ? { server: session.server } : {}),
    ...(baseUrlSecurity.backendSecurity ? { security: baseUrlSecurity.backendSecurity } : {}),
  };
  await SecureStore.setItemAsync(TOKEN_KEY, session.token);
  await AsyncStorage.setItem(SESSION_KEY, JSON.stringify(metadata));
}

export async function clearSession(): Promise<void> {
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
