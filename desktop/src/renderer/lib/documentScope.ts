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

export function uniqueScopePaths(paths: string[]): string[] {
  const seen = new Set<string>();
  const uniquePaths: string[] = [];
  for (const path of paths) {
    const value = path.trim();
    const key = normalizePathForCompare(value);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    uniquePaths.push(value);
  }
  return uniquePaths;
}

export function mergeScopePaths(paths: string[]): string[] {
  const mergedPaths: string[] = [];
  for (const path of uniqueScopePaths(paths)) {
    if (isPathWithinScope(path, mergedPaths)) continue;
    for (let index = mergedPaths.length - 1; index >= 0; index -= 1) {
      if (isPathWithinScope(mergedPaths[index], [path])) {
        mergedPaths.splice(index, 1);
      }
    }
    mergedPaths.push(path);
  }
  return mergedPaths;
}

export function documentScopesForFiles(filePaths: string[], currentScopes: string[]): string[] {
  const knownScopes = mergeScopePaths(currentScopes);
  const missingScopes: string[] = [];
  for (const filePath of filePaths) {
    const folderPath = parentDirectory(filePath);
    if (!folderPath || isPathWithinScope(filePath, [...knownScopes, ...missingScopes])) continue;
    missingScopes.splice(0, missingScopes.length, ...mergeScopePaths([...missingScopes, folderPath]));
  }
  return missingScopes;
}
