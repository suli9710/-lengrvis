import type { DocumentAskRequest, DocumentCompareRequest, DocumentParseRequest } from "../../../shared/documentTypes";
import {
  ApiRequestValidationError,
  rejectUnexpectedBridgeKeys,
  validateBridgeBoolean,
  validateBridgePathValue,
  validateBridgePositiveInteger,
  validateBridgeStringArray,
  validateBridgeStringValue,
  validatePlainBridgeBody
} from "./primitives";

const DOCUMENT_QUESTION_MAX_CHARS = 8_000;
const DOCUMENT_FOCUS_MAX_CHARS = 4_000;
const DOCUMENT_PARSE_ALLOWED_KEYS = new Set(["path", "includeText", "include_text"]);
const DOCUMENT_ASK_ALLOWED_KEYS = new Set(["path", "question", "topK", "top_k"]);
const DOCUMENT_COMPARE_ALLOWED_KEYS = new Set(["paths", "focus"]);

export function validateDocumentParseRequest(value: unknown): { path: string; include_text?: boolean } {
  const request = validatePlainBridgeBody(value, "document parse request") as DocumentParseRequest &
    Record<string, unknown>;
  rejectUnexpectedBridgeKeys(request, DOCUMENT_PARSE_ALLOWED_KEYS, "document parse request");
  const body: { path: string; include_text?: boolean } = {
    path: validateBridgePathValue(request.path, "document path")
  };
  const includeText = request.includeText ?? request.include_text;
  if (includeText !== undefined) {
    body.include_text = validateBridgeBoolean(includeText, "document includeText");
  }
  return body;
}

export function validateDocumentAskRequest(value: unknown): { path: string; question: string; top_k?: number } {
  const request = validatePlainBridgeBody(value, "document ask request") as DocumentAskRequest &
    Record<string, unknown>;
  rejectUnexpectedBridgeKeys(request, DOCUMENT_ASK_ALLOWED_KEYS, "document ask request");
  const body: { path: string; question?: string; top_k?: number } = {
    path: validateBridgePathValue(request.path, "document path")
  };
  body.question = validateBridgeStringValue(request.question, "document question", DOCUMENT_QUESTION_MAX_CHARS, {
    allowEmpty: false,
    trim: true
  });
  const topK = request.topK ?? request.top_k;
  if (topK !== undefined) {
    body.top_k = validateBridgePositiveInteger(topK, "document topK", 5, 1, 20);
  }
  return body as { path: string; question: string; top_k?: number };
}

export function validateDocumentCompareRequest(value: unknown): { paths: string[]; focus?: string } {
  const request = validatePlainBridgeBody(value, "document compare request") as DocumentCompareRequest &
    Record<string, unknown>;
  rejectUnexpectedBridgeKeys(request, DOCUMENT_COMPARE_ALLOWED_KEYS, "document compare request");
  const paths = validateBridgeStringArray(request.paths, "document compare paths", 2, 4096);
  if (paths.length !== 2) {
    throw new ApiRequestValidationError("document compare requires exactly two paths");
  }
  const body: { paths: string[]; focus?: string } = { paths };
  if (request.focus !== undefined && request.focus !== null && request.focus !== "") {
    body.focus = validateBridgeStringValue(request.focus, "document compare focus", DOCUMENT_FOCUS_MAX_CHARS, {
      allowEmpty: false,
      trim: true
    });
  }
  return body;
}
