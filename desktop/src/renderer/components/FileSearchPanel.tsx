import { FileText, Layers, Search } from "lucide-react";
import { useState } from "react";

import type { FileSearchResult } from "../../shared/types";
import type { BackendClusterEntry, MavrisApiClient } from "../lib/apiClient";
import { Badge, Panel } from "./Panel";

interface FileSearchPanelProps {
  results: FileSearchResult[];
  isSearching: boolean;
  onSearch: (query: string) => Promise<void>;
  api?: MavrisApiClient;
}

export function FileSearchPanel({ results, isSearching, onSearch, api }: FileSearchPanelProps) {
  const [query, setQuery] = useState("");
  const [clusters, setClusters] = useState<BackendClusterEntry[]>([]);
  const [isClustering, setIsClustering] = useState(false);
  const [clusterError, setClusterError] = useState<string | null>(null);

  const submit = async () => {
    await onSearch(query.trim());
  };

  const runCluster = async () => {
    if (!api) return;
    setIsClustering(true);
    setClusterError(null);
    const response = await api.clusterFiles({});
    setIsClustering(false);
    if (response.ok && response.data?.ok) {
      setClusters(response.data.clusters ?? []);
      if (!response.data.clusters?.length) {
        setClusterError("没有可分组的索引文件。请先在设置里加入授权目录并触发索引。");
      }
    } else {
      setClusters([]);
      setClusterError(response.data?.error || response.error?.message || "分组失败");
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
          <button
            className="button button--ghost"
            onClick={() => void runCluster()}
            disabled={isClustering}
            title="按文件名/扩展名对索引内容做轻量聚类"
          >
            <Layers size={16} aria-hidden="true" />
            智能分组
          </button>
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
            <Badge tone="info">{clusters.length} 组</Badge>
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
