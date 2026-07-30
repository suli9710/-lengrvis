import { net, protocol } from "electron";
import { existsSync, realpathSync, statSync } from "node:fs";
import { extname, isAbsolute, relative, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";

export const PACKAGED_RENDERER_SCHEME = "app";
export const PACKAGED_RENDERER_HOST = "local";
export const PACKAGED_RENDERER_ENTRY_URL = `${PACKAGED_RENDERER_SCHEME}://${PACKAGED_RENDERER_HOST}/index.html`;

const RENDERER_CSP = [
  "default-src 'self'",
  "base-uri 'none'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "form-action 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "style-src-elem 'self'",
  "style-src-attr 'unsafe-inline'",
  "img-src 'self' data: http://127.0.0.1:* http://localhost:*",
  "connect-src 'self' http://127.0.0.1:* http://localhost:* ws://127.0.0.1:* ws://localhost:* wss://127.0.0.1:* wss://localhost:*"
].join("; ");
const RENDERER_CONTENT_TYPES = new Map<string, string>([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".png", "image/png"],
  [".gif", "image/gif"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".webp", "image/webp"],
  [".avif", "image/avif"],
  [".svg", "image/svg+xml"],
  [".ico", "image/x-icon"],
  [".woff", "font/woff"],
  [".woff2", "font/woff2"],
  [".ttf", "font/ttf"],
  [".otf", "font/otf"]
]);

type RendererProtocolRequest = Pick<Request, "method" | "url">;
type RendererFileFetch = (url: string) => Promise<Response>;

export function registerRendererSchemePrivileges(): void {
  protocol.registerSchemesAsPrivileged([
    {
      scheme: PACKAGED_RENDERER_SCHEME,
      privileges: {
        standard: true,
        secure: true,
        supportFetchAPI: true
      }
    }
  ]);
}

export function registerPackagedRendererProtocol(rendererRoot: string): void {
  const canonicalRoot = realpathSync(resolve(rendererRoot));
  protocol.handle(PACKAGED_RENDERER_SCHEME, (request) => {
    return handleRendererProtocolRequest(canonicalRoot, request, (url) => net.fetch(url));
  });
}

export async function handleRendererProtocolRequest(
  canonicalRendererRoot: string,
  request: RendererProtocolRequest,
  fetchFile: RendererFileFetch
): Promise<Response> {
  if (request.method !== "GET" && request.method !== "HEAD") {
    return secureTextResponse(405, "Method Not Allowed", { Allow: "GET, HEAD" });
  }

  const candidate = resolveRendererAssetPath(canonicalRendererRoot, request.url);
  if (!candidate || !existsSync(candidate)) {
    return secureTextResponse(404, "Not Found");
  }

  let canonicalCandidate: string;
  try {
    canonicalCandidate = realpathSync(candidate);
    if (!isPathInside(canonicalRendererRoot, canonicalCandidate) || !statSync(canonicalCandidate).isFile()) {
      return secureTextResponse(404, "Not Found");
    }
  } catch {
    return secureTextResponse(404, "Not Found");
  }

  const relativeAsset = relative(canonicalRendererRoot, canonicalCandidate);
  const extension = extname(canonicalCandidate).toLowerCase();
  const contentType = RENDERER_CONTENT_TYPES.get(extension);
  if (!contentType || (extension === ".html" && relativeAsset !== "index.html")) {
    return secureTextResponse(404, "Not Found");
  }

  try {
    const fileResponse = await fetchFile(pathToFileURL(canonicalCandidate).toString());
    const headers = new Headers(fileResponse.headers);
    headers.set("Content-Type", contentType);
    applySecurityHeaders(headers);
    return new Response(request.method === "HEAD" ? null : fileResponse.body, {
      status: fileResponse.status,
      statusText: fileResponse.statusText,
      headers
    });
  } catch {
    return secureTextResponse(404, "Not Found");
  }
}

export function resolveRendererAssetPath(rendererRoot: string, requestUrl: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(requestUrl);
  } catch {
    return null;
  }
  if (
    parsed.protocol !== `${PACKAGED_RENDERER_SCHEME}:`
    || parsed.hostname !== PACKAGED_RENDERER_HOST
    || parsed.username
    || parsed.password
    || parsed.port
    || !hasExactRendererAuthority(requestUrl)
  ) {
    return null;
  }

  const rawPath = rawRequestPath(requestUrl);
  if (rawPath === null || !rawPath.startsWith("/") || rawPath.includes("//")) {
    return null;
  }

  let segments: string[];
  try {
    segments = (rawPath === "/" ? ["index.html"] : rawPath.slice(1).split("/")).map(
      (segment) => decodeURIComponent(segment)
    );
  } catch {
    return null;
  }
  if (
    segments.some(
      (segment) => segment.includes("/") || segment.includes("\\") || isUnsafeRendererPathSegment(segment)
    )
  ) {
    return null;
  }

  const root = resolve(rendererRoot);
  const candidate = resolve(root, ...segments);
  return isPathInside(root, candidate) ? candidate : null;
}

function rawRequestPath(requestUrl: string): string | null {
  const schemeDelimiter = requestUrl.indexOf("://");
  if (schemeDelimiter < 0) return null;
  const authorityStart = schemeDelimiter + 3;
  const boundaryIndexes = ["/", "?", "#"]
    .map((marker) => requestUrl.indexOf(marker, authorityStart))
    .filter((index) => index >= 0);
  if (!boundaryIndexes.length) return "/";
  const boundary = Math.min(...boundaryIndexes);
  if (requestUrl[boundary] !== "/") return "/";
  const suffix = requestUrl.slice(boundary);
  const queryOrFragment = [suffix.indexOf("?"), suffix.indexOf("#")]
    .filter((index) => index >= 0);
  return queryOrFragment.length ? suffix.slice(0, Math.min(...queryOrFragment)) : suffix;
}

export function isPackagedRendererEntryUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    const rawPath = rawRequestPath(url);
    return (
      parsed.protocol === `${PACKAGED_RENDERER_SCHEME}:`
      && parsed.hostname === PACKAGED_RENDERER_HOST
      && !parsed.username
      && !parsed.password
      && !parsed.port
      && hasExactRendererAuthority(url)
      && (rawPath === "/" || rawPath === "/index.html")
    );
  } catch {
    return false;
  }
}

function hasExactRendererAuthority(requestUrl: string): boolean {
  const schemeDelimiter = requestUrl.indexOf("://");
  if (schemeDelimiter < 0) return false;
  if (requestUrl.slice(0, schemeDelimiter).toLowerCase() !== PACKAGED_RENDERER_SCHEME) return false;
  const authorityStart = schemeDelimiter + 3;
  const boundaryIndexes = ["/", "?", "#"]
    .map((marker) => requestUrl.indexOf(marker, authorityStart))
    .filter((index) => index >= 0);
  const authorityEnd = boundaryIndexes.length ? Math.min(...boundaryIndexes) : requestUrl.length;
  return requestUrl.slice(authorityStart, authorityEnd).toLowerCase() === PACKAGED_RENDERER_HOST;
}

function isUnsafeRendererPathSegment(segment: string): boolean {
  const windowsBaseName = segment.split(".", 1)[0].trimEnd();
  return (
    !segment
    || segment === "."
    || segment === ".."
    || segment.includes("%")
    || /[\u0000-\u001f\u007f<>:"|?*]/u.test(segment)
    || /^(?:con|prn|aux|nul|clock\$|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])$/iu.test(
      windowsBaseName
    )
    || segment.endsWith(".")
    || segment.endsWith(" ")
  );
}

function isPathInside(root: string, candidate: string): boolean {
  const child = relative(resolve(root), resolve(candidate));
  return child !== "" && child !== ".." && !child.startsWith(`..${sep}`) && !isAbsolute(child);
}

function secureTextResponse(
  status: number,
  body: string,
  extraHeaders: Record<string, string> = {}
): Response {
  const headers = new Headers({
    "Content-Type": "text/plain; charset=utf-8",
    ...extraHeaders
  });
  applySecurityHeaders(headers);
  return new Response(body, { status, headers });
}

function applySecurityHeaders(headers: Headers): void {
  headers.set("Content-Security-Policy", RENDERER_CSP);
  headers.set("Cross-Origin-Opener-Policy", "same-origin");
  headers.set("Cross-Origin-Resource-Policy", "same-origin");
  headers.set("X-Content-Type-Options", "nosniff");
}
