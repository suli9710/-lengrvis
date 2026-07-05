import { FolderOpen, Layers, Search } from "lucide-react";
import type { MutableRefObject } from "react";

import type { FileSearchMeta, FileSearchResult } from "../../../shared/fileLibraryTypes";
import type { BackendClusterEntry } from "../../lib/apiClient";
import { Badge } from "../Panel";
import {
  CLUSTER_DIMENSION_OPTIONS,
  type FileClusterDimension,
  type FileClusterDimensionOption,
  type ResultActionMessage,
  type ResultDocumentAction,
  type SearchNoticeTone,
  type SearchStatus
} from "./FileSearchModels";
import { FileSearchResults } from "./FileSearchResults";

interface FileSearchPaneProps {
  apiAvailable: boolean;
  clusterDimension: FileClusterDimension;
  clusterError: string | null;
  clusters: BackendClusterEntry[];
  currentScope: string;
  hasKnownFolderShortcuts: boolean;
  indexStatusNotice: { tone: SearchNoticeTone; text: string } | null;
  isClustering: boolean;
  isSavingScope: boolean;
  isSearching: boolean;
  query: string;
  resultActionMessage: ResultActionMessage | null;
  resultClusterDimension: FileClusterDimensionOption;
  resultDocumentAction: { path: string; action: ResultDocumentAction } | null;
  results: FileSearchResult[];
  revealingPath: string | null;
  searchButtonLabel: string;
  searchInputRef: MutableRefObject<HTMLInputElement | null>;
  searchMessage: string | null;
  searchMeta?: FileSearchMeta | null;
  searchNotice: { tone: SearchNoticeTone; text: string } | null;
  searchStatus: SearchStatus;
  selectedClusterDimension: FileClusterDimensionOption;
  onClusterDimensionChange: (dimension: FileClusterDimension) => void;
  onFocusScopePicker: () => void;
  onQueryChange: (query: string) => void;
  onRevealSearchResult: (path: string) => void;
  onRunCluster: () => void;
  onSubmit: () => void;
  onUseSearchResultAsDocument: (path: string, action: ResultDocumentAction) => void;
}

export function FileSearchPane({
  apiAvailable,
  clusterDimension,
  clusterError,
  clusters,
  currentScope,
  hasKnownFolderShortcuts,
  indexStatusNotice,
  isClustering,
  isSavingScope,
  isSearching,
  query,
  resultActionMessage,
  resultClusterDimension,
  resultDocumentAction,
  results,
  revealingPath,
  searchButtonLabel,
  searchInputRef,
  searchMessage,
  searchMeta,
  searchNotice,
  searchStatus,
  selectedClusterDimension,
  onClusterDimensionChange,
  onFocusScopePicker,
  onQueryChange,
  onRevealSearchResult,
  onRunCluster,
  onSubmit,
  onUseSearchResultAsDocument
}: FileSearchPaneProps) {
  return (
    <section className="file-tool-pane" aria-label="搜索文件" aria-busy={isSearching || isSavingScope}>
      {!currentScope ? (
        <div className="file-search-prereq" role="status">
          <div>
            <strong>先选要找的文件夹</strong>
            <p>从桌面、下载、文档、图片开始，或选择一个文件夹。选好后再输入关键词搜索。</p>
          </div>
          <button className="button button--secondary" type="button" onClick={onFocusScopePicker}>
            <FolderOpen size={16} aria-hidden="true" />
            去选择
          </button>
        </div>
      ) : null}
      <div className="search-row">
        <div className="input-with-icon">
          <Search size={16} aria-hidden="true" />
          <input
            ref={searchInputRef}
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") onSubmit();
            }}
            placeholder="搜索文件名、扩展名，或已索引文档内容"
          />
        </div>
        <button className="button button--secondary" onClick={onSubmit} disabled={isSearching || isSavingScope}>
          <Search size={16} aria-hidden="true" />
          {searchButtonLabel}
        </button>
        {apiAvailable ? (
          <>
            <label className="cluster-dimension-picker" title={selectedClusterDimension.description}>
              <span>分组方式</span>
              <select
                aria-label="选择文件分组方式"
                value={clusterDimension}
                onChange={(event) => onClusterDimensionChange(event.target.value as FileClusterDimension)}
                disabled={isClustering || isSavingScope}
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
              onClick={onRunCluster}
              disabled={isClustering || isSavingScope}
              title={selectedClusterDimension.description}
            >
              <Layers size={16} aria-hidden="true" />
              智能分组
            </button>
          </>
        ) : null}
      </div>
      {searchNotice ? (
        <p className={`file-status file-status--${searchNotice.tone}`} role={searchNotice.tone === "error" ? "alert" : "status"}>
          {searchNotice.text}
        </p>
      ) : null}
      {indexStatusNotice ? (
        <p className={`file-status file-status--${indexStatusNotice.tone}`} role="status">
          {indexStatusNotice.text}
        </p>
      ) : null}
      <div className="file-results">
        <FileSearchResults
          currentScope={currentScope}
          hasKnownFolderShortcuts={hasKnownFolderShortcuts}
          isSavingScope={isSavingScope}
          isSearching={isSearching}
          results={results}
          resultActionMessage={resultActionMessage}
          resultDocumentAction={resultDocumentAction}
          revealingPath={revealingPath}
          searchMessage={searchMessage}
          searchMeta={searchMeta}
          searchStatus={searchStatus}
          onRevealSearchResult={onRevealSearchResult}
          onUseSearchResultAsDocument={onUseSearchResultAsDocument}
        />
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
    </section>
  );
}
