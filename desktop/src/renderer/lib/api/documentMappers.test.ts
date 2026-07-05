import { describe, expect, it } from "vitest";

import { mapDocumentAskResponse, mapDocumentCompareResponse, mapDocumentIR } from "./documentMappers";

describe("document mappers", () => {
  it("maps document IR aliases and derives tables from table blocks", () => {
    expect(
      mapDocumentIR({
        document_id: "doc_1",
        path: "C:\\Docs\\sample.pdf",
        name: "Sample",
        mime_type: "application/pdf",
        language: "zh",
        text: "body",
        truncated: false,
        blocks: [
          {
            block_id: "block_1",
            kind: "table",
            content: "Revenue",
            page: "2",
            columns: ["Year", "Amount"],
            rows: [{ Year: 2026, Amount: 42 }]
          }
        ],
        citations: [{ id: "c1", label: "A", snippet: "quoted", page: "2", score: "0.75" }],
        metadata: { source: "fixture" },
        created_at: "2026-01-01T00:00:00Z"
      })
    ).toMatchObject({
      id: "doc_1",
      path: "C:\\Docs\\sample.pdf",
      title: "Sample",
      mimeType: "application/pdf",
      language: "zh",
      text: "body",
      truncated: false,
      blocks: [
        {
          id: "block_1",
          type: "table",
          text: "Revenue",
          page: 2,
          columns: ["Year", "Amount"],
          rows: [["2026", "42"]]
        }
      ],
      tables: [
        {
          id: "block_1",
          title: "Revenue",
          columns: ["Year", "Amount"],
          rows: [["2026", "42"]],
          page: 2,
          sourceBlockId: "block_1"
        }
      ],
      citations: [{ id: "c1", label: "A", text: "quoted", page: 2, score: 0.75 }],
      metadata: { source: "fixture" },
      createdAt: "2026-01-01T00:00:00Z"
    });
  });

  it("uses source chunks when ask response citations are not structured", () => {
    expect(
      mapDocumentAskResponse({
        summary: "answer",
        citations: ["not-structured"],
        source_chunks: [{ id: "chunk_1", snippet: "source text", path: "a.md" }],
        note: "partial"
      })
    ).toEqual({
      answer: "answer",
      citations: [{ id: "chunk_1", label: "chunk_1", text: "source text", path: "a.md" }],
      sourceChunks: [{ id: "chunk_1", label: "chunk_1", text: "source text", path: "a.md" }],
      note: "partial"
    });
  });

  it("maps compare response fallback items", () => {
    expect(
      mapDocumentCompareResponse({
        summary: "changed",
        documents: [{ id: "doc_a", path: "a.md", title: "A" }],
        items: [{ field: "price", text: "price changed", severity: "warning" }],
        tables: [{ table_id: "table_1", rows: [["a"]], columns: ["col"] }]
      })
    ).toMatchObject({
      summary: "changed",
      documents: [{ id: "doc_a", path: "a.md", title: "A" }],
      differences: [
        {
          id: "difference-1",
          title: "price",
          detail: "price changed",
          severity: "warning"
        }
      ],
      tables: [{ id: "table_1", columns: ["col"], rows: [["a"]] }]
    });
  });
});
