import { ApiRequestValidationError } from "./errors";

export function validateBridgeIdentifier(value: unknown, label: string): string {
  if (typeof value !== "string") {
    throw new ApiRequestValidationError(`${label} is required`);
  }
  const trimmed = value.trim();
  if (
    !trimmed ||
    trimmed.length > 128 ||
    /[\s/\\?#\u0000-\u001F\u007F]/.test(trimmed) ||
    trimmed === "." ||
    trimmed === ".."
  ) {
    throw new ApiRequestValidationError(`${label} is invalid`);
  }
  return trimmed;
}

export function validateBridgePathValue(value: unknown, label: string): string {
  if (typeof value !== "string") {
    throw new ApiRequestValidationError(`${label} is required`);
  }
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > 4096 || trimmed.includes("\0") || /[\u0000-\u001F\u007F]/.test(trimmed)) {
    throw new ApiRequestValidationError(`${label} is invalid`);
  }
  return trimmed;
}

export function validateBridgeStringArray(value: unknown, label: string, maxItems: number, maxChars: number): string[] {
  if (!Array.isArray(value) || value.length > maxItems) {
    throw new ApiRequestValidationError(`${label} must be an array`);
  }
  return value.map((item, index) =>
    validateBridgeStringValue(item, `${label}[${index}]`, maxChars, { allowEmpty: false, trim: true })
  );
}

export function validateBridgeStringValue(
  value: unknown,
  label: string,
  maxChars: number,
  options: { allowEmpty?: boolean; trim?: boolean } = {}
): string {
  if (typeof value !== "string") {
    throw new ApiRequestValidationError(`${label} must be a string`);
  }
  const result = options.trim ? value.trim() : value;
  if (
    (!options.allowEmpty && !result) ||
    result.length > maxChars ||
    result.includes("\0") ||
    /[\u0000-\u001F\u007F]/.test(result)
  ) {
    throw new ApiRequestValidationError(`${label} is invalid`);
  }
  return result;
}

export function validateOptionalConfirmationNonce(value: unknown): string | undefined {
  if (value === undefined || value === null || value === "") {
    return undefined;
  }
  return validateBridgeIdentifier(value, "confirmation nonce");
}

export function validateOptionalBridgeIdentifier(value: unknown, label: string): string | undefined {
  if (value === undefined || value === null || value === "") {
    return undefined;
  }
  return validateBridgeIdentifier(value, label);
}

export function validateOptionalStringList(
  value: unknown,
  label: string,
  maxItems: number,
  maxChars: number
): string[] | undefined {
  if (value === undefined || value === null) {
    return undefined;
  }
  return validateBridgeStringArray(value, label, maxItems, maxChars);
}
