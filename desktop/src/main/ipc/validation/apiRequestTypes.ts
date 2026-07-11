import type { ApiMethod, ApiQueryValue } from "../../../shared/types";

export interface ValidatedApiRequest {
  endpoint: string;
  method: ApiMethod;
  query?: Record<string, Exclude<ApiQueryValue, null | undefined>>;
  serializedBody?: string;
  timeoutMs: number;
  abortGroup?: string;
}

export interface ApiRequestValidationOptions {
  allowExplicitDesktopBridgePath?: boolean;
}
