import type { BaseUrlSecurity } from "./types";

declare const require: ((id: string) => unknown) | undefined;

type NativeTlsTrustModule = {
  trustServerCertificate?: (baseUrl: string, fingerprintSha256: string) => Promise<unknown>;
  clearTrustedServers?: () => Promise<unknown>;
};

type ReactNativeRuntime = {
  NativeModules?: {
    LengrvisLanTrust?: NativeTlsTrustModule;
  };
  Platform?: {
    OS?: string;
  };
};

export class TlsTrustConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TlsTrustConfigurationError";
  }
}

export async function configureNativeTlsTrust(security: BaseUrlSecurity): Promise<void> {
  if (!security.requiresTlsTrust) return;
  if (!isAndroidRuntime()) return;
  const fingerprint = security.serverTls?.fingerprintSha256?.trim();
  if (!fingerprint) {
    throw new TlsTrustConfigurationError("LAN HTTPS requires a certificate SHA-256 fingerprint before Android can pair.");
  }
  const module = nativeTlsTrustModule();
  if (!module?.trustServerCertificate) {
    throw new TlsTrustConfigurationError("Android LAN TLS trust module is unavailable.");
  }
  await module.trustServerCertificate(security.normalizedBaseUrl, fingerprint);
}

export async function clearNativeTlsTrust(): Promise<void> {
  if (!isAndroidRuntime()) return;
  await nativeTlsTrustModule()?.clearTrustedServers?.();
}

function isAndroidRuntime(): boolean {
  return reactNativeRuntime()?.Platform?.OS === "android";
}

function nativeTlsTrustModule(): NativeTlsTrustModule | undefined {
  return reactNativeRuntime()?.NativeModules?.LengrvisLanTrust;
}

function reactNativeRuntime(): ReactNativeRuntime | undefined {
  if (typeof require !== "function") return undefined;
  try {
    return require("react-native") as ReactNativeRuntime;
  } catch {
    return undefined;
  }
}
