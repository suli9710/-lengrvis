import {
  BookOpenText,
  Download,
  FileQuestion,
  FileText,
  FolderOpen,
  GitCompare,
  Image as ImageIcon,
  Layers,
  Loader2,
  MousePointerClick,
  Route,
  Search,
  Sparkles,
  Trash2,
  type LucideIcon
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  AppSettings,
  DocumentAskResponse,
  DocumentCompareResponse,
  DocumentIR,
  FileSearchMeta,
  FileSearchResult
} from "../../shared/types";
import type { BackendClusterEntry, LengrvisApiClient } from "../lib/apiClient";
import { documentScopesForFiles, mergeScopePaths } from "../lib/documentScope";
import { motionAwareScrollBehavior } from "../lib/motion";
import type { ConnectionState } from "../store";
import { DocumentAnswerView, DocumentCompareView, DocumentResultView } from "./file-search/FileDocumentViews";
import { useFileCleanupWorkspace } from "./file-search/FileCleanupWorkspace";
import {
  CLUSTER_DIMENSION_OPTIONS,
  DEFAULT_SUMMARY_QUESTION,
  SERVICE_OFFLINE_TEXT,
  buildFileOnboardingSteps,
  clusterDimensionOption,
  clusterPayloadFor,
  compactPath,
  displayFilePath,
  fileActionError,
  noticeForIndexStatus,
  noticeForSearchStatus,
  normalizedDirectories,
  searchErrorText,
  shortcutsHaveAnyPath,
  userFileError,
  validateDocumentPath,
  type FileClusterDimension,
  type FileToolTabValue,
  type ResultActionMessage,
  type ResultDocumentAction,
  type SearchStatus
} from "./file-search/FileSearchModels";
import { FileSearchResults } from "./file-search/FileSearchResults";
import { FileScopePanel, type KnownFolderShortcut } from "./file-search/FileScopePanel";
import { FileOnboardingRail, FileServiceGate } from "./file-search/FileToolChrome";
import { Badge, Panel } from "./Panel";

interface FileSearchPanelProps {
  results: FileSearchResult[];
  searchMeta?: FileSearchMeta | null;
  isSearching: boolean;
  onSearch: (query: string) => Promise<void>;
  onClearResults?: () => void;
  searchError?: string | null;
  api?: LengrvisApiClient;
  connectionState?: ConnectionState;
  settings: AppSettings;
  onSaveSettings: (settings: AppSettings) => Promise<void>;
  initialTool?: FileToolTab;
  onToolChange?: (tool: FileToolTab) => void;
  selectedDocumentPath?: string;
  selectedDocumentAction?: DocumentIntentAction;
  selectedDocumentQuestion?: string;
  selectedDocumentIntentId?: number;
  onDocumentIntentHandled?: () => void;
  hasPendingApproval?: boolean;
  onOpenApprovals?: () => void;
  onRequestCleanupApproval?: (scope: string) => Promise<void>;
}

export type FileToolTab = FileToolTabValue;
export type DocumentIntentAction = "read" | "summarize" | "ask";
type DocumentWorkingAction = "read" | "summarize" | "ask" | "compare";
type DocumentOperationResult = {
  ok: boolean;
  error?: string;
};

const TOOL_TABS: Array<{ id: FileToolTab; label: string; description: string; icon: LucideIcon }> = [
  { id: "search", label: "搜索", description: "查找文件", icon: Search },
  { id: "document", label: "文档", description: "读取和提问", icon: FileText },
  { id: "cleanup", label: "清理", description: "先预览", icon: Trash2 }
];

export function FileSearchPanel({
  results,
  searchMeta = null,
  isSearching,
  onSearch,
  onClearResults,
  searchError = null,
  api,
  connectionState = "online",
  settings,
  onSaveSettings,
  initialTool = "search",
  onToolChange,
  selectedDocumentPath,
  selectedDocumentAction,
  selectedDocumentQuestion,
  selectedDocumentIntentId,
  onDocumentIntentHandled,
  hasPendingApproval = false,
  onOpenApprovals,
  onRequestCleanupApproval
}: FileSearchPanelProps) {
  const [activeTool, setActiveTool] = useState<FileToolTab>(initialTool);
  const [query, setQuery] = useState("");
  const [knownFolders, setKnownFolders] = useState<Record<KnownFolderShortcut["id"], string | null>>({
    desktop: null,
    downloads: null,
    documents: null,
    pictures: null
  });
  const [knownFoldersChecked, setKnownFoldersChecked] = useState(false);
  const [manualScope, setManualScope] = useState("");
  const [scopeNotice, setScopeNotice] = useState<string | null>(null);
  const [scopeError, setScopeError] = useState<string | null>(null);
  const [isSavingScope, setIsSavingScope] = useState(false);
  const [searchStatus, setSearchStatus] = useState<SearchStatus>("idle");
  const [searchMessage, setSearchMessage] = useState<string | null>(null);
  const [resultActionMessage, setResultActionMessage] = useState<ResultActionMessage | null>(null);
  const [resultDocumentAction, setResultDocumentAction] = useState<{
    path: string;
    action: ResultDocumentAction;
  } | null>(null);
  const [revealingPath, setRevealingPath] = useState<string | null>(null);
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
  const [documentNotice, setDocumentNotice] = useState<string | null>(null);
  const [documentError, setDocumentError] = useState<string | null>(null);
  const [isDocumentWorking, setIsDocumentWorking] = useState(false);
  const [documentWorkingAction, setDocumentWorkingAction] = useState<DocumentWorkingAction | null>(null);
  const scopePanelRef = useRef<HTMLElement>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const documentPaneRef = useRef<HTMLElement | null>(null);
  const documentPathInputRef = useRef<HTMLInputElement | null>(null);
  const comparePathInputRef = useRef<HTMLInputElement | null>(null);
  const documentQuestionInputRef = useRef<HTMLInputElement | null>(null);
  const handledDocumentIntentId = useRef<number | undefined>(undefined);

  const allowedDirectories = useMemo(() => normalizedDirectories(settings), [settings]);
  const currentScope = allowedDirectories[0] ?? "";
  const cleanupWorkspace = useFileCleanupWorkspace({
    api,
    currentScope,
    hasPendingApproval,
    onOpenApprovals,
    onRequestCleanupApproval
  });
  const hasKnownFolderShortcuts = shortcutsHaveAnyPath(knownFolders);
  const selectedClusterDimension = clusterDimensionOption(clusterDimension);
  const resultClusterDimension = clusterDimensionOption(clusterResultDimension);
  const selectedDocument = documentPath.trim() || documentResult?.title || "";
  const selectedDocumentPathValue = documentPath.trim();
  const selectedDocumentPathParts = useMemo(
    () => selectedDocumentPathValue ? displayFilePath(selectedDocumentPathValue) : null,
    [selectedDocumentPathValue]
  );
  const compareDocumentPathValue = comparePath.trim();
  const trimmedQuery = query.trim();
  const serviceUnavailable = connectionState === "offline";
  const searchButtonLabel = !currentScope ? "先选要找的文件夹" : trimmedQuery ? "搜索" : "先输入关键词";
  const onboardingSteps = useMemo(
    () => buildFileOnboardingSteps({
      currentScope,
      activeTool,
      searchStatus,
      resultsCount: results.length,
      selectedDocumentPath: selectedDocumentPathValue,
      documentReady: Boolean(documentResult),
      cleanupReady: cleanupWorkspace.hasPreview
    }),
    [activeTool, cleanupWorkspace.hasPreview, currentScope, documentResult, results.length, searchStatus, selectedDocumentPathValue]
  );
  const searchNotice = useMemo(
    () =>
      isSavingScope
        ? { tone: "info" as const, text: "正在切换搜索范围，请稍等一下。" }
        : noticeForSearchStatus(searchStatus, searchMessage, results.length, searchMeta),
    [isSavingScope, results.length, searchMessage, searchMeta, searchStatus]
  );
  const indexStatusNotice = useMemo(
    () => noticeForIndexStatus(searchMeta?.indexStatus),
    [searchMeta?.indexStatus]
  );

  const shortcuts: KnownFolderShortcut[] = useMemo(
    () => [
      { id: "desktop", label: "桌面", icon: FolderOpen, path: knownFolders.desktop },
      { id: "downloads", label: "下载", icon: Download, path: knownFolders.downloads },
      { id: "documents", label: "文档", icon: FileText, path: knownFolders.documents },
      { id: "pictures", label: "图片", icon: ImageIcon, path: knownFolders.pictures }
    ],
    [knownFolders]
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const knownFoldersBridge = window.lengrvis?.dialog.knownFolders;
      if (!knownFoldersBridge) {
        if (!cancelled) setKnownFoldersChecked(true);
        return;
      }
      try {
        const folders = await knownFoldersBridge();
        if (!cancelled && folders) setKnownFolders(folders);
      } finally {
        if (!cancelled) setKnownFoldersChecked(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setActiveTool(initialTool);
  }, [initialTool]);

  useEffect(() => {
    setManualScope(currentScope);
  }, [currentScope]);

  const selectTool = (tool: FileToolTab) => {
    setActiveTool(tool);
    onToolChange?.(tool);
  };

  useEffect(() => {
    if (isSearching) {
      setSearchStatus("loading");
      setSearchMessage(null);
    }
  }, [isSearching]);

  useEffect(() => {
    if (!searchError) return;
    setSearchStatus("error");
    setSearchMessage(searchErrorText(searchError, "文件搜索失败，请稍后重试。"));
  }, [searchError]);

  useEffect(() => {
    if (isSearching || searchError || searchStatus !== "loading") return;
    if (results.length) {
      setSearchStatus("success");
      setSearchMessage(null);
    } else {
      setSearchStatus("empty");
      setSearchMessage(null);
    }
  }, [isSearching, results.length, searchError, searchMeta, searchStatus]);

  const setSearchScope = async (path: string | null | undefined) => {
    const nextPath = path?.trim();
    if (!nextPath) {
      setScopeNotice(null);
      setScopeError("这个位置暂时不可用。请点“选择要查找的文件夹”，或在下方粘贴文件夹路径。");
      return;
    }

    setScopeError(null);
    setScopeNotice(null);
    setSearchStatus("idle");
    setSearchMessage(null);
    onClearResults?.();
    setClusters([]);
    setClusterError(null);
    cleanupWorkspace.reset();
    setIsSavingScope(true);
    try {
      await onSaveSettings({
        ...settings,
        workspaceRoot: nextPath,
        allowedDirectories: [nextPath]
      });
      setScopeNotice(`已切换范围：${compactPath(nextPath)}。接下来只会在这个文件夹里搜索、分组或清理。`);
    } catch (error) {
      setScopeError(userFileError(error, "保存当前范围失败，请稍后重试。"));
    } finally {
      setIsSavingScope(false);
    }
  };

  const ensureDocumentScopes = useCallback(async (filePaths: string[]): Promise<boolean> => {
    const missingScopes = documentScopesForFiles(filePaths, allowedDirectories);
    if (!missingScopes.length) return true;

    const nextAllowedDirectories = mergeScopePaths([...allowedDirectories, ...missingScopes]);
    const nextWorkspaceRoot = nextAllowedDirectories.includes(currentScope) ? currentScope : nextAllowedDirectories[0] || settings.workspaceRoot;
    setScopeError(null);
    setScopeNotice(null);
    setIsSavingScope(true);
    try {
      await onSaveSettings({
        ...settings,
        workspaceRoot: nextWorkspaceRoot,
        allowedDirectories: nextAllowedDirectories
      });
      setManualScope(nextWorkspaceRoot);
      setScopeNotice(
        missingScopes.length === 1
          ? `已授权文档所在文件夹：${compactPath(missingScopes[0])}。`
          : `已授权 ${missingScopes.length} 个文档所在文件夹。`
      );
      return true;
    } catch (error) {
      setScopeError(userFileError(error, "保存文档所在文件夹范围失败，请稍后重试。"));
      setDocumentError("已选中文档，但当前范围保存失败。请手动选择文档所在文件夹后再继续。");
      return false;
    } finally {
      setIsSavingScope(false);
    }
  }, [allowedDirectories, currentScope, onSaveSettings, settings]);

  const ensureDocumentScope = useCallback(
    async (filePath: string): Promise<boolean> => ensureDocumentScopes([filePath]),
    [ensureDocumentScopes]
  );

  const chooseFolder = async () => {
    const directoryChooser = window.lengrvis?.dialog.chooseDirectory;
    if (!directoryChooser) {
      setScopeError(null);
      setScopeNotice("这里暂时不能弹出文件夹窗口。请打开桌面应用，或先在下方粘贴完整文件夹路径。");
      return;
    }
    const picked = await directoryChooser();
    if (picked) {
      await setSearchScope(picked);
      return;
    }
    setScopeError(null);
    setScopeNotice("已取消选择，当前范围没有改变。");
  };

  const applyManualScope = async () => {
    const value = manualScope.trim();
    if (!value) {
      setScopeNotice(null);
      setScopeError("请先粘贴一个文件夹路径。");
      return;
    }
    await setSearchScope(value);
  };

  const chooseDocument = async (action: "select" | "summarize" = "select") => {
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
    if (!scopeReady) return;
    setDocumentNotice("已选中文档。可以读取、总结，或输入问题后提问。");
  };

  const chooseCompareDocument = async () => {
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
  };

  const focusScopePicker = () => {
    setScopeNotice("请先在这里选择要查找的文件夹；Lengrvis 不会默认扫描整台电脑。");
    window.setTimeout(() => scopePanelRef.current?.scrollIntoView({ block: "nearest", behavior: motionAwareScrollBehavior() }), 0);
  };

  const submit = async () => {
    const value = query.trim();
    if (serviceUnavailable) {
      onClearResults?.();
      setSearchStatus("error");
      setSearchMessage(`已保留你的关键词。${SERVICE_OFFLINE_TEXT}`);
      return;
    }
    if (!currentScope) {
      onClearResults?.();
      setSearchStatus("missing_scope");
      setSearchMessage("请先选择要查找的文件夹，再开始查找文件。");
      focusScopePicker();
      return;
    }
    if (isSavingScope) {
      onClearResults?.();
      setSearchStatus("idle");
      setSearchMessage("正在切换搜索范围，请稍等一下。");
      return;
    }
    if (!value) {
      onClearResults?.();
      setSearchStatus("missing_query");
      setSearchMessage("请输入要查找的文件名或关键词。");
      window.setTimeout(() => searchInputRef.current?.focus(), 0);
      return;
    }
    if (isSearching) return;

    onClearResults?.();
    setSearchStatus("loading");
    setSearchMessage(null);
    await onSearch(value);
  };

  const runCluster = async () => {
    if (!api || isClustering) return;
    if (!currentScope) {
      setClusterError("请先选择一个文件夹范围，再进行智能分组。");
      return;
    }
    if (isSavingScope) {
      setClusterError("正在切换文件夹范围，请稍等一下再分组。");
      return;
    }
    const requestedDimension = clusterDimension;
    setIsClustering(true);
    setClusterError(null);
    setClusterResultDimension(requestedDimension);
    try {
      const response = await api.clusterFiles(clusterPayloadFor(requestedDimension));
      if (response.ok && response.data?.ok) {
        setClusters(response.data.clusters ?? []);
        if (!response.data.clusters?.length) {
          setClusterError("当前范围里还没找到可分组的文件。可以换到下载、文档或图片再试。");
        }
      } else {
        setClusters([]);
        setClusterError(userFileError(response.data?.error || response.error?.message, "分组失败，请稍后重试。"));
      }
    } catch (error) {
      setClusters([]);
      setClusterError(userFileError(error, "分组失败，请稍后重试。"));
    } finally {
      setIsClustering(false);
    }
  };

  const clearDocumentOutput = () => {
    setDocumentResult(null);
    setDocumentAnswer(null);
    setCompareResult(null);
    setDocumentNotice(null);
    setDocumentError(null);
  };

  const parseDocument = useCallback(async (forcedPath?: string, notice?: string): Promise<DocumentOperationResult> => {
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
      return { ok: false, error: "当前范围没有保存成功。请先选择这个文档所在文件夹，再读取文档。" };
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
      } else {
        const message = fileActionError(response.error?.message, "read");
        setDocumentNotice(null);
        setDocumentError(message);
        return { ok: false, error: message };
      }
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

  const askDocument = useCallback(async (forcedQuestion?: string, forcedPath?: string, notice?: string): Promise<DocumentOperationResult> => {
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
      return { ok: false, error: "当前范围没有保存成功。请先选择这个文档所在文件夹，再总结或提问。" };
    }
    const nextWorkingAction: DocumentWorkingAction = forcedQuestion === DEFAULT_SUMMARY_QUESTION ? "summarize" : "ask";
    setIsDocumentWorking(true);
    setDocumentWorkingAction(nextWorkingAction);
    setDocumentNotice(notice ?? null);
    setDocumentError(null);
    try {
      const response = await api.askDocument({ path, question });
      if (response.ok && response.data) {
        setDocumentAnswer(response.data);
        setDocumentNotice(forcedQuestion === DEFAULT_SUMMARY_QUESTION ? "已生成总结，可以继续追问。" : "已生成回答，可以继续追问。");
        return { ok: true };
      } else {
        const message = fileActionError(response.error?.message, "summarize");
        setDocumentNotice(null);
        setDocumentError(message);
        return { ok: false, error: message };
      }
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

  const summarizeDocument = async (forcedPath?: string) => {
    const path = (forcedPath ?? selectedDocumentPathValue).trim();
    if (!path) return;
    setDocumentQuestion(DEFAULT_SUMMARY_QUESTION);
    await askDocument(DEFAULT_SUMMARY_QUESTION, path, "正在总结这份文档...");
  };

  const useSearchResultAsDocument = async (path: string, action: ResultDocumentAction) => {
    setActiveTool("document");
    onToolChange?.("document");
    setDocumentPath(path);
    setResultDocumentAction({ path, action });
    setResultActionMessage({
      path,
      tone: "info",
      text: action === "read" ? "正在切到文档操作区并读取文件..." : "正在切到文档操作区并总结文件..."
    });
    clearDocumentOutput();
    window.setTimeout(() => documentPaneRef.current?.scrollIntoView({ block: "start", behavior: motionAwareScrollBehavior() }), 0);
    try {
      if (action === "read") {
        setDocumentQuestion("");
        const result = await parseDocument(path, "正在读取这份文档...");
        if (!result.ok) {
          setResultActionMessage({ path, tone: "error", text: result.error || fileActionError(null, "read") });
          return;
        }
        setResultActionMessage({ path, tone: "success", text: "已切到文档操作区并读取完成。" });
        return;
      }
      setDocumentQuestion(DEFAULT_SUMMARY_QUESTION);
      const result = await askDocument(DEFAULT_SUMMARY_QUESTION, path, "正在总结这份文档...");
      if (!result.ok) {
        setResultActionMessage({ path, tone: "error", text: result.error || fileActionError(null, "summarize") });
        return;
      }
      setResultActionMessage({ path, tone: "success", text: "已切到文档操作区并生成总结。" });
    } catch (error) {
      setResultActionMessage({ path, tone: "error", text: fileActionError(error, action) });
    } finally {
      setResultDocumentAction(null);
    }
  };

  const revealSearchResult = async (path: string) => {
    if (!api || revealingPath) return;
    setRevealingPath(path);
    setResultActionMessage(null);
    try {
      const response = await api.revealFile(path);
      if (response.ok && response.data?.ok) {
        setResultActionMessage({ path, tone: "success", text: "已打开文件所在位置；这一步只定位文件，不会修改内容。" });
      } else {
        setResultActionMessage({ path, tone: "error", text: fileActionError(response.data?.error || response.error?.message, "reveal") });
      }
    } catch (error) {
      setResultActionMessage({ path, tone: "error", text: fileActionError(error, "reveal") });
    } finally {
      setRevealingPath(null);
    }
  };

  useEffect(() => {
    if (!selectedDocumentPath || selectedDocumentIntentId === undefined) return;
    if (handledDocumentIntentId.current === selectedDocumentIntentId) return;
    handledDocumentIntentId.current = selectedDocumentIntentId;

    const action = selectedDocumentAction ?? "ask";
    const question = selectedDocumentQuestion ?? (action === "summarize" ? DEFAULT_SUMMARY_QUESTION : "");
    setActiveTool("document");
    onToolChange?.("document");
    setDocumentPath(selectedDocumentPath);
    setDocumentQuestion(question);
    clearDocumentOutput();

    if (action === "read") {
      void parseDocument(selectedDocumentPath, "正在读取这份文档...");
    } else if (action === "summarize") {
      void askDocument(question || DEFAULT_SUMMARY_QUESTION, selectedDocumentPath, "正在总结这份文档...");
    } else {
      setDocumentNotice("正在向这份文档提问：请在“问这个文档”输入框里写问题。");
      window.setTimeout(() => documentQuestionInputRef.current?.focus(), 0);
    }

    onDocumentIntentHandled?.();
  }, [
    askDocument,
    onDocumentIntentHandled,
    onToolChange,
    parseDocument,
    selectedDocumentAction,
    selectedDocumentIntentId,
    selectedDocumentPath,
    selectedDocumentQuestion
  ]);

  const compareDocuments = async () => {
    if (!api || !selectedDocumentPathValue || !compareDocumentPathValue) return;
    const currentValidation = validateDocumentPath(selectedDocumentPathValue);
    const compareValidation = validateDocumentPath(compareDocumentPathValue);
    if (currentValidation || compareValidation) {
      setDocumentNotice(null);
      setDocumentError(currentValidation ? `第一份文档：${currentValidation}` : `第二份文档：${compareValidation}`);
      return;
    }
    const scopeReady = await ensureDocumentScopes([selectedDocumentPathValue, compareDocumentPathValue]);
    if (!scopeReady) return;
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
  };

  return (
    <Panel
      title="文件工具"
      eyebrow="先选文件夹，再操作"
      action={<Badge tone={currentScope ? "info" : "warning"}>{currentScope ? "已选文件夹" : "未选文件夹"}</Badge>}
    >
      {serviceUnavailable ? <FileServiceGate /> : null}
      <FileOnboardingRail steps={onboardingSteps} onSelectTool={selectTool} />

      <FileScopePanel
        currentScope={currentScope}
        hasKnownFolderShortcuts={hasKnownFolderShortcuts}
        isSavingScope={isSavingScope}
        knownFoldersChecked={knownFoldersChecked}
        manualScope={manualScope}
        scopeError={scopeError}
        scopeNotice={scopeNotice}
        scopePanelRef={scopePanelRef}
        shortcuts={shortcuts}
        onApplyManualScope={() => void applyManualScope()}
        onChooseFolder={() => void chooseFolder()}
        onManualScopeChange={setManualScope}
        onSetSearchScope={(path) => void setSearchScope(path)}
      />

      <div className="file-tool-tabs" role="tablist" aria-label="文件工具类型">
        {TOOL_TABS.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              className={activeTool === tab.id ? "file-tool-tab file-tool-tab--active" : "file-tool-tab"}
              type="button"
              role="tab"
              aria-selected={activeTool === tab.id}
              onClick={() => selectTool(tab.id)}
            >
              <Icon size={15} aria-hidden="true" />
              <span>{tab.label}</span>
              <small>{tab.description}</small>
            </button>
          );
        })}
      </div>

      {activeTool === "search" ? (
        <section className="file-tool-pane" aria-label="搜索文件" aria-busy={isSearching || isSavingScope}>
          {!currentScope ? (
            <div className="file-search-prereq" role="status">
              <div>
                <strong>先选要找的文件夹</strong>
                <p>从桌面、下载、文档、图片开始，或选择一个文件夹。选好后再输入关键词搜索。</p>
              </div>
              <button className="button button--secondary" type="button" onClick={focusScopePicker}>
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
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    void submit();
                  }
                }}
                placeholder="搜索文件名、扩展名，或已索引文档内容"
              />
            </div>
            <button className="button button--secondary" onClick={() => void submit()} disabled={isSearching || isSavingScope}>
              <Search size={16} aria-hidden="true" />
              {searchButtonLabel}
            </button>
            {api ? (
              <>
                <label className="cluster-dimension-picker" title={selectedClusterDimension.description}>
                  <span>分组方式</span>
                  <select
                    aria-label="选择文件分组方式"
                    value={clusterDimension}
                    onChange={(event) => setClusterDimension(event.target.value as FileClusterDimension)}
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
                  onClick={() => void runCluster()}
                  disabled={isClustering || isSavingScope}
                  title={selectedClusterDimension.description}
                >
                  <Layers size={16} aria-hidden="true" />
                  智能分组
                </button>
              </>
            ) : null}
          </div>
          {searchNotice ? <p className={`file-status file-status--${searchNotice.tone}`} role={searchNotice.tone === "error" ? "alert" : "status"}>{searchNotice.text}</p> : null}
          {indexStatusNotice ? <p className={`file-status file-status--${indexStatusNotice.tone}`} role="status">{indexStatusNotice.text}</p> : null}
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
              onRevealSearchResult={(path) => void revealSearchResult(path)}
              onUseSearchResultAsDocument={(path, action) => void useSearchResultAsDocument(path, action)}
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
      ) : null}

      {api && activeTool === "document" ? (
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
                  <button className="button button--primary" type="button" onClick={() => void chooseDocument("select")}>
                    <MousePointerClick size={16} aria-hidden="true" />
                    选择文档
                  </button>
                  <button className="button button--secondary" type="button" onClick={() => void chooseDocument("summarize")}>
                    <FileQuestion size={16} aria-hidden="true" />
                    选择并总结
                  </button>
                  <button className="button button--secondary" type="button" onClick={() => selectTool("search")}>
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
                  onChange={(event) => setDocumentPath(event.target.value)}
                  placeholder={results[0]?.path || "选择文档，或粘贴文件位置"}
                />
                <button className="button button--ghost" type="button" onClick={() => void chooseDocument("select")}>
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
              <button className="button button--secondary document-action-button document-action-button--read" type="button" data-loading={documentWorkingAction === "read" ? "true" : undefined} onClick={() => void parseDocument(undefined, "正在读取这份文档...")} disabled={isDocumentWorking || !selectedDocumentPathValue}>
                {documentWorkingAction === "read" ? <Loader2 size={16} aria-hidden="true" /> : <BookOpenText size={16} aria-hidden="true" />}
                {documentWorkingAction === "read" ? "读取中" : "读取预览"}
              </button>
              <button className="button button--secondary document-action-button document-action-button--summarize" type="button" data-loading={documentWorkingAction === "summarize" ? "true" : undefined} onClick={() => void summarizeDocument()} disabled={isDocumentWorking || !selectedDocumentPathValue}>
                {documentWorkingAction === "summarize" ? <Loader2 size={16} aria-hidden="true" /> : <Sparkles size={16} aria-hidden="true" />}
                {documentWorkingAction === "summarize" ? "总结中" : "总结这份文档"}
              </button>
              <div className="input-with-icon">
                <FileQuestion size={16} aria-hidden="true" />
                <input
                  ref={documentQuestionInputRef}
                  value={documentQuestion}
                  onChange={(event) => setDocumentQuestion(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void askDocument(undefined, undefined, "正在查找文档里的答案...");
                  }}
                  placeholder="问这个文档"
                />
              </div>
              <button className="button button--ghost document-action-button document-action-button--ask" type="button" data-loading={documentWorkingAction === "ask" ? "true" : undefined} onClick={() => void askDocument(undefined, undefined, "正在查找文档里的答案...")} disabled={isDocumentWorking || !selectedDocumentPathValue || !documentQuestion.trim()}>
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
                  onChange={(event) => {
                    setComparePath(event.target.value);
                    setCompareResult(null);
                  }}
                  placeholder="第二份文档位置"
                  aria-label="第二份文档位置"
                />
                <button className="button button--ghost" type="button" onClick={() => void chooseCompareDocument()} disabled={isDocumentWorking}>
                  <MousePointerClick size={16} aria-hidden="true" />
                  选择第二份
                </button>
                <button className="button button--ghost document-action-button document-action-button--compare" type="button" data-loading={documentWorkingAction === "compare" ? "true" : undefined} onClick={() => void compareDocuments()} disabled={isDocumentWorking || !selectedDocumentPathValue || !compareDocumentPathValue}>
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
      ) : null}

      {api && activeTool === "cleanup" ? cleanupWorkspace.pane : null}
    </Panel>
  );
}
