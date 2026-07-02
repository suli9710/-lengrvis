import { Table2 } from "lucide-react";

import type { DocumentAskResponse, DocumentCompareResponse, DocumentIR } from "../../../shared/types";

export function DocumentResultView({ document }: { document: DocumentIR }) {
  const previewBlocks = document.blocks.filter((block) => block.text).slice(0, 4);
  return (
    <div className="document-preview">
      <div className="row row--between">
        <strong>{document.title}</strong>
        <span className="muted">{document.tables.length} 张表</span>
      </div>
      {document.summary ? <p>{document.summary}</p> : null}
      {previewBlocks.length ? (
        <ul>
          {previewBlocks.map((block) => (
            <li key={block.id}>
              <span className="muted">{block.page ? `第 ${block.page} 页` : block.type}</span>
              <p>{block.text}</p>
            </li>
          ))}
        </ul>
      ) : null}
      {document.tables.length ? <TablePreview table={document.tables[0]} /> : null}
    </div>
  );
}

export function DocumentAnswerView({ answer }: { answer: DocumentAskResponse }) {
  return (
    <div className="document-preview">
      <strong>回答</strong>
      <p>{answer.answer || "没有生成回答。"}</p>
      {answer.citations.length ? (
        <ul className="citation-list">
          {answer.citations.slice(0, 4).map((citation) => (
            <li key={citation.id}>
              <span>{citation.label}</span>
              <p>{citation.text}</p>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function DocumentCompareView({ result }: { result: DocumentCompareResponse }) {
  return (
    <div className="document-preview">
      <strong>对比结果</strong>
      {result.summary ? <p>{result.summary}</p> : null}
      {result.differences.length ? (
        <ul>
          {result.differences.slice(0, 5).map((difference) => (
            <li key={difference.id}>
              <span className="muted">{difference.severity || "差异"}</span>
              <p>
                <strong>{difference.title}</strong>：{difference.detail}
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">没有发现明显差异。</p>
      )}
    </div>
  );
}

function TablePreview({ table }: { table: DocumentIR["tables"][number] }) {
  return (
    <div className="table-preview">
      <div className="table-preview__title">
        <Table2 size={14} aria-hidden="true" />
        <span>{table.title || "表格结果"}</span>
      </div>
      <table>
        <thead>
          <tr>
            {(table.columns.length ? table.columns : table.rows[0] ?? []).slice(0, 4).map((column, index) => (
              <th key={`${column}-${index}`}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.slice(table.columns.length ? 0 : 1, 4).map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.slice(0, 4).map((cell, cellIndex) => (
                <td key={`${rowIndex}-${cellIndex}`}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
