import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";

import type { PairingSession } from "../api/client";

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

    let token = await SecureStore.getItemAsync(TOKEN_KEY);
    if (!token && parsed.token) {
      token = parsed.token;
      await saveSession({ baseUrl: parsed.baseUrl, deviceId: parsed.deviceId, token });
    } else if (token) {
      await saveSession({ baseUrl: parsed.baseUrl, deviceId: parsed.deviceId, token });
    }
    return token ? { baseUrl: parsed.baseUrl, deviceId: parsed.deviceId, token } : null;
  } catch {
    return null;
  }
}

export async function saveSession(session: PairingSession): Promise<void> {
  const metadata = {
    baseUrl: session.baseUrl,
    deviceId: session.deviceId,
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
