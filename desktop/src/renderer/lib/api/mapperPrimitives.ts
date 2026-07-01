export function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

export function numberOrUndefined(value: unknown): number | undefined {
  const number = typeof value === "number" ? value : typeof value === "string" && value.trim() ? Number(value) : NaN;
  return Number.isFinite(number) ? number : undefined;
}

export function numberOrZero(value: unknown): number {
  return numberOrUndefined(value) ?? 0;
}

export function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

export function arrayOfObjects(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)))
    : [];
}

export function recordOrUndefined(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

export function optionalObjectRecord(value: unknown): Record<string, unknown> | undefined {
  return recordOrUndefined(value);
}

export function tableRowsFromUnknown(value: unknown): string[][] {
  if (!Array.isArray(value)) return [];
  return value.map((row) => {
    if (Array.isArray(row)) {
      return row.map((cell) => String(cell ?? ""));
    }
    if (row && typeof row === "object") {
      return Object.values(row).map((cell) => String(cell ?? ""));
    }
    return [String(row ?? "")];
  });
}

export function fileNameFromPath(path: string): string | undefined {
  const normalized = path.replace(/\\/g, "/");
  const name = normalized.split("/").filter(Boolean).pop();
  return name || undefined;
}
