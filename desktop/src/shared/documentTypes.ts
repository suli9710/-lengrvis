export type DocumentBlockType =
  | "title"
  | "heading"
  | "paragraph"
  | "list"
  | "table"
  | "image"
  | "code"
  | "metadata"
  | string;

export interface DocumentTable {
  id: string;
  title?: string;
  columns: string[];
  rows: string[][];
  page?: number;
  sourceBlockId?: string;
}

export interface DocumentBlock {
  id: string;
  type: DocumentBlockType;
  text?: string;
  level?: number;
  page?: number;
  order?: number;
  columns?: string[];
  rows?: string[][];
  metadata?: Record<string, unknown>;
}

export interface DocumentCitation {
  id: string;
  label: string;
  text: string;
  path?: string;
  blockId?: string;
  page?: number;
  score?: number;
}

export interface DocumentIR {
  id: string;
  path: string;
  title: string;
  mimeType?: string;
  language?: string;
  summary?: string;
  text?: string;
  truncated?: boolean;
  blocks: DocumentBlock[];
  tables: DocumentTable[];
  citations?: DocumentCitation[];
  metadata?: Record<string, unknown>;
  createdAt?: string;
}

export interface DocumentParseRequest {
  path: string;
  includeText?: boolean;
}

export interface DocumentAskRequest {
  path: string;
  question: string;
  topK?: number;
}

export interface DocumentAskResponse {
  answer: string;
  citations: DocumentCitation[];
  sourceChunks?: DocumentCitation[];
  note?: string;
}

export interface DocumentCompareRequest {
  paths: string[];
  focus?: string;
}

export interface DocumentDifference {
  id: string;
  title: string;
  detail: string;
  severity?: "info" | "warning" | "critical" | string;
  citations?: DocumentCitation[];
}

export interface DocumentCompareResponse {
  summary: string;
  documents: DocumentIR[];
  differences: DocumentDifference[];
  tables?: DocumentTable[];
  note?: string;
}
