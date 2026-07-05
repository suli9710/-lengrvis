export interface BackendDocumentParseRequest {
  path: string;
  include_text?: boolean;
}

export interface BackendDocumentAskRequest {
  path: string;
  question: string;
  top_k?: number;
}

export interface BackendDocumentCompareRequest {
  paths: string[];
  focus?: string;
}

export interface BackendDocumentBlock {
  id?: string;
  block_id?: string;
  type?: string;
  kind?: string;
  text?: string;
  content?: string;
  level?: number | string;
  page?: number | string;
  order?: number | string;
  index?: number | string;
  columns?: unknown;
  rows?: unknown;
  metadata?: unknown;
}

export interface BackendDocumentTable {
  id?: string;
  table_id?: string;
  title?: string;
  name?: string;
  columns?: unknown;
  rows?: unknown;
  page?: number | string;
  source_block_id?: string;
  sourceBlockId?: string;
}

export interface BackendDocumentCitation {
  id?: string;
  label?: string;
  text?: string;
  snippet?: string;
  content?: string;
  path?: string;
  block_id?: string;
  blockId?: string;
  page?: number | string;
  score?: number | string;
}

export interface BackendDocumentIR {
  id?: string;
  document_id?: string;
  path?: string;
  title?: string;
  name?: string;
  mime_type?: string;
  mimeType?: string;
  language?: string;
  summary?: string;
  text?: string;
  truncated?: boolean;
  blocks?: BackendDocumentBlock[];
  tables?: BackendDocumentTable[];
  citations?: BackendDocumentCitation[];
  metadata?: unknown;
  created_at?: string;
  createdAt?: string;
}

export interface BackendDocumentAskResponse {
  answer?: string;
  summary?: string;
  citations?: unknown;
  citation_items?: BackendDocumentCitation[];
  citations_detail?: BackendDocumentCitation[];
  source_chunks?: BackendDocumentCitation[];
  sources?: BackendDocumentCitation[];
  note?: string;
}

export interface BackendDocumentCompareDifference {
  id?: string;
  title?: string;
  field?: string;
  detail?: string;
  summary?: string;
  text?: string;
  severity?: string;
  citations?: BackendDocumentCitation[];
}

export interface BackendDocumentCompareResponse {
  summary?: string;
  documents?: BackendDocumentIR[];
  differences?: BackendDocumentCompareDifference[];
  items?: BackendDocumentCompareDifference[];
  tables?: BackendDocumentTable[];
  note?: string;
}
