import type { ApiRequest, ApiResponse } from "../shared/desktopBridgeTypes";
import { backendErrorMessage } from "../shared/backendError";
import {
  ApiRequestValidationError,
  type ApiRequestValidationOptions,
  buildValidatedRequestUrl,
  validateApiRequest
} from "./ipcValidation";
import { mergeAbortSignals, resolveInflightGroupSignal } from "./ipcInflight";

const DESKTOP_API_TOKEN_HEADER = "X-Lengrvis-Desktop-Token";

export type InternalDesktopBridgeRequest = Omit<ApiRequest, "headers"> & {
  headers?: Record<string, string>;
};

export async function proxyApiRequest<TData>(
  baseUrl: string,
  request: InternalDesktopBridgeRequest,
  desktopApiToken: string,
  options: ApiRequestValidationOptions & { allowInternalHeaders?: boolean } = {}
): Promise<ApiResponse<TData>> {
  const receivedAt = new Date().toISOString();
  let timeout: ReturnType<typeof setTimeout> | undefined;

  try {
    const { allowInternalHeaders, ...validationOptions } = options;
    const { headers: extraHeaders, ...requestWithoutHeaders } = request;
    const requestForValidation = allowInternalHeaders ? requestWithoutHeaders : request;
    const validatedRequest = validateApiRequest(requestForValidation, validationOptions);
    const url = buildValidatedRequestUrl(baseUrl, validatedRequest);
    const timeoutController = new AbortController();
    timeout = setTimeout(
      () => timeoutController.abort(),
      validatedRequest.timeoutMs
    );
    const groupSignal = resolveInflightGroupSignal(validatedRequest.abortGroup);
    const signal = groupSignal
      ? mergeAbortSignals([groupSignal, timeoutController.signal])
      : timeoutController.signal;

    const response = await fetch(url, {
      method: validatedRequest.method,
      headers: {
        Accept: "application/json",
        [DESKTOP_API_TOKEN_HEADER]: desktopApiToken,
        ...(allowInternalHeaders ? (extraHeaders ?? {}) : {}),
        ...(validatedRequest.serializedBody !== undefined ? { "Content-Type": "application/json" } : {})
      },
      body: validatedRequest.serializedBody,
      signal
    });

    const data = await parseResponseBody(response);

    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: {
          code: `HTTP_${response.status}`,
          message: getErrorMessage(data, response.statusText),
          details: data
        },
        receivedAt
      };
    }

    return {
      ok: true,
      status: response.status,
      data: data as TData,
      receivedAt
    };
  } catch (error) { // broad-exception-boundary
    const message = error instanceof Error ? error.message : "Request failed";
    if (error instanceof ApiRequestValidationError) {
      return {
        ok: false,
        status: 0,
        error: {
          code: "INVALID_RENDERER_API_REQUEST",
          message
        },
        receivedAt
      };
    }

    return {
      ok: false,
      status: 0,
      error: {
        code: "NETWORK_ERROR",
        message
      },
      receivedAt
    };
  } finally {
    if (timeout) {
      clearTimeout(timeout);
    }
  }
}

async function parseResponseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";

  if (response.status === 204) {
    return undefined;
  }

  if (contentType.includes("application/json")) {
    return response.json();
  }

  const text = await response.text();
  return text ? { message: text } : undefined;
}

function getErrorMessage(data: unknown, fallback: string): string {
  return userFacingBackendError(backendErrorMessage(data, fallback || "Backend request failed"));
}

function userFacingBackendError(message: string): string {
  const normalized = message.toLowerCase();
  if (normalized.includes("missing desktop api token") || normalized.includes("unauthorized")) {
    return "Lengrvis 正在保护本机接口。请重启桌面应用后再试；未授权页面不能直接读取本机数据。";
  }
  return message;
}
