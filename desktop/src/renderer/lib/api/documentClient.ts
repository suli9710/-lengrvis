import type {
  DocumentAskRequest,
  DocumentAskResponse,
  DocumentCompareRequest,
  DocumentCompareResponse,
  DocumentIR,
  DocumentParseRequest
} from "../../../shared/documentTypes";
import type { ApiRequest, ApiResponse } from "../../../shared/desktopBridgeTypes";
import type {
  BackendDocumentAskRequest,
  BackendDocumentAskResponse,
  BackendDocumentCompareRequest,
  BackendDocumentCompareResponse,
  BackendDocumentIR,
  BackendDocumentParseRequest
} from "./documentBackendTypes";
import { mapDocumentAskResponse, mapDocumentCompareResponse, mapDocumentIR } from "./documentMappers";
import { mapResponse } from "./transport";

export type DocumentEndpointRequest = <TResponse, TBody = unknown>(
  request: ApiRequest<TBody>
) => Promise<ApiResponse<TResponse>>;

export function parseDocumentEndpoint(
  request: DocumentEndpointRequest,
  body: DocumentParseRequest
): Promise<ApiResponse<DocumentIR>> {
  const response = window.lengrvis?.documents
    ? window.lengrvis.documents.parse(body) as Promise<ApiResponse<BackendDocumentIR>>
    : request<BackendDocumentIR, BackendDocumentParseRequest>({
        endpoint: "/api/documents/parse",
        method: "POST",
        body: {
          path: body.path,
          include_text: body.includeText
        },
        timeoutMs: 30_000
      });
  return response.then((result) => mapResponse(result, mapDocumentIR));
}

export function askDocumentEndpoint(
  request: DocumentEndpointRequest,
  body: DocumentAskRequest
): Promise<ApiResponse<DocumentAskResponse>> {
  const response = window.lengrvis?.documents
    ? window.lengrvis.documents.ask(body) as Promise<ApiResponse<BackendDocumentAskResponse>>
    : request<BackendDocumentAskResponse, BackendDocumentAskRequest>({
        endpoint: "/api/documents/ask",
        method: "POST",
        body: {
          path: body.path,
          question: body.question,
          top_k: body.topK
        },
        timeoutMs: 30_000
      });
  return response.then((result) => mapResponse(result, mapDocumentAskResponse));
}

export function compareDocumentsEndpoint(
  request: DocumentEndpointRequest,
  body: DocumentCompareRequest
): Promise<ApiResponse<DocumentCompareResponse>> {
  const response = window.lengrvis?.documents
    ? window.lengrvis.documents.compare(body) as Promise<ApiResponse<BackendDocumentCompareResponse>>
    : request<BackendDocumentCompareResponse, BackendDocumentCompareRequest>({
        endpoint: "/api/documents/compare",
        method: "POST",
        body: {
          paths: body.paths,
          focus: body.focus
        },
        timeoutMs: 45_000
      });
  return response.then((result) => mapResponse(result, mapDocumentCompareResponse));
}
