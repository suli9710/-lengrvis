import { useCallback, useEffect, useRef, useState } from "react";

import type { DocumentAskResponse, DocumentCompareResponse, DocumentIR } from "../../../shared/documentTypes";
import type { LengrvisApiClient } from "../../lib/apiClient";
import { motionAwareScrollBehavior } from "../../lib/motion";
import {
  DEFAULT_SUMMARY_QUESTION,
  fileActionError,
  userFileError,
  validateDocumentPath
} from "./FileSearchModels";
import type { DocumentWorkingAction, FileDocumentPaneProps } from "./FileDocumentPane";

// Owns the file tool's document workspace orchestration: parsing, QA, compare, picker, and intent state.
export type DocumentIntentAction = "read" | "summarize" | "ask";

export type DocumentOperationResult = {
  ok: boolean;
  error?: string;
};

type FileDocumentPaneWorkspaceProps = Omit<FileDocumentPaneProps, "results" | "serviceUnavailable" | "onSelectTool">;

interface FileDocumentWorkspaceOptions {
  api?: LengrvisApiClient;
  ensureDocumentScopes: (filePaths: string[]) => Promise<boolean>;
  selectedDocumentPath?: string;
  selectedDocumentAction?: DocumentIntentAction;
  selectedDocumentQuestion?: string;
  selectedDocumentIntentId?: number;
  onDocumentIntentHandled?: () => void;
  onSelectDocumentTool: () => void;
}

interface FileDocumentWorkspace {
  selectedDocumentPathValue: string;
  documentResult: DocumentIR | null;
  clearDocumentOutput: () => void;
  readDocument: (forcedPath?: string, notice?: string) => Promise<DocumentOperationResult>;
  askDocument: (forcedQuestion?: string, forcedPath?: string, notice?: string) => Promise<DocumentOperationResult>;
  summarizeDocument: (forcedPath?: string) => Promise<DocumentOperationResult>;
  setDocumentPath: (path: string) => void;
  setDocumentQuestion: (question: string) => void;
  scrollIntoView: () => void;
  paneProps: FileDocumentPaneWorkspaceProps;
}

export function questionForDocumentIntent(action: DocumentIntentAction, selectedQuestion?: string): string {
  return selectedQuestion ?? (action === "summarize" ? DEFAULT_SUMMARY_QUESTION : "");
}

export function documentWorkingActionForQuestion(question: string): DocumentWorkingAction {
  return question === DEFAULT_SUMMARY_QUESTION ? "summarize" : "ask";
}

export function compareDocumentValidationError(primaryPath: string, comparePath: string): string | null {
  const currentValidation = validateDocumentPath(primaryPath);
  const compareValidation = validateDocumentPath(comparePath);
  if (!currentValidation && !compareValidation) return null;
  return currentValidation ? `第一份文档：${currentValidation}` : `第二份文档：${compareValidation}`;
}

export function useFileDocumentWorkspace({
  api,
  ensureDocumentScopes,
  selectedDocumentPath,
  selectedDocumentAction,
  selectedDocumentQuestion,
  selectedDocumentIntentId,
  onDocumentIntentHandled,
  onSelectDocumentTool
}: FileDocumentWorkspaceOptions): FileDocumentWorkspace {
  const [documentPath, setDocumentPath] = useState("");
  const [documentQuestion, setDocumentQuestion] = useState("");
  const [comparePath, setComparePath] = useState("");
  const [documentResult, setDocumentResult] = useState<DocumentIR | null>(null);
  const [documentAnswer, setDocumentAnswer] = useState<DocumentAskResponse | null>(null);
  const [compareResult, setCompareResult] = useState<DocumentCompareResponse | null>(null);
  const [documentNotice, setDocumentNotice] = useState<string | null>(null);
  const [documentError, setDocumentError] = useState<string | null>(null);
  const [isDocumentWorking, setIsDocumentWorking] = useState(false);
  const [documentWorkingAction, setDocumentWorkingAction] = useState<DocumentWorkingAction | null>(null);
  const documentPaneRef = useRef<HTMLElement | null>(null);
  const documentPathInputRef = useRef<HTMLInputElement | null>(null);
  const comparePathInputRef = useRef<HTMLInputElement | null>(null);
  const documentQuestionInputRef = useRef<HTMLInputElement | null>(null);
  const handledDocumentIntentId = useRef<number | undefined>(undefined);

  const selectedDocumentPathValue = documentPath.trim();
  const compareDocumentPathValue = comparePath.trim();

  const ensureDocumentScope = useCallback(
    async (filePath: string): Promise<boolean> => ensureDocumentScopes([filePath]),
    [ensureDocumentScopes]
  );

  const clearDocumentOutput = useCallback(() => {
    setDocumentResult(null);
    setDocumentAnswer(null);
    setCompareResult(null);
    setDocumentNotice(null);
    setDocumentError(null);
  }, []);

  const scrollIntoView = useCallback(() => {
    window.setTimeout(() => documentPaneRef.current?.scrollIntoView({ block: "start", behavior: motionAwareScrollBehavior() }), 0);
  }, []);

  const readDocument = useCallback(async (forcedPath?: string, notice?: string): Promise<DocumentOperationResult> => {
    const path = (forcedPath ?? selectedDocumentPathValue).trim();
    if (!api || !path) return { ok: false };
    const validationMessage = validateDocumentPath(path);
    if (validationMessage) {
      setDocumentNotice(null);
      setDocumentError(validationMessage);
      return { ok: false, error: validationMessage };
    }
    const scopeReady = await ensureDocumentScope(path);
    if (!scopeReady) {
      const message = "当前范围没有保存成功。请先选择这个文档所在文件夹，再读取文档。";
      setDocumentError(message);
      return { ok: false, error: message };
    }
    setIsDocumentWorking(true);
    setDocumentWorkingAction("read");
    setDocumentNotice(notice ?? null);
    setDocumentError(null);
    try {
      const response = await api.parseDocument({ path, includeText: true });
      if (response.ok && response.data) {
        setDocumentResult(response.data);
        setDocumentAnswer(null);
        setDocumentNotice("已读取，可查看下方预览。");
        return { ok: true };
      }
      const message = fileActionError(response.error?.message, "read");
      setDocumentNotice(null);
      setDocumentError(message);
      return { ok: false, error: message };
    } catch (error) {
      const message = fileActionError(error, "read");
      setDocumentNotice(null);
      setDocumentError(message);
      return { ok: false, error: message };
    } finally {
      setIsDocumentWorking(false);
      setDocumentWorkingAction(null);
    }
  }, [api, ensureDocumentScope, selectedDocumentPathValue]);

  const askDocument = useCallback(async (
    forcedQuestion?: string,
    forcedPath?: string,
    notice?: string
  ): Promise<DocumentOperationResult> => {
    const question = forcedQuestion ?? documentQuestion.trim();
    const path = (forcedPath ?? selectedDocumentPathValue).trim();
    if (!api || !path || !question) return { ok: false };
    const validationMessage = validateDocumentPath(path);
    if (validationMessage) {
      setDocumentNotice(null);
      setDocumentError(validationMessage);
      return { ok: false, error: validationMessage };
    }
    const scopeReady = await ensureDocumentScope(path);
    if (!scopeReady) {
      const message = "当前范围没有保存成功。请先选择这个文档所在文件夹，再总结或提问。";
      setDocumentError(message);
      return { ok: false, error: message };
    }
    const nextWorkingAction = documentWorkingActionForQuestion(question);
    setIsDocumentWorking(true);
    setDocumentWorkingAction(nextWorkingAction);
    setDocumentNotice(notice ?? null);
    setDocumentError(null);
    try {
      const response = await api.askDocument({ path, question });
      if (response.ok && response.data) {
        setDocumentAnswer(response.data);
        setDocumentNotice(question === DEFAULT_SUMMARY_QUESTION ? "已生成总结，可以继续追问。" : "已生成回答，可以继续追问。");
        return { ok: true };
      }
      const message = fileActionError(response.error?.message, "summarize");
      setDocumentNotice(null);
      setDocumentError(message);
      return { ok: false, error: message };
    } catch (error) {
      const message = fileActionError(error, "summarize");
      setDocumentNotice(null);
      setDocumentError(message);
      return { ok: false, error: message };
    } finally {
      setIsDocumentWorking(false);
      setDocumentWorkingAction(null);
    }
  }, [api, documentQuestion, ensureDocumentScope, selectedDocumentPathValue]);

  const summarizeDocument = useCallback(async (forcedPath?: string): Promise<DocumentOperationResult> => {
    const path = (forcedPath ?? selectedDocumentPathValue).trim();
    if (!path) return { ok: false };
    setDocumentQuestion(DEFAULT_SUMMARY_QUESTION);
    return askDocument(DEFAULT_SUMMARY_QUESTION, path, "正在总结这份文档...");
  }, [askDocument, selectedDocumentPathValue]);

  const chooseDocument = useCallback(async (action: "select" | "summarize" = "select") => {
    const documentChooser = window.lengrvis?.dialog.chooseDocument;
    if (!documentChooser) {
      setDocumentError(null);
      setDocumentNotice("当前环境不能打开文档选择器。可以直接粘贴完整文件路径。");
      window.setTimeout(() => documentPathInputRef.current?.focus(), 0);
      return;
    }
    setDocumentError(null);
    setDocumentNotice(action === "summarize" ? "请选择要总结的文档..." : "请选择要读取或提问的文档...");
    const picked = await documentChooser();
    if (!picked) {
      setDocumentNotice("已取消选择，当前文档没有改变。");
      return;
    }
    setDocumentPath(picked);
    clearDocumentOutput();
    if (action === "summarize") {
      await summarizeDocument(picked);
      return;
    }
    const scopeReady = await ensureDocumentScope(picked);
    if (!scopeReady) {
      setDocumentError("已选中文档，但当前范围保存失败。请手动选择文档所在文件夹后再继续。");
      return;
    }
    setDocumentNotice("已选中文档。可以读取、总结，或输入问题后提问。");
  }, [clearDocumentOutput, ensureDocumentScope, summarizeDocument]);

  const chooseCompareDocument = useCallback(async () => {
    const documentChooser = window.lengrvis?.dialog.chooseDocument;
    if (!documentChooser) {
      setDocumentError(null);
      setDocumentNotice("这里暂时不能弹出文档窗口。可以先把第二份文档的完整位置粘贴到下方。");
      window.setTimeout(() => comparePathInputRef.current?.focus(), 0);
      return;
    }
    setDocumentError(null);
    setDocumentNotice("请选择要对比的第二份文档...");
    const picked = await documentChooser();
    if (!picked) {
      setDocumentNotice("已取消选择，第二份文档没有改变。");
      return;
    }
    setComparePath(picked);
    setCompareResult(null);
    setDocumentNotice("已选中第二份文档。确认第一份文档后，点“对比”查看差异。");
  }, []);

  const compareDocuments = useCallback(async () => {
    if (!api || !selectedDocumentPathValue || !compareDocumentPathValue) return;
    const validationError = compareDocumentValidationError(selectedDocumentPathValue, compareDocumentPathValue);
    if (validationError) {
      setDocumentNotice(null);
      setDocumentError(validationError);
      return;
    }
    const scopeReady = await ensureDocumentScopes([selectedDocumentPathValue, compareDocumentPathValue]);
    if (!scopeReady) {
      setDocumentError("两份文档的所在文件夹没有保存成功。请手动选择文档所在文件夹后再继续。");
      return;
    }
    setIsDocumentWorking(true);
    setDocumentWorkingAction("compare");
    setDocumentNotice("正在分析两份文档差异...");
    setDocumentError(null);
    try {
      const response = await api.compareDocuments({ paths: [selectedDocumentPathValue, compareDocumentPathValue] });
      if (response.ok && response.data) {
        setCompareResult(response.data);
        setDocumentNotice(response.data.differences.length ? "已完成对比，请查看下方差异。" : "已完成对比，没有发现明显差异。");
      } else {
        setDocumentError(userFileError(response.error?.message, "文档对比失败，请确认两个文件都能打开。"));
      }
    } catch (error) {
      setDocumentError(userFileError(error, "文档对比失败，请确认两个文件都能打开。"));
    } finally {
      setIsDocumentWorking(false);
      setDocumentWorkingAction(null);
    }
  }, [api, compareDocumentPathValue, ensureDocumentScopes, selectedDocumentPathValue]);

  useEffect(() => {
    if (!selectedDocumentPath || selectedDocumentIntentId === undefined) return;
    if (handledDocumentIntentId.current === selectedDocumentIntentId) return;
    handledDocumentIntentId.current = selectedDocumentIntentId;

    const action = selectedDocumentAction ?? "ask";
    const question = questionForDocumentIntent(action, selectedDocumentQuestion);
    onSelectDocumentTool();
    setDocumentPath(selectedDocumentPath);
    setDocumentQuestion(question);
    clearDocumentOutput();

    if (action === "read") {
      void readDocument(selectedDocumentPath, "正在读取这份文档...");
    } else if (action === "summarize") {
      void askDocument(question || DEFAULT_SUMMARY_QUESTION, selectedDocumentPath, "正在总结这份文档...");
    } else {
      setDocumentNotice("正在向这份文档提问：请在“问这个文档”输入框里写问题。");
      window.setTimeout(() => documentQuestionInputRef.current?.focus(), 0);
    }

    onDocumentIntentHandled?.();
  }, [
    askDocument,
    clearDocumentOutput,
    onDocumentIntentHandled,
    onSelectDocumentTool,
    readDocument,
    selectedDocumentAction,
    selectedDocumentIntentId,
    selectedDocumentPath,
    selectedDocumentQuestion
  ]);

  const setComparePathAndReset = useCallback((path: string) => {
    setComparePath(path);
    setCompareResult(null);
  }, []);

  return {
    selectedDocumentPathValue,
    documentResult,
    clearDocumentOutput,
    readDocument,
    askDocument,
    summarizeDocument,
    setDocumentPath,
    setDocumentQuestion,
    scrollIntoView,
    paneProps: {
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
      selectedDocumentPathValue,
      onAskDocument: () => void askDocument(undefined, undefined, "正在查找文档里的答案..."),
      onChooseCompareDocument: () => void chooseCompareDocument(),
      onChooseDocument: (action) => void chooseDocument(action),
      onCompareDocuments: () => void compareDocuments(),
      onComparePathChange: setComparePathAndReset,
      onDocumentPathChange: setDocumentPath,
      onDocumentQuestionChange: setDocumentQuestion,
      onReadDocument: () => void readDocument(undefined, "正在读取这份文档..."),
      onSummarizeDocument: () => void summarizeDocument()
    }
  };
}
