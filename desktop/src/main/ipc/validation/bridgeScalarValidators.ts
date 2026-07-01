import { ApiRequestValidationError } from "./errors";

export function validateBridgeEnum<T extends string>(
  value: unknown,
  label: string,
  allowed: ReadonlySet<string>,
  defaultValue?: T
): T {
  if (value === undefined || value === null || value === "") {
    if (defaultValue !== undefined) {
      return defaultValue;
    }
    throw new ApiRequestValidationError(`${label} is required`);
  }
  if (typeof value !== "string") {
    throw new ApiRequestValidationError(`${label} is invalid`);
  }
  const normalized = value.trim().toLowerCase();
  if (!allowed.has(normalized)) {
    throw new ApiRequestValidationError(`${label} is invalid`);
  }
  return normalized as T;
}

export function validateBridgeBoolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") {
    throw new ApiRequestValidationError(`${label} must be a boolean`);
  }
  return value;
}

export function validateBridgeFiniteNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || Math.abs(value) > Number.MAX_SAFE_INTEGER) {
    throw new ApiRequestValidationError(`${label} must be a finite number`);
  }
  return value;
}

export function validateBridgePositiveInteger(
  value: unknown,
  label: string,
  defaultValue: number,
  minimum: number,
  maximum: number
): number {
  if (value === undefined) {
    return defaultValue;
  }
  if (typeof value !== "number" || !Number.isInteger(value) || value < minimum || value > maximum) {
    throw new ApiRequestValidationError(`${label} is invalid`);
  }
  return value;
}
