import { dirname, join } from "node:path";

interface PackagedBackendEnvironmentOptions {
  activationBaseUrl: string;
  licensePublicKey: string;
}

export function hardenPackagedProcessEnvironment(defaultBackendUrl: string): void {
  process.env.LENGRVIS_ENV = "production";
  process.env.LENGRVIS_TEST = "0";
  process.env.LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL = "false";
  process.env.LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS = "false";
  process.env.LENGRVIS_STRICT_STATE_MACHINE = "true";
  process.env.LENGRVIS_BACKEND_URL = defaultBackendUrl;
  process.env.LENGRVIS_BACKEND_HOST = "127.0.0.1";
  process.env.LENGRVIS_BACKEND_PORT = "8000";
  process.env.LENGRVIS_BACKEND_SERVICE_DISABLED = "1";
  process.env.LENGRVIS_LAN_TLS_ENABLED = "false";
  process.env.LENGRVIS_LAN_TLS_AUTO = "false";
  process.env.LENGRVIS_ALLOW_LAN_DESKTOP_API = "false";
  for (const name of [
    "PYTEST_CURRENT_TEST",
    "LENGRVIS_DESKTOP_API_TOKEN",
    "LENGRVIS_CONFIG_DIR",
    "LENGRVIS_DATA_DIR",
    "LENGRVIS_CONFIG_FILE",
    "LENGRVIS_ENV_FILE",
    "LENGRVIS_BACKEND_COMMAND",
    "LENGRVIS_BACKEND_ARGS",
    "LENGRVIS_BACKEND_CWD",
    "LENGRVIS_NATIVE_CONFIRMATION_SECRET",
    "LENGRVIS_APPROVAL_HMAC_SECRET",
    "LENGRVIS_AUDIT_HMAC_SECRET",
    "LENGRVIS_AUDIT_HMAC_SECRET_FILE",
    "LENGRVIS_JWT_SECRET",
    "LENGRVIS_LAN_PUBLIC_BASE_URL",
    "LENGRVIS_LAN_TLS_CERT_FILE",
    "LENGRVIS_LAN_TLS_KEY_FILE",
    "LENGRVIS_TRUSTED_PROXY_IPS",
    "LENGRVIS_TRUSTED_PROXIES"
  ]) {
    delete process.env[name];
  }
}

export function packagedBackendEnvironment(
  options: PackagedBackendEnvironmentOptions
): NodeJS.ProcessEnv {
  return {
    ...forcedEnv("LENGRVIS_ENV", "production"),
    ...forcedEnv("LENGRVIS_TEST", "0"),
    ...forcedEnv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", "false"),
    ...forcedEnv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", "false"),
    ...forcedEnv("LENGRVIS_STRICT_STATE_MACHINE", "true"),
    ...forcedEnv("LENGRVIS_BACKEND_HOST", "127.0.0.1"),
    ...forcedEnv("LENGRVIS_BACKEND_PORT", "8000"),
    ...forcedEnv("LENGRVIS_BACKEND_SERVICE_DISABLED", "1"),
    ...forcedEnv("LENGRVIS_LAN_TLS_ENABLED", "false"),
    ...forcedEnv("LENGRVIS_LAN_TLS_AUTO", "false"),
    ...forcedEnv("LENGRVIS_LAN_PUBLIC_BASE_URL", ""),
    ...forcedEnv("LENGRVIS_LAN_TLS_CERT_FILE", ""),
    ...forcedEnv("LENGRVIS_LAN_TLS_KEY_FILE", ""),
    ...forcedEnv("LENGRVIS_ALLOW_LAN_DESKTOP_API", "false"),
    ...forcedEnv("LENGRVIS_TRUSTED_PROXY_IPS", ""),
    ...forcedEnv("LENGRVIS_TRUSTED_PROXIES", ""),
    ...forcedEnv("PYTEST_CURRENT_TEST", ""),
    ...forcedEnv("LENGRVIS_COMMERCIAL_RELEASE", "true"),
    ...forcedEnv("LENGRVIS_ACTIVATION_BASE_URL", options.activationBaseUrl),
    ...forcedEnv("LENGRVIS_LICENSE_PUBLIC_KEY", options.licensePublicKey)
  };
}

export function packagedBackendConfigDir(command: string | undefined, resourcesPath: string): string {
  return command ? dirname(command) : join(resourcesPath, "backend");
}

export function setResolvedProcessEnv(name: string, value: string, force: boolean): void {
  if (force || process.env[name] === undefined) {
    process.env[name] = value;
  }
}

export function forcedEnv(name: string, value: string): NodeJS.ProcessEnv {
  return { [name]: value };
}
