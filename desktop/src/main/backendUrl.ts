export function assertLoopbackBackendUrl(baseUrl: string, context: string): URL {
  const url = new URL(baseUrl);
  if (!["http:", "https:"].includes(url.protocol)) {
    throw new Error(`${context} backend baseUrl must be HTTP(S)`);
  }
  if (!isLoopbackHostname(url.hostname)) {
    throw new Error(`${context} requires a loopback backend base URL`);
  }
  return url;
}

export function isLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase();
  return normalized === "localhost" || normalized === "::1" || normalized === "[::1]" || /^127(?:\.\d{1,3}){3}$/.test(normalized);
}
