import { FileQuestion, FileText, Layers, Search, Table2, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import type { CleanupPlan, DocumentAskResponse, DocumentCompareResponse, DocumentIR, FileSearchResult } from "../../shared/types";
import type { BackendClusterEntry, FileClusterOptions, MavrisApiClient } from "../lib/apiClient";
import { Badge, Panel } from "./Panel";

interface FileSearchPanelProps {
  results: FileSearchResult[];
  isSearching: boolean;
  onSearch: (query: string) => Promise<void>;
  api?: MavrisApiClient;
}

type FileClusterDimension =
  | "content"
  | "type"
  | "extension"
  | "image_auto"
  | "scene"
  | "people"
  | "objects"
  | "tags"
  | "time"
  | "location";

interface FileClusterDimensionOption {
  value: FileClusterDimension;
  label: string;
  description: string;
}

const CLUSTER_DIMENSION_OPTIONS: FileClusterDimensionOption[] = [
  { value: "content", label: "内容", description: "按文件名和扩展名做轻量内容聚类" },
  { value: "type", label: "类型", description: "按后端识别的文件类型分组" },
  { value: "extension", label: "扩展名", description: "按文件扩展名精确分组" },
  { value: "image_auto", label: "图片自动", description: "按图片语义和元数据自动聚类" },
  { value: "scene", label: "场景", description: "按图片场景标签分组" },
  { value: "people", label: "人物", description: "按图片中的人物数量分组" },
  { value: "objects", label: "物体", description: "按图片中的可见物体分组" },
  { value: "tags", label: "标签", description: "按图片结构化标签分组" },
  { value: "time", label: "时间", description: "按图片拍摄或修改时间分组" },
  { value: "location", label: "地点", description: "按图片 GPS 位置分组" }
];

export function FileSearchPanel({ results, isSearching, onSearch, api }: FileSearchPanelProps) {
  const [query, setQuery] = useState("");
  const [clusters, setClusters] = useState<BackendClusterEntry[]>([]);
  const [isClustering, setIsClustering] = useState(false);
  const [clusterError, setClusterError] = useState<string | null>(null);
  const [clusterDimension, setClusterDimension] = useState<FileClusterDimension>("content");
  const [clusterResultDimension, setClusterResultDimension] = useState<FileClusterDimension>("content");
  const [documentPath, setDocumentPath] = useState("");
  const [documentQuestion, setDocumentQuestion] = useState("");
  const [comparePath, setComparePath] = useState("");
  const [documentResult, setDocumentResult] = useState<DocumentIR | null>(null);
  const [documentAnswer, setDocumentAnswer] = useState<DocumentAskResponse | null>(null);
  const [compareResult, setCompareResult] = useState<DocumentCompareResponse | null>(null);
  const [documentError, setDocumentError] = useState<string | null>(null);
  const [isDocumentWorking, setIsDocumentWorking] = useState(false);
  const [cleanupPlan, setCleanupPlan] = useState<CleanupPlan | null>(null);
  const [cleanupError, setCleanupError] = useState<string | null>(null);
  const [isCleanupWorking, setIsCleanupWorking] = useState(false);

  const selectedClusterDimension = clusterDimensionOption(clusterDimension);
  const resultClusterDimension = clusterDimensionOption(clusterResultDimension);
  const selectedDocumentPath = documentPath.trim() || results[0]?.path || "";
  const cleanupBuckets = useMemo(() => splitCleanupItems(cleanupPlan), [cleanupPlan]);

  const submit = async () => {
    await onSearch(query.trim());
  };

  const runCluster = async () => {
    if (!api) return;
    const requestedDimension = clusterDimension;
    setIsClustering(true);
    setClusterError(null);
    setClusterResultDimension(requestedDimension);
    try {
      const response = await api.clusterFiles(clusterPayloadFor(requestedDimension));
      if (response.ok && response.data?.ok) {
        setClusters(response.data.clusters ?? []);
        if (!response.data.clusters?.length) {
          setClusterError("没有可分组的索引文件。请先在设置里加入授权目录并触发索引。");
        }
      } else {
        setClusters([]);
        setClusterError(response.data?.error || response.error?.message || "分组失败");
      }
    } catch (error) {
      setClusters([]);
      setClusterError(error instanceof Error ? error.message : "分组失败");
    } finally {
      setIsClustering(false);
    }
  };

  const parseDocument = async () => {
    if (!api || !selectedDocumentPath) return;
    setIsDocumentWorking(true);
    setDocumentError(null);
    try {
      const response = await api.parseDocument({ path: selectedDocumentPath, includeText: true });
      if (response.ok && response.data) {
        setDocumentResult(response.data);
        setDocumentAnswer(null);
      } else {
        setDocumentError(response.error?.message || "文档解析失败");
      }
    } catch (error) {
      setDocumentError(error instanceof Error ? error.message : "文档解析失败");
    } finally {
      setIsDocumentWorking(false);
    }
  };

  const askDocument = async () => {
    if (!api || !selectedDocumentPath || !documentQuestion.trim()) return;
    setIsDocumentWorking(true);
    setDocumentError(null);
    try {
      const response = await api.askDocument({ path: selectedDocumentPath, question: documentQuestion.trim() });
      if (response.ok && response.data) {
        setDocumentAnswer(response.data);
      } else {
        setDocumentError(response.error?.message || "文档问答失败");
      }
    } catch (error) {
      setDocumentError(error instanceof Error ? error.message : "文档问答失败");
    } finally {
      setIsDocumentWorking(false);
    }
  };

  const compareDocuments = async () => {
    if (!api || !selectedDocumentPath || !comparePath.trim()) return;
    setIsDocumentWorking(true);
    setDocumentError(null);
    try {
      const response = await api.compareDocuments({ paths: [selectedDocumentPath, comparePath.trim()] });
      if (response.ok && response.data) {
        setCompareResult(response.data);
      } else {
        setDocumentError(response.error?.message || "文档对比失败");
      }
    } catch (error) {
      setDocumentError(error instanceof Error ? error.message : "文档对比失败");
    } finally {
      setIsDocumentWorking(false);
    }
  };

  const scanCleanup = async () => {
    if (!api) return;
    setIsCleanupWorking(true);
    setCleanupError(null);
    try {
      const response = await api.scanCleanup({ thresholdMb: 100 });
      if (response.ok && response.data) {
        setCleanupPlan(response.data);
      } else {
        setCleanupError(response.error?.message || "清理扫描失败");
      }
    } catch (error) {
      setCleanupError(error instanceof Error ? error.message : "清理扫描失败");
    } finally {
      setIsCleanupWorking(false);
    }
  };

  return (
    <Panel
      title="文件搜索"
      eyebrow="工作区"
      action={<Badge tone="neutral">{results.length} 条结果</Badge>}
    >
      <div className="search-row">
        <div className="input-with-icon">
          <Search size={16} aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                void submit();
              }
            }}
            placeholder="搜索文件"
          />
        </div>
        <button className="button button--secondary" onClick={() => void submit()} disabled={isSearching}>
          <Search size={16} aria-hidden="true" />
          搜索
        </button>
        {api ? (
          <>
            <label className="cluster-dimension-picker" title={selectedClusterDimension.description}>
              <span>维度</span>
              <select
                aria-label="选择文件聚类维度"
                value={clusterDimension}
                onChange={(event) => setClusterDimension(event.target.value as FileClusterDimension)}
                disabled={isClustering}
              >
                {CLUSTER_DIMENSION_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="button button--ghost"
              onClick={() => void runCluster()}
              disabled={isClustering}
              title={selectedClusterDimension.description}
            >
              <Layers size={16} aria-hidden="true" />
              智能分组
            </button>
          </>
        ) : null}
      </div>
      <div className="file-results">
        {results.map((result) => (
          <article className="file-result" key={result.id}>
            <FileText size={16} aria-hidden="true" />
            <div>
              <div className="row row--between">
                <strong>{result.path}</strong>
                <span className="muted">第 {result.line} 行</span>
              </div>
              <p>{result.match}</p>
            </div>
          </article>
        ))}
      </div>
      {clusters.length || clusterError ? (
        <section className="file-cluster" style={{ marginTop: 12 }}>
          <div className="row row--between">
            <strong>智能分组</strong>
            <div className="row">
              <Badge tone="neutral">{resultClusterDimension.label}</Badge>
              <Badge tone="info">{clusters.length} 组</Badge>
            </div>
          </div>
          {clusterError ? <p className="muted">{clusterError}</p> : null}
          <ul className="file-cluster__list">
            {clusters.map((cluster) => (
              <li key={cluster.cluster_id}>
                <div className="row row--between">
                  <strong>{cluster.suggested_name || `分组 ${cluster.cluster_id}`}</strong>
                  <span className="muted">{cluster.size} 项</span>
                </div>
                {cluster.preview?.length ? (
                  <ul className="muted">
                    {cluster.preview.slice(0, 3).map((path) => (
                      <li key={path}>{path}</li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {api ? (
        <section className="file-tools">
          <div className="file-tool">
            <div className="file-tool__head">
              <div>
                <strong>文档</strong>
                <span className="muted">解析、引用问答、对比</span>
              </div>
              <Badge tone="neutral">{documentResult?.blocks.length ?? 0} 块</Badge>
            </div>
            <label className="field">
              <span>文档路径</span>
              <input
                value={documentPath}
                onChange={(event) => setDocumentPath(event.target.value)}
                placeholder={results[0]?.path || "输入或先搜索一个文档"}
              />
            </label>
            <div className="file-tool__actions">
              <button className="button button--secondary" onClick={() => void parseDocument()} disabled={isDocumentWorking || !selectedDocumentPath}>
                <FileText size={16} aria-hidden="true" />
                解析
              </button>
              <div className="input-with-icon">
                <FileQuestion size={16} aria-hidden="true" />
                <input
                  value={documentQuestion}
                  onChange={(event) => setDocumentQuestion(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void askDocument();
                  }}
                  placeholder="问这个文档"
                />
              </div>
              <button className="button button--ghost" onClick={() => void askDocument()} disabled={isDocumentWorking || !selectedDocumentPath || !documentQuestion.trim()}>
                提问
              </button>
            </div>
            <div className="file-tool__actions">
              <input
                className="plain-input"
                value={comparePath}
                onChange={(event) => setComparePath(event.target.value)}
                placeholder="对比另一个文档路径"
              />
              <button className="button button--ghost" onClick={() => void compareDocuments()} disabled={isDocumentWorking || !selectedDocumentPath || !comparePath.trim()}>
                对比
              </button>
            </div>
            {documentError ? <p className="field-error">{documentError}</p> : null}
            {documentResult ? <DocumentResultView document={documentResult} /> : null}
            {documentAnswer ? <DocumentAnswerView answer={documentAnswer} /> : null}
            {compareResult ? <DocumentCompareView result={compareResult} /> : null}
          </div>

          <div className="file-tool">
            <div className="file-tool__head">
              <div>
                <strong>清理预览</strong>
                <span className="muted">扫描后再决定，不会直接删除</span>
              </div>
              <Badge tone={cleanupBuckets.permanent.length ? "warning" : "neutral"}>
                {formatBytes(cleanupPlan?.reclaimableBytes)} 可释放
              </Badge>
            </div>
            <button className="button button--secondary" onClick={() => void scanCleanup()} disabled={isCleanupWorking}>
              <Trash2 size={16} aria-hidden="true" />
              扫描可清理项
            </button>
            {cleanupError ? <p className="field-error">{cleanupError}</p> : null}
            {cleanupPlan ? (
              <CleanupPlanPreview
                plan={cleanupPlan}
                permanent={cleanupBuckets.permanent}
                trash={cleanupBuckets.trash}
                suggestions={cleanupBuckets.suggestions}
              />
            ) : null}
          </div>
        </section>
      ) : null}
    </Panel>
  );
}

function DocumentResultView({ document }: { document: DocumentIR }) {
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

function DocumentAnswerView({ answer }: { answer: DocumentAskResponse }) {
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

function DocumentCompareView({ result }: { result: DocumentCompareResponse }) {
  return (
    <div className="document-preview">
      <strong>对比结果</strong>
      {result.summary ? <p>{result.summary}</p> : null}
      <ul>
        {result.differences.slice(0, 5).map((difference) => (
          <li key={difference.id}>
            <span className="muted">{difference.severity || "差异"}</span>
            <p><strong>{difference.title}</strong>：{difference.detail}</p>
          </li>
        ))}
      </ul>
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

function CleanupPlanPreview({
  plan,
  permanent,
  trash,
  suggestions
}: {
  plan: CleanupPlan;
  permanent: CleanupPlan["items"];
  trash: CleanupPlan["items"];
  suggestions: CleanupPlan["items"];
}) {
  return (
    <div className="cleanup-preview">
      <div className="cleanup-preview__metrics">
        <span><strong>{formatBytes(plan.reclaimableBytes)}</strong> 可释放</span>
        <span><strong>{permanent.length}</strong> 永久删除</span>
        <span><strong>{trash.length}</strong> 进回收站</span>
      </div>
      <CleanupBucket title="永久删除" tone="danger" items={permanent} emptyText="没有永久删除项" />
      <CleanupBucket title="进回收站" tone="warning" items={trash} emptyText="没有回收站项" />
      <CleanupBucket title="仅建议" tone="neutral" items={suggestions} emptyText="没有建议项" />
      {plan.riskWarnings.length ? (
        <ul className="cleanup-risk">
          {plan.riskWarnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function CleanupBucket({
  title,
  tone,
  items,
  emptyText
}: {
  title: string;
  tone: "neutral" | "warning" | "danger";
  items: CleanupPlan["items"];
  emptyText: string;
}) {
  return (
    <section className="cleanup-bucket">
      <div className="row row--between">
        <strong>{title}</strong>
        <Badge tone={tone}>{items.length} 项</Badge>
      </div>
      {items.length ? (
        <ul>
          {items.slice(0, 5).map((item) => (
            <li key={item.id}>
              <span>{item.path}</span>
              <em>{formatBytes(item.sizeBytes ?? (item.sizeMb ? item.sizeMb * 1024 * 1024 : undefined))}</em>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">{emptyText}</p>
      )}
    </section>
  );
}

function splitCleanupItems(plan: CleanupPlan | null) {
  const items = plan?.items ?? [];
  return {
    permanent: items.filter((item) => item.disposition === "permanent_delete"),
    trash: items.filter((item) => item.disposition === "trash"),
    suggestions: items.filter((item) => item.disposition !== "permanent_delete" && item.disposition !== "trash")
  };
}

function formatBytes(bytes?: number): string {
  if (!bytes || !Number.isFinite(bytes)) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}

function clusterDimensionOption(value: FileClusterDimension): FileClusterDimensionOption {
  return CLUSTER_DIMENSION_OPTIONS.find((option) => option.value === value) ?? CLUSTER_DIMENSION_OPTIONS[0];
}

function clusterPayloadFor(dimension: FileClusterDimension): FileClusterOptions {
  switch (dimension) {
    case "type":
      return { groupBy: "type", clusterBy: "type" };
    case "extension":
      return { groupBy: "extension", clusterBy: "extension" };
    case "image_auto":
      return { groupBy: "image", clusterBy: "auto" };
    case "scene":
    case "people":
    case "objects":
    case "tags":
    case "time":
    case "location":
      return { groupBy: dimension, clusterBy: dimension };
    case "content":
    default:
      return {};
  }
}
