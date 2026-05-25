import AsyncStorage from "@react-native-async-storage/async-storage";

import type { PairingSession } from "../api/client";

const SESSION_KEY = "mavris.mobile.session";

export async function loadSession(): Promise<PairingSession | null> {
  const raw = await AsyncStorage.getItem(SESSION_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as PairingSession;
    return parsed.baseUrl && parsed.token ? parsed : null;
  } catch {
    return null;
  }
}

export async function saveSession(session: PairingSession): Promise<void> {
  await AsyncStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

export async function clearSession(): Promise<void> {
  await AsyncStorage.removeItem(SESSION_KEY);
}
