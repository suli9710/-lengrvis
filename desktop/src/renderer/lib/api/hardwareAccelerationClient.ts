import type {
  HardwareAccelerationSmokePayload,
  HardwareAccelerationStatusPayload
} from "../../../shared/hardwareAccelerationTypes";
import type { ApiRequest, ApiResponse } from "../../../shared/desktopBridgeTypes";
import type {
  BackendHardwareAccelerationSmoke,
  BackendHardwareAccelerationStatus,
  HardwareAccelerationSmokeRequest,
  HardwareAccelerationSmokeRequestBody
} from "./hardwareAccelerationBackendTypes";
import { mapHardwareAccelerationSmoke, mapHardwareAccelerationStatus } from "./hardwareAccelerationMappers";
import { mapResponse } from "./transport";

export type HardwareAccelerationEndpointRequest = <TResponse, TBody = unknown>(
  request: ApiRequest<TBody>
) => Promise<ApiResponse<TResponse>>;

export function getHardwareAccelerationStatusEndpoint(
  request: HardwareAccelerationEndpointRequest
): Promise<ApiResponse<HardwareAccelerationStatusPayload>> {
  return request<BackendHardwareAccelerationStatus>({
    endpoint: "/api/settings/onnx/status",
    timeoutMs: 2500
  }).then((response) => mapResponse(response, mapHardwareAccelerationStatus));
}

export async function runHardwareAccelerationSmokeEndpoint(
  request: HardwareAccelerationEndpointRequest,
  payload: HardwareAccelerationSmokeRequest = {}
): Promise<ApiResponse<HardwareAccelerationSmokePayload>> {
  const endpointByOperation: Record<NonNullable<HardwareAccelerationSmokeRequest["operation"]>, string> = {
    warmup: "/api/settings/onnx/warmup",
    test_generate: "/api/settings/onnx/test-generate",
    test_embedding: "/api/settings/onnx/test-embedding",
    test_ocr: "/api/settings/onnx/test-ocr",
    test_image_embedding: "/api/settings/onnx/test-image-embedding"
  };
  const operation = payload.operation ?? "warmup";
  const body = operation === "test_generate"
    ? {
        prompt: payload.prompt,
        max_tokens: payload.maxTokens,
        model_path: payload.modelPath
      }
    : operation === "test_embedding"
      ? {
          texts: payload.texts,
          model_path: payload.modelPath
        }
      : operation === "test_image_embedding"
        ? {
            image_path: payload.imagePath,
            model_path: payload.modelPath
          }
        : {
            model_path: payload.modelPath
          };
  const response = window.lengrvis?.hardwareAcceleration
    ? await window.lengrvis.hardwareAcceleration.smoke({
        operation,
        prompt: payload.prompt,
        maxTokens: payload.maxTokens,
        texts: payload.texts,
        modelPath: payload.modelPath,
        imagePath: payload.imagePath
      }) as ApiResponse<BackendHardwareAccelerationSmoke>
    : await request<BackendHardwareAccelerationSmoke, HardwareAccelerationSmokeRequestBody>({
        endpoint: endpointByOperation[operation],
        method: "POST",
        body,
        timeoutMs: 10_000
      });
  return mapResponse(response, (data) => mapHardwareAccelerationSmoke(data, operation));
}
