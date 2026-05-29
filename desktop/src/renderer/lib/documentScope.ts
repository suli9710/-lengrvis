export function normalizePathForCompare(path: string): string {
  return path.trim().replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

export function parentDirectory(path: string): string {
  const value = path.trim().replace(/\/+$/, "").replace(/\\+$/, "");
  const separatorIndex = Math.max(value.lastIndexOf("\\"), value.lastIndexOf("/"));
  if (separatorIndex <= 0) return "";
  if (separatorIndex === 2 && /^[a-z]:/i.test(value)) return value.slice(0, 3);
  return value.slice(0, separatorIndex);
}

export function isPathWithinScope(path: string, scopes: string[]): boolean {
  const normalizedPath = normalizePathForCompare(path);
  if (!normalizedPath) return false;
  return scopes.some((scope) => {
    const normalizedScope = normalizePathForCompare(scope);
    return Boolean(normalizedScope && (normalizedPath === normalizedScope || normalizedPath.startsWith(`${normalizedScope}/`)));
  });
}
