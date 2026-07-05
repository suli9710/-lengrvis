import type { DocumentAskResponse, DocumentCitation, DocumentCompareResponse, DocumentIR, DocumentTable } from "../../../shared/documentTypes";
import type {
  BackendDocumentAskResponse,
  BackendDocumentBlock,
  BackendDocumentCitation,
  BackendDocumentCompareResponse,
  BackendDocumentIR,
  BackendDocumentTable
} from "./documentBackendTypes";

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function numberOrUndefined(value: unknown): number | undefined {
  const number = typeof value === "number" ? value : typeof value === "string" && value.trim() ? Number(value) : NaN;
  return Number.isFinite(number) ? number : undefined;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

function arrayOfObjects(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)))
    : [];
}

function recordOrUndefined(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function fileNameFromPath(path: string): string | undefined {
  const normalized = path.replace(/\\/g, "/");
  const name = normalized.split("/").filter(Boolean).pop();
  return name || undefined;
}

function tableRowsFromUnknown(value: unknown): string[][] {
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

export function mapDocumentIR(data: BackendDocumentIR): DocumentIR {
  const path = String(data.path ?? "");
  const blocks = (data.blocks ?? []).map(mapDocumentBlock);
  const tables = [
    ...(data.tables ?? []).map(mapDocumentTable),
    ...blocks
      .filter((block) => block.type === "table" && (block.columns?.length || block.rows?.length))
      .map((block, index) => ({
        id: block.id || `table-${index + 1}`,
        title: block.text,
        columns: block.columns ?? [],
        rows: block.rows ?? [],
        page: block.page,
        sourceBlockId: block.id
      }))
  ];
  return {
    id: String(data.id ?? data.document_id ?? path),
    path,
    title: String(data.title ?? data.name ?? fileNameFromPath(path) ?? "文档"),
    mimeType: optionalString(data.mime_type ?? data.mimeType),
    language: optionalString(data.language),
    summary: optionalString(data.summary),
    text: optionalString(data.text),
    truncated: data.truncated === undefined ? undefined : Boolean(data.truncated),
    blocks,
    tables,
    citations: (data.citations ?? []).map(mapDocumentCitation),
    metadata: recordOrUndefined(data.metadata),
    createdAt: optionalString(data.created_at ?? data.createdAt)
  };
}

export function mapDocumentBlock(block: BackendDocumentBlock): DocumentIR["blocks"][number] {
  const rows = tableRowsFromUnknown(block.rows);
  return {
    id: String(block.id ?? block.block_id ?? crypto.randomUUID()),
    type: String(block.type ?? block.kind ?? "paragraph"),
    text: optionalString(block.text ?? block.content),
    level: numberOrUndefined(block.level),
    page: numberOrUndefined(block.page),
    order: numberOrUndefined(block.order ?? block.index),
    columns: stringArray(block.columns),
    rows,
    metadata: recordOrUndefined(block.metadata)
  };
}

export function mapDocumentTable(table: BackendDocumentTable): DocumentTable {
  return {
    id: String(table.id ?? table.table_id ?? crypto.randomUUID()),
    title: optionalString(table.title ?? table.name),
    columns: stringArray(table.columns),
    rows: tableRowsFromUnknown(table.rows),
    page: numberOrUndefined(table.page),
    sourceBlockId: optionalString(table.source_block_id ?? table.sourceBlockId)
  };
}

export function mapDocumentCitation(citation: BackendDocumentCitation, index = 0): DocumentCitation {
  const label = String(citation.label ?? citation.id ?? `引用 ${index + 1}`);
  return {
    id: String(citation.id ?? label),
    label,
    text: String(citation.text ?? citation.snippet ?? citation.content ?? ""),
    path: optionalString(citation.path),
    blockId: optionalString(citation.block_id ?? citation.blockId),
    page: numberOrUndefined(citation.page),
    score: numberOrUndefined(citation.score)
  };
}

export function mapDocumentAskResponse(data: BackendDocumentAskResponse): DocumentAskResponse {
  const sourceChunks = (data.source_chunks ?? data.sources ?? []).map(mapDocumentCitation);
  const citationItems = arrayOfObjects(data.citation_items ?? data.citations_detail ?? data.citations);
  return {
    answer: String(data.answer ?? data.summary ?? ""),
    citations: citationItems.length ? citationItems.map(mapDocumentCitation) : sourceChunks,
    sourceChunks,
    note: optionalString(data.note)
  };
}

export function mapDocumentCompareResponse(data: BackendDocumentCompareResponse): DocumentCompareResponse {
  return {
    summary: String(data.summary ?? ""),
    documents: (data.documents ?? []).map(mapDocumentIR),
    differences: (data.differences ?? data.items ?? []).map((item, index) => ({
      id: String(item.id ?? `difference-${index + 1}`),
      title: String(item.title ?? item.field ?? `差异 ${index + 1}`),
      detail: String(item.detail ?? item.summary ?? item.text ?? ""),
      severity: optionalString(item.severity),
      citations: (item.citations ?? []).map(mapDocumentCitation)
    })),
    tables: (data.tables ?? []).map(mapDocumentTable),
    note: optionalString(data.note)
  };
}
