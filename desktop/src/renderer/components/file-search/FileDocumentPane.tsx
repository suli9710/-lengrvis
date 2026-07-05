import {
  BookOpenText,
  FileQuestion,
  FileText,
  GitCompare,
  Loader2,
  MousePointerClick,
  Route,
  Search,
  Sparkles
} from "lucide-react";
import type { MutableRefObject } from "react";

import type { DocumentAskResponse, DocumentCompareResponse, DocumentIR } from "../../../shared/documentTypes";
import type { FileSearchResult } from "../../../shared/fileLibraryTypes";
import { Badge } from "../Panel";
import { DocumentAnswerView, DocumentCompareView, DocumentResultView } from "./FileDocumentViews";
import { displayFilePath, type FileToolTabValue } from "./FileSearchModels";

export type DocumentWorkingAction = "read" | "summarize" | "ask" | "compare";

export interface FileDocumentPaneProps {
  compareDocumentPathValue: string;
  comparePath: string;
  comparePathInputRef: MutableRefObject<HTMLInputElement | null>;
  compareResult: DocumentCompareResponse | null;
  documentAnswer: DocumentAskResponse | null;
  documentError: string | null;
  documentNotice: string | null;
  documentPaneRef: MutableRefObject<HTMLElement | null>;
  documentPath: string;
  documentPathInputRef: MutableRefObject<HTMLInputElement | null>;
  documentQuestion: string;
  documentQuestionInputRef: MutableRefObject<HTMLInputElement | null>;
  documentResult: DocumentIR | null;
  documentWorkingAction: DocumentWorkingAction | null;
  isDocumentWorking: boolean;
  results: FileSearchResult[];
  selectedDocumentPathValue: string;
  serviceUnavailable: boolean;
  onAskDocument: () => void;
  onChooseCompareDocument: () => void;
  onChooseDocument: (action: "select" | "summarize") => void;
  onCompareDocuments: () => void;
  onComparePathChange: (path: string) => void;
  onDocumentPathChange: (path: string) => void;
  onDocumentQuestionChange: (question: string) => void;
  onReadDocument: () => void;
  onSelectTool: (tool: FileToolTabValue) => void;
  onSummarizeDocument: () => void;
}

export function FileDocumentPane({
  compareDocumentPathValue,
  comparePath,
  comparePathInputRef,
  compareResult,
  documentAnswer,
  documentError,
  documentNotice,
  documentPaneRef,
  documentPath,
  documentPathInputRef,
  documentQuestion,
  documentQuestionInputRef,
  documentResult,
  documentWorkingAction,
  isDocumentWorking,
  results,
  selectedDocumentPathValue,
  serviceUnavailable,
  onAskDocument,
  onChooseCompareDocument,
  onChooseDocument,
  onCompareDocuments,
  onComparePathChange,
  onDocumentPathChange,
  onDocumentQuestionChange,
  onReadDocument,
  onSelectTool,
  onSummarizeDocument
}: FileDocumentPaneProps) {
  const selectedDocumentPathParts = selectedDocumentPathValue ? displayFilePath(selectedDocumentPathValue) : null;
  const selectedDocument = documentPath.trim() || documentResult?.title || "";

  return (
    <section className="file-tool-pane" aria-label="文档操作区" aria-busy={isDocumentWorking} ref={documentPaneRef}>
      <div className="file-tool">
        <div className="file-tool__head">
          <div>
            <strong>文档操作区</strong>
            <span className="muted">选中文档后，可以读取预览、总结这份文档，或继续追问</span>
          </div>
          <Badge tone={selectedDocumentPathValue ? "info" : "neutral"}>{selectedDocumentPathValue ? "已选文档" : "未选文档"}</Badge>
        </div>
        {serviceUnavailable ? (
          <p className="file-status file-status--info">现在服务还没连上。你可以先粘贴文档路径；连接恢复后再读取预览、总结或提问。</p>
        ) : null}
        {!selectedDocumentPathValue ? (
          <div className="document-start-hero">
            <div>
              <strong>先选择一份文档</strong>
              <p>直接选择文件即可开始；Lengrvis 会把它所在文件夹设为当前范围，只读取这份文档。</p>
            </div>
            <div className="document-start-hero__actions">
              <button className="button button--primary" type="button" onClick={() => onChooseDocument("select")}>
                <MousePointerClick size={16} aria-hidden="true" />
                选择文档
              </button>
              <button className="button button--secondary" type="button" onClick={() => onChooseDocument("summarize")}>
                <FileQuestion size={16} aria-hidden="true" />
                选择并总结
              </button>
              <button className="button button--secondary" type="button" onClick={() => onSelectTool("search")}>
                <Search size={16} aria-hidden="true" />
                去搜索文档
              </button>
              <button className="button button--ghost" type="button" onClick={() => documentPathInputRef.current?.focus()}>
                <FileText size={16} aria-hidden="true" />
                粘贴路径
              </button>
            </div>
          </div>
        ) : null}
        <div className="document-start-guide">
          <div className={selectedDocumentPathValue ? "document-step document-step--done" : "document-step document-step--active"}>
            <span>1</span>
            <div>
              <strong>选择文档</strong>
              <p>{selectedDocumentPathValue ? "已选中，可以继续操作。" : "点“选择文档”最直接，也可以粘贴完整文件路径。"}</p>
            </div>
          </div>
          <div className={documentResult ? "document-step document-step--done" : selectedDocumentPathValue ? "document-step document-step--active" : "document-step"}>
            <span>2</span>
            <div>
              <strong>读取内容</strong>
              <p>{documentResult ? "内容已读取，可查看预览。" : "点击读取，先确认 Lengrvis 能打开这份文档。"}</p>
            </div>
          </div>
          <div className={documentAnswer ? "document-step document-step--done" : selectedDocumentPathValue ? "document-step document-step--active" : "document-step"}>
            <span>3</span>
            <div>
              <strong>总结或提问</strong>
              <p>{documentAnswer ? "已生成回答，可以继续追问。" : "总结会自动开始；提问需要先输入问题。"}</p>
            </div>
          </div>
        </div>
        <label className="field">
          <span>文档位置</span>
          <div className="document-path-row">
            <input
              ref={documentPathInputRef}
              value={documentPath}
              onChange={(event) => onDocumentPathChange(event.target.value)}
              placeholder={results[0]?.path || "选择文档，或粘贴文件位置"}
            />
            <button className="button button--ghost" type="button" onClick={() => onChooseDocument("select")}>
              <MousePointerClick size={16} aria-hidden="true" />
              选择文档
            </button>
          </div>
        </label>
        {!selectedDocumentPathValue ? (
          <p className="file-status file-status--info">当前还没有选中文档。点“选择文档”会同步当前范围；粘贴路径后，读取或总结前也会尝试同步所在文件夹。</p>
        ) : (
          <div className="file-status file-status--success document-current-file" role="status">
            <Route size={14} aria-hidden="true" />
            <span>当前文档</span>
            <strong title={selectedDocumentPathValue}>{selectedDocumentPathParts?.name || selectedDocument || selectedDocumentPathValue}</strong>
            {selectedDocumentPathParts?.parent ? <code title={selectedDocumentPathValue}>{selectedDocumentPathParts.parent}</code> : null}
          </div>
        )}
        <div className="file-tool__actions">
          <button
            className="button button--secondary document-action-button document-action-button--read"
            type="button"
            data-loading={documentWorkingAction === "read" ? "true" : undefined}
            onClick={onReadDocument}
            disabled={isDocumentWorking || !selectedDocumentPathValue}
          >
            {documentWorkingAction === "read" ? <Loader2 size={16} aria-hidden="true" /> : <BookOpenText size={16} aria-hidden="true" />}
            {documentWorkingAction === "read" ? "读取中" : "读取预览"}
          </button>
          <button
            className="button button--secondary document-action-button document-action-button--summarize"
            type="button"
            data-loading={documentWorkingAction === "summarize" ? "true" : undefined}
            onClick={onSummarizeDocument}
            disabled={isDocumentWorking || !selectedDocumentPathValue}
          >
            {documentWorkingAction === "summarize" ? <Loader2 size={16} aria-hidden="true" /> : <Sparkles size={16} aria-hidden="true" />}
            {documentWorkingAction === "summarize" ? "总结中" : "总结这份文档"}
          </button>
          <div className="input-with-icon">
            <FileQuestion size={16} aria-hidden="true" />
            <input
              ref={documentQuestionInputRef}
              value={documentQuestion}
              onChange={(event) => onDocumentQuestionChange(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") onAskDocument();
              }}
              placeholder="问这个文档"
            />
          </div>
          <button
            className="button button--ghost document-action-button document-action-button--ask"
            type="button"
            data-loading={documentWorkingAction === "ask" ? "true" : undefined}
            onClick={onAskDocument}
            disabled={isDocumentWorking || !selectedDocumentPathValue || !documentQuestion.trim()}
          >
            {documentWorkingAction === "ask" ? <Loader2 size={16} aria-hidden="true" /> : <FileQuestion size={16} aria-hidden="true" />}
            {documentWorkingAction === "ask" ? "提问中" : "向这份文档提问"}
          </button>
        </div>
        {!selectedDocumentPathValue ? (
          <p className="file-action-hint">选择或粘贴文档后，“读取预览 / 总结这份文档 / 向这份文档提问”会按顺序变为可用。</p>
        ) : !documentQuestion.trim() ? (
          <p className="file-action-hint file-action-hint--info">
            <FileQuestion size={14} aria-hidden="true" />
            <span>想追问这份文档时，先在“问这个文档”输入框里写问题，再点“提问”。</span>
          </p>
        ) : (
          <p className="file-action-hint">按 Enter 可直接提问。</p>
        )}
        <div className="document-compare-box" aria-label="对比两份文档">
          <div className="document-compare-box__head">
            <div>
              <strong>对比两份文档</strong>
              <span>第一份使用当前文档，第二份可以选择或粘贴路径。</span>
            </div>
            <Badge tone={compareDocumentPathValue ? "info" : "neutral"}>{compareDocumentPathValue ? "第二份已选" : "可选"}</Badge>
          </div>
          <div className="file-tool__actions file-tool__actions--compare">
            <input
              ref={comparePathInputRef}
              className="plain-input"
              value={comparePath}
              onChange={(event) => onComparePathChange(event.target.value)}
              placeholder="第二份文档位置"
              aria-label="第二份文档位置"
            />
            <button className="button button--ghost" type="button" onClick={onChooseCompareDocument} disabled={isDocumentWorking}>
              <MousePointerClick size={16} aria-hidden="true" />
              选择第二份
            </button>
            <button
              className="button button--ghost document-action-button document-action-button--compare"
              type="button"
              data-loading={documentWorkingAction === "compare" ? "true" : undefined}
              onClick={onCompareDocuments}
              disabled={isDocumentWorking || !selectedDocumentPathValue || !compareDocumentPathValue}
            >
              {documentWorkingAction === "compare" ? <Loader2 size={16} aria-hidden="true" /> : <GitCompare size={16} aria-hidden="true" />}
              {documentWorkingAction === "compare" ? "对比中" : "对比"}
            </button>
          </div>
          {!selectedDocumentPathValue ? (
            <p className="file-action-hint">先选择第一份文档，再选择第二份文档进行对比。</p>
          ) : !compareDocumentPathValue ? (
            <p className="file-action-hint">选择或粘贴第二份文档后，“对比”会变为可用。</p>
          ) : (
            <p className="file-action-hint">对比只读取这两份文档，不会修改文件。</p>
          )}
        </div>
        {documentError ? <p className="field-error" role="alert">{documentError}</p> : null}
        {documentNotice && !documentError ? <p className="file-status file-status--info" role="status">{documentNotice}</p> : null}
        {documentResult ? <DocumentResultView document={documentResult} /> : null}
        {documentAnswer ? <DocumentAnswerView answer={documentAnswer} /> : null}
        {compareResult ? <DocumentCompareView result={compareResult} /> : null}
      </div>
    </section>
  );
}
