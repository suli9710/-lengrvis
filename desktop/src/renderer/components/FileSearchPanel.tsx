import { FileText, Layers, Search } from "lucide-react";
import { useState } from "react";

import type { FileSearchResult } from "../../shared/types";
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

  const selectedClusterDimension = clusterDimensionOption(clusterDimension);
  const resultClusterDimension = clusterDimensionOption(clusterResultDimension);

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
    </Panel>
  );
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
