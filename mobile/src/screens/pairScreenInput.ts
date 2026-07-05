import { MAX_BASE_URL_LENGTH, MAX_DEVICE_NAME_LENGTH, MAX_PAIRING_CODE_LENGTH, MAX_PAIRING_PAYLOAD_LENGTH } from "./pairScreenConstants";
import { baseUrlInputCleanedNotice, pairingInputTooLongNotice } from "./pairScreenNotices";
import type { PairingFailureNotice } from "./pairScreenTypes";

export interface ProtectedPairingPayloadInput {
  value: string;
  wasTruncated: boolean;
}

export function protectPairingPayloadInput(value: string): ProtectedPairingPayloadInput {
  const withoutUnsafeCharacters = value.replace(/[\u0000-\u001f\u007f]+/g, " ");
  return {
    value: withoutUnsafeCharacters.slice(0, MAX_PAIRING_PAYLOAD_LENGTH),
    wasTruncated: withoutUnsafeCharacters.length > MAX_PAIRING_PAYLOAD_LENGTH,
  };
}

export function protectBaseUrlInput(value: string): { value: string; notice: PairingFailureNotice | null } {
  const withoutUnsafeCharacters = value.replace(/[\u0000-\u001f\u007f\s]+/g, "");
  const nextValue = withoutUnsafeCharacters.slice(0, MAX_BASE_URL_LENGTH);
  if (nextValue === value) return { value: nextValue, notice: null };
  return {
    value: nextValue,
    notice: withoutUnsafeCharacters.length > MAX_BASE_URL_LENGTH ? pairingInputTooLongNotice("baseUrl") : baseUrlInputCleanedNotice(),
  };
}

export function normalizePairingCodeInput(value: string): string {
  return value.replace(/[^a-z0-9]/gi, "").toLowerCase().slice(0, MAX_PAIRING_CODE_LENGTH);
}

export function isPairingBaseUrlInputReady(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed) return false;
  try {
    const withProtocol = /^[a-z][a-z\d+\-.]*:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`;
    const parsed = new URL(withProtocol);
    return Boolean(parsed.hostname && (parsed.protocol === "http:" || parsed.protocol === "https:"));
  } catch {
    return false;
  }
}

export function safeDeviceName(value: string | null): string {
  const normalized = value?.replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim().slice(0, MAX_DEVICE_NAME_LENGTH);
  return normalized || "安卓设备";
}
