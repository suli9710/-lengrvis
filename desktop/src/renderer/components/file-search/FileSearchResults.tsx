import {
  AlertCircle,
  BookOpenText,
  CheckCircle2,
  FileText,
  FolderOpen,
  Loader2,
  Sparkles
} from "lucide-react";

import type { FileSearchMeta, FileSearchResult } from "../../../shared/types";
import {
  displayFilePath,
  displaySearchMatch,
  formatCount,
  isDocumentPathSupported,
  type ResultActionMessage,
  type ResultDocumentAction,
  type SearchStatus
} from "./FileSearchModels";

interface FileSearchResultsProps {
  currentScope: string;
  hasKnownFolderShortcuts: boolean;
  isSavingScope: boolean;
  isSearching: boolean;
  results: FileSearchResult[];
  resultActionMessage: ResultActionMessage | null;
  resultDocumentAction: { path: string; action: ResultDocumentAction } | null;
  revealingPath: string | null;
  searchMessage: string | null;
  searchMeta?: FileSearchMeta | null;
  searchStatus: SearchStatus;
  onRevealSearchResult: (path: string) => void;
  onUseSearchResultAsDocument: (path: string, action: ResultDocumentAction) => void;
}

export function FileSearchResults({
  currentScope,
  hasKnownFolderShortcuts,
  isSavingScope,
  isSearching,
  results,
  resultActionMessage,
  resultDocumentAction,
  revealingPath,
  searchMessage,
  searchMeta,
  searchStatus,
  onRevealSearchResult,
  onUseSearchResultAsDocument
}: FileSearchResultsProps) {
  if (searchStatus === "loading" || isSearching) {
    return <p className="empty-state">正在查找当前范围里的匹配文件...</p>;
  }
  if (isSavingScope) {
    return (
      <div className="empty-state file-empty-guide">
        <strong>正在切换范围</strong>
        <p>切换完成后再输入关键词搜索，结果只会来自当前范围。</p>
      </div>
    );
  }
  if (searchStatus === "success" && results.length) {
    return (
      <>
        {results.map((result, index) => (
          <FileSearchResultCard
            key={`${result.id}-${result.path}-${result.line}-${index}`}
            result={result}
            resultActionMessage={resultActionMessage}
            resultDocumentAction={resultDocumentAction}
            revealingPath={revealingPath}
            onRevealSearchResult={onRevealSearchResult}
            onUseSearchResultAsDocument={onUseSearchResultAsDocument}
          />
        ))}
      </>
    );
  }
  if (searchStatus === "empty") {
    return (
      <div className="empty-state file-empty-guide">
        <strong>{searchMeta?.truncated ? "没有找到完整结果" : "没有找到结果"}</strong>
        <p>
          {searchMeta?.truncated
            ? `已检查 ${formatCount(searchMeta.scanned)} 个文件，但当前范围还没扫完。可以缩小范围，或换一个更具体的关键词再试。`
            : hasKnownFolderShortcuts
              ? "换个关键词，或切换到桌面、下载、文档、图片再试。"
              : "换个关键词，或点“选择要查找的文件夹”/粘贴路径后再试。"}
        </p>
      </div>
    );
  }
  if (searchStatus === "error") {
    return (
      <div className="empty-state file-empty-guide">
        <strong>这次搜索未完成</strong>
        <p>{searchMessage || "文件搜索失败，请稍后重试。"}</p>
        <p>可以换一个小一点的范围，或只搜文件名、扩展名再试。</p>
      </div>
    );
  }
  if (searchStatus === "missing_scope") {
    return (
      <div className="empty-state file-empty-guide">
        <strong>还没有选择要查找的文件夹</strong>
        <p>先点“选择要查找的文件夹”，或从桌面、下载、文档、图片里选一个位置。</p>
      </div>
    );
  }
  if (searchStatus === "missing_query") {
    return (
      <div className="empty-state file-empty-guide">
        <strong>还没有输入关键词</strong>
        <p>输入文件名、扩展名或内容关键词后再搜索。</p>
      </div>
    );
  }
  return (
    <div className="empty-state file-empty-guide">
      <strong>{currentScope ? "已选择文件夹，输入关键词开始搜索" : "先选择文件夹，再输入关键词"}</strong>
      <p>
        {currentScope
          ? "输入文件名、扩展名或内容关键词后，Lengrvis 只会在当前范围里查找。"
          : "可以从桌面、下载、文档、图片开始，也可以点“选择要查找的文件夹”指定位置。"}
      </p>
      <p>搜索、分组和清理只会查看当前范围；移动、重命名或删除前都会再次确认。</p>
    </div>
  );
}

function FileSearchResultCard({
  result,
  resultActionMessage,
  resultDocumentAction,
  revealingPath,
  onRevealSearchResult,
  onUseSearchResultAsDocument
}: {
  result: FileSearchResult;
  resultActionMessage: ResultActionMessage | null;
  resultDocumentAction: { path: string; action: ResultDocumentAction } | null;
  revealingPath: string | null;
  onRevealSearchResult: (path: string) => void;
  onUseSearchResultAsDocument: (path: string, action: ResultDocumentAction) => void;
}) {
  const pathParts = displayFilePath(result.path);
  const matchText = displaySearchMatch(result.match, pathParts.name, result.path);
  const documentSupported = isDocumentPathSupported(result.path);
  const reading = resultDocumentAction?.path === result.path && resultDocumentAction.action === "read";
  const summarizing = resultDocumentAction?.path === result.path && resultDocumentAction.action === "summarize";
  const revealing = revealingPath === result.path;
  const busy = Boolean(resultDocumentAction || revealingPath);

  return (
    <article className="file-result">
      <FileText size={16} aria-hidden="true" />
      <div className="file-result__body">
        <div className="file-result__head">
          <div className="file-result__title" title={result.path}>
            <strong>{pathParts.name}</strong>
            {pathParts.parent ? <span>{pathParts.parent}</span> : null}
          </div>
          <span className="file-result__meta">{result.line > 1 ? `第 ${result.line} 行` : "文件名匹配"}</span>
        </div>
        {matchText ? <p>{matchText}</p> : null}
        <div className="file-result__actions" aria-label={`${pathParts.name} 的操作`}>
          {documentSupported ? (
            <>
              <button
                className="file-result-action file-result-action--read"
                type="button"
                data-loading={reading ? "true" : undefined}
                onClick={() => onUseSearchResultAsDocument(result.path, "read")}
                disabled={busy}
              >
                {reading ? <Loader2 size={14} aria-hidden="true" /> : <BookOpenText size={14} aria-hidden="true" />}
                {reading ? "读取中" : "读取"}
              </button>
              <button
                className="file-result-action file-result-action--summarize"
                type="button"
                data-loading={summarizing ? "true" : undefined}
                onClick={() => onUseSearchResultAsDocument(result.path, "summarize")}
                disabled={busy}
              >
                {summarizing ? <Loader2 size={14} aria-hidden="true" /> : <Sparkles size={14} aria-hidden="true" />}
                {summarizing ? "总结中" : "总结"}
              </button>
            </>
          ) : (
            <span className="file-result__unsupported" title="这个格式暂不支持文档读取或总结">
              不支持读取/总结
            </span>
          )}
          <button
            className="file-result-action file-result-action--reveal"
            type="button"
            data-loading={revealing ? "true" : undefined}
            onClick={() => onRevealSearchResult(result.path)}
            disabled={Boolean(resultDocumentAction) || revealing}
          >
            {revealing ? <Loader2 size={14} aria-hidden="true" /> : <FolderOpen size={14} aria-hidden="true" />}
            {revealing ? "打开中" : "打开位置"}
          </button>
        </div>
        {resultActionMessage?.path === result.path ? (
          <p
            className={`file-action-hint file-action-hint--${resultActionMessage.tone}`}
            role={resultActionMessage.tone === "error" ? "alert" : "status"}
          >
            <ResultActionIcon tone={resultActionMessage.tone} />
            {resultActionMessage.text}
          </p>
        ) : null}
      </div>
    </article>
  );
}

function ResultActionIcon({ tone }: { tone: ResultActionMessage["tone"] }) {
  if (tone === "success") return <CheckCircle2 size={14} aria-hidden="true" />;
  if (tone === "error") return <AlertCircle size={14} aria-hidden="true" />;
  return <Loader2 size={14} aria-hidden="true" />;
}
