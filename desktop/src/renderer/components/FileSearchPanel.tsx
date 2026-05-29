import {
  AlertCircle,
  BookOpenText,
  CheckCircle2,
  Download,
  FileQuestion,
  FileText,
  FolderOpen,
  GitCompare,
  Image as ImageIcon,
  Layers,
  Loader2,
  MousePointerClick,
  RotateCcw,
  Search,
  ShieldCheck,
  Sparkles,
  Table2,
  Trash2,
  type LucideIcon
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  AppSettings,
  CleanupPlan,
  DocumentAskResponse,
  DocumentCompareResponse,
  DocumentIR,
  FileSearchMeta,
  FileSearchResult
} from "../../shared/types";
import type { BackendClusterEntry, FileClusterOptions, MavrisApiClient } from "../lib/apiClient";
import { isPathWithinScope, parentDirectory } from "../lib/documentScope";
import { zhUserFacingError } from "../lib/zh";
import type { ConnectionState } from "../store";
import { Badge, Panel } from "./Panel";

interface FileSearchPanelProps {
  results: FileSearchResult[];
  searchMeta?: FileSearchMeta | null;
  isSearching: boolean;
  onSearch: (query: string) => Promise<void>;
  onClearResults?: () => void;
  searchError?: string | null;
  api?: MavrisApiClient;
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

export type FileToolTab = "search" | "document" | "cleanup";
export type DocumentIntentAction = "read" | "summarize" | "ask";
type SearchStatus = "idle" | "missing_scope" | "missing_query" | "loading" | "empty" | "success" | "error";
type SearchNoticeTone = "info" | "error" | "empty" | "success";
type CleanupApprovalStatus = "idle" | "planning" | "ready" | "requesting" | "requested" | "error";
type ResultDocumentAction = "read" | "summarize";
type DocumentWorkingAction = "read" | "summarize" | "ask" | "compare";
type ResultActionMessage = {
  path: string;
  tone: "info" | "success" | "error";
  text: string;
};
type DocumentOperationResult = {
  ok: boolean;
  error?: string;
};

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

interface KnownFolderShortcut {
  id: "desktop" | "downloads" | "documents" | "pictures";
  label: string;
  icon: LucideIcon;
  path: string | null;
}

const CLUSTER_DIMENSION_OPTIONS: FileClusterDimensionOption[] = [
  { value: "content", label: "内容", description: "按文件名和扩展名做轻量内容聚类" },
  { value: "type", label: "类型", description: "按文件类型分组" },
  { value: "extension", label: "扩展名", description: "按文件扩展名精确分组" },
  { value: "image_auto", label: "图片自动", description: "按图片语义和元数据自动聚类" },
  { value: "scene", label: "场景", description: "按图片场景标签分组" },
  { value: "people", label: "人物", description: "按图片中的人物数量分组" },
  { value: "objects", label: "物体", description: "按图片中的可见物体分组" },
  { value: "tags", label: "标签", description: "按图片结构化标签分组" },
  { value: "time", label: "时间", description: "按图片拍摄或修改时间分组" },
  { value: "location", label: "地点", description: "按图片 GPS 位置分组" }
];

const TOOL_TABS: Array<{ id: FileToolTab; label: string; description: string; icon: LucideIcon }> = [
  { id: "search", label: "搜索", description: "查找文件", icon: Search },
  { id: "document", label: "文档", description: "读取和提问", icon: FileText },
  { id: "cleanup", label: "清理", description: "先预览", icon: Trash2 }
];

const DEFAULT_SUMMARY_QUESTION = "请用简单的话总结这份文档的重点。";

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
  const [cleanupPlan, setCleanupPlan] = useState<CleanupPlan | null>(null);
  const [cleanupError, setCleanupError] = useState<string | null>(null);
  const [cleanupApprovalStatus, setCleanupApprovalStatus] = useState<CleanupApprovalStatus>("idle");
  const [cleanupApprovalMessage, setCleanupApprovalMessage] = useState<string | null>(null);
  const [isCleanupWorking, setIsCleanupWorking] = useState(false);
  const scopePanelRef = useRef<HTMLElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const documentPaneRef = useRef<HTMLElement | null>(null);
  const documentPathInputRef = useRef<HTMLInputElement | null>(null);
  const comparePathInputRef = useRef<HTMLInputElement | null>(null);
  const documentQuestionInputRef = useRef<HTMLInputElement | null>(null);
  const handledDocumentIntentId = useRef<number | undefined>(undefined);

  const allowedDirectories = useMemo(() => normalizedDirectories(settings), [settings]);
  const currentScope = allowedDirectories[0] ?? "";
  const hasKnownFolderShortcuts = shortcutsHaveAnyPath(knownFolders);
  const selectedClusterDimension = clusterDimensionOption(clusterDimension);
  const resultClusterDimension = clusterDimensionOption(clusterResultDimension);
  const selectedDocument = documentPath.trim() || documentResult?.title || "";
  const selectedDocumentPathValue = documentPath.trim();
  const compareDocumentPathValue = comparePath.trim();
  const trimmedQuery = query.trim();
  const serviceUnavailable = connectionState === "offline";
  const searchButtonLabel = !currentScope ? "先选择范围" : trimmedQuery ? "搜索" : "先输入关键词";
  const cleanupBuckets = useMemo(() => splitCleanupItems(cleanupPlan), [cleanupPlan]);
  const executableCleanupCount = useMemo(
    () => cleanupPlan?.items.filter(isExecutableCleanupItem).length ?? 0,
    [cleanupPlan]
  );
  const onboardingSteps = useMemo(
    () => buildFileOnboardingSteps({
      currentScope,
      activeTool,
      searchStatus,
      resultsCount: results.length,
      selectedDocumentPath: selectedDocumentPathValue,
      documentReady: Boolean(documentResult),
      cleanupReady: Boolean(cleanupPlan)
    }),
    [activeTool, cleanupPlan, currentScope, documentResult, results.length, searchStatus, selectedDocumentPathValue]
  );
  const searchNotice = useMemo(
    () =>
      isSavingScope
        ? { tone: "info" as const, text: "正在切换搜索范围，请稍等一下。" }
        : noticeForSearchStatus(searchStatus, searchMessage, results.length, searchMeta),
    [isSavingScope, results.length, searchMessage, searchMeta, searchStatus]
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
      const knownFoldersBridge = window.mavris?.dialog.knownFolders;
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
      setScopeError("这个位置暂时不可用。请点“选择要整理的位置”，或在下方粘贴文件夹路径。");
      return;
    }

    setScopeError(null);
    setScopeNotice(null);
    setSearchStatus("idle");
    setSearchMessage(null);
    onClearResults?.();
    setClusters([]);
    setClusterError(null);
    setCleanupPlan(null);
    setCleanupError(null);
    setCleanupApprovalStatus("idle");
    setCleanupApprovalMessage(null);
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

  const ensureDocumentScope = useCallback(async (filePath: string): Promise<boolean> => {
    const folderPath = parentDirectory(filePath);
    if (!folderPath || isPathWithinScope(filePath, allowedDirectories)) return true;

    setScopeError(null);
    setScopeNotice(null);
    setIsSavingScope(true);
    try {
      await onSaveSettings({
        ...settings,
        workspaceRoot: folderPath,
        allowedDirectories: [folderPath]
      });
      setManualScope(folderPath);
      setScopeNotice(`已把文档所在文件夹设为当前范围：${compactPath(folderPath)}。后续读取和总结只会访问这个文件夹。`);
      return true;
    } catch (error) {
      setScopeError(userFileError(error, "保存文档所在文件夹范围失败，请稍后重试。"));
      setDocumentError("已选中文档，但当前范围保存失败。请手动选择这个文档所在文件夹后再读取或总结。");
      return false;
    } finally {
      setIsSavingScope(false);
    }
  }, [allowedDirectories, onSaveSettings, settings]);

  const chooseFolder = async () => {
    const directoryChooser = window.mavris?.dialog.chooseDirectory;
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
    const documentChooser = window.mavris?.dialog.chooseDocument;
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
    const documentChooser = window.mavris?.dialog.chooseDocument;
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
    setScopeNotice("请先在这里选择一个文件夹范围；Mavris 不会默认扫描整台电脑。");
    window.setTimeout(() => scopePanelRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }), 0);
  };

  const submit = async () => {
    const value = query.trim();
    if (serviceUnavailable) {
      onClearResults?.();
      setSearchStatus("error");
      setSearchMessage("Mavris 服务未连接。请先刷新或重启服务，连接恢复后再搜索文件。");
      return;
    }
    if (!currentScope) {
      onClearResults?.();
      setSearchStatus("missing_scope");
      setSearchMessage("请先选择搜索范围，再开始查找文件。");
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
    window.setTimeout(() => documentPaneRef.current?.scrollIntoView({ block: "start", behavior: "smooth" }), 0);
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
      setDocumentNotice("已选中文档，请在下方输入你的问题。");
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

  const resetCleanupPreview = () => {
    setCleanupPlan(null);
    setCleanupError(null);
    setCleanupApprovalStatus("idle");
    setCleanupApprovalMessage(null);
  };

  const scanCleanup = async () => {
    if (!api) return;
    if (!currentScope) {
      setCleanupError("请先选择要检查的文件夹范围。");
      return;
    }
    setIsCleanupWorking(true);
    setCleanupError(null);
    setCleanupApprovalStatus("idle");
    setCleanupApprovalMessage(null);
    try {
      const response = await api.scanCleanup({ roots: [currentScope], thresholdMb: 100 });
      if (response.ok && response.data) {
        setCleanupPlan(response.data);
      } else {
        setCleanupError(userFileError(response.error?.message, "暂时无法扫描可清理项，请稍后重试。"));
      }
    } catch (error) {
      setCleanupError(userFileError(error, "暂时无法扫描可清理项，请稍后重试。"));
    } finally {
      setIsCleanupWorking(false);
    }
  };

  const generateCleanupApprovalPreview = async () => {
    if (!api || isCleanupWorking) return;
    if (!currentScope) {
      setCleanupError("请先选择要检查的文件夹范围。");
      return;
    }
    setIsCleanupWorking(true);
    setCleanupError(null);
    setCleanupApprovalStatus("planning");
    setCleanupApprovalMessage("正在生成确认预览，只校验清单，不执行删除。");
    try {
      const response = await api.planCleanup({ roots: [currentScope], thresholdMb: 100, preferTrash: true });
      if (response.ok && response.data) {
        setCleanupPlan(response.data);
        const executableCount = response.data.items.filter(isExecutableCleanupItem).length;
        if (executableCount) {
          setCleanupApprovalStatus("ready");
          setCleanupApprovalMessage("确认预览已生成。下一步发起确认任务，Mavris 会等待你批准后才执行。");
        } else {
          setCleanupApprovalStatus("ready");
          setCleanupApprovalMessage("确认预览已生成，但当前只有建议项，没有可执行清理项。");
        }
      } else {
        setCleanupApprovalStatus("error");
        setCleanupApprovalMessage(userFileError(response.error?.message, "确认预览生成失败，请稍后重试。"));
      }
    } catch (error) {
      setCleanupApprovalStatus("error");
      setCleanupApprovalMessage(userFileError(error, "确认预览生成失败，请稍后重试。"));
    } finally {
      setIsCleanupWorking(false);
    }
  };

  const requestCleanupApproval = async () => {
    if (!onRequestCleanupApproval || isCleanupWorking) return;
    if (!currentScope) {
      setCleanupError("请先选择要检查的文件夹范围。");
      return;
    }
    setCleanupApprovalStatus("requesting");
    setCleanupApprovalMessage("正在发起确认任务；在你批准前不会移动或删除文件。");
    try {
      await onRequestCleanupApproval(currentScope);
      setCleanupApprovalStatus("requested");
      setCleanupApprovalMessage("确认任务已发起。审批出现后，点“去确认”查看清单并决定批准或拒绝。");
    } catch (error) {
      setCleanupApprovalStatus("error");
      setCleanupApprovalMessage(userFileError(error, "确认任务发起失败，请稍后重试。"));
    }
  };

  return (
    <Panel
      title="文件工具"
      eyebrow="先选范围，再操作"
      action={<Badge tone={currentScope ? "info" : "warning"}>{currentScope ? "已选范围" : "未选择范围"}</Badge>}
    >
      {serviceUnavailable ? (
        <section className="file-service-gate" aria-label="Mavris 服务连接提示">
          <div>
            <strong>先恢复 Mavris 服务连接</strong>
            <p>文件搜索、范围保存和文档读取需要本机服务参与；这只是服务连接问题，不代表电脑或文件夹有故障。</p>
          </div>
          <Badge tone="warning">等待连接</Badge>
        </section>
      ) : null}

      <section className="file-onboarding-rail" aria-label="文件工具开箱流程">
        <div className="file-onboarding-rail__copy">
          <span>首次任务流</span>
          <strong>{fileOnboardingHeadline(onboardingSteps)}</strong>
        </div>
        <div className="file-onboarding-steps">
          {onboardingSteps.map((step, index) => (
            <button
              key={step.id}
              type="button"
              className={`file-onboarding-step file-onboarding-step--${step.state}`}
              onClick={() => selectTool(step.tool)}
              aria-current={step.state === "current" ? "step" : undefined}
            >
              <span className="file-onboarding-step__index">{index + 1}</span>
              <span className="file-onboarding-step__label">{step.label}</span>
            </button>
          ))}
        </div>
      </section>

      <section className="file-scope-panel" aria-label="文件搜索范围" ref={scopePanelRef}>
        <div className="file-scope-panel__head">
          <div className="file-scope-current">
            <span className="file-scope-current__label">当前范围</span>
            <strong title={currentScope || undefined}>
              {isSavingScope ? "正在切换范围..." : currentScope || "未选择范围"}
            </strong>
            <small>{currentScope ? "搜索、分组、清理都会限定在这里。" : "先选择一个文件夹，再开始搜索或整理。"}</small>
          </div>
          <button className="button button--secondary" type="button" onClick={() => void chooseFolder()} disabled={isSavingScope}>
            <FolderOpen size={16} aria-hidden="true" />
            选择要整理的位置
          </button>
        </div>
        {!currentScope ? (
          <p className="file-status file-status--info">第一步：选择要整理的位置。Mavris 不会默认扫描整台电脑。</p>
        ) : null}
        <div className="file-scope-shortcuts" aria-label="常用文件夹">
          {shortcuts.map((shortcut) => {
            const Icon = shortcut.icon;
            const active = Boolean(shortcut.path && shortcut.path === currentScope);
            return (
              <button
                key={shortcut.id}
                className={active ? "scope-chip scope-chip--active" : "scope-chip"}
                type="button"
                onClick={() => void setSearchScope(shortcut.path)}
                disabled={!shortcut.path || isSavingScope}
                aria-pressed={active}
                title={shortcut.path ?? "暂时读不到这个常用文件夹。可以先用上方主按钮选择位置。"}
              >
                <Icon size={14} aria-hidden="true" />
                {shortcut.label}
              </button>
            );
          })}
        </div>
        {knownFoldersChecked && !hasKnownFolderShortcuts ? (
          <p className="file-scope-note">暂时读不到桌面、下载、文档、图片。可以点“选择要整理的位置”，或直接粘贴文件夹路径。</p>
        ) : null}
        {currentScope ? (
          <div className="file-scope-path-preview" aria-label="当前完整范围路径">
            <span>完整路径</span>
            <code>{currentScope}</code>
          </div>
        ) : null}
        <div className="file-scope-manual">
          <label className="field">
            <span>手动范围</span>
            <input
              value={manualScope}
              onChange={(event) => setManualScope(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void applyManualScope();
              }}
              placeholder="粘贴文件夹路径，例如 C:\\Users\\你\\Documents"
            />
          </label>
          <button className="button button--ghost" type="button" onClick={() => void applyManualScope()} disabled={isSavingScope}>
            使用这个范围
          </button>
        </div>
        {scopeNotice ? <p className="file-status file-status--info">{scopeNotice}</p> : null}
        {scopeError ? <p className="field-error">{scopeError}</p> : null}
      </section>

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
                <strong>先选搜索范围</strong>
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
          <div className="file-results">
            {searchStatus === "loading" || isSearching ? (
              <p className="empty-state">正在查找当前范围里的匹配文件...</p>
            ) : isSavingScope ? (
              <div className="empty-state file-empty-guide">
                <strong>正在切换范围</strong>
                <p>切换完成后再输入关键词搜索，结果只会来自当前范围。</p>
              </div>
            ) : searchStatus === "success" && results.length ? (
              results.map((result, index) => {
                const pathParts = displayFilePath(result.path);
                const matchText = displaySearchMatch(result.match, pathParts.name, result.path);
                const documentSupported = isDocumentPathSupported(result.path);
                return (
                  <article className="file-result" key={`${result.id}-${result.path}-${result.line}-${index}`}>
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
                              data-loading={resultDocumentAction?.path === result.path && resultDocumentAction.action === "read" ? "true" : undefined}
                              onClick={() => void useSearchResultAsDocument(result.path, "read")}
                              disabled={Boolean(resultDocumentAction || revealingPath)}
                            >
                              {resultDocumentAction?.path === result.path && resultDocumentAction.action === "read" ? <Loader2 size={14} aria-hidden="true" /> : <BookOpenText size={14} aria-hidden="true" />}
                              {resultDocumentAction?.path === result.path && resultDocumentAction.action === "read" ? "读取中" : "读取"}
                            </button>
                            <button
                              className="file-result-action file-result-action--summarize"
                              type="button"
                              data-loading={resultDocumentAction?.path === result.path && resultDocumentAction.action === "summarize" ? "true" : undefined}
                              onClick={() => void useSearchResultAsDocument(result.path, "summarize")}
                              disabled={Boolean(resultDocumentAction || revealingPath)}
                            >
                              {resultDocumentAction?.path === result.path && resultDocumentAction.action === "summarize" ? <Loader2 size={14} aria-hidden="true" /> : <Sparkles size={14} aria-hidden="true" />}
                              {resultDocumentAction?.path === result.path && resultDocumentAction.action === "summarize" ? "总结中" : "总结"}
                            </button>
                          </>
                        ) : (
                          <span className="file-result__unsupported" title="这个格式暂不支持文档读取或总结">
                            不支持读取/总结
                          </span>
                        )}
                        <button className="file-result-action file-result-action--reveal" type="button" data-loading={revealingPath === result.path ? "true" : undefined} onClick={() => void revealSearchResult(result.path)} disabled={Boolean(resultDocumentAction) || revealingPath === result.path}>
                          {revealingPath === result.path ? <Loader2 size={14} aria-hidden="true" /> : <FolderOpen size={14} aria-hidden="true" />}
                          {revealingPath === result.path ? "打开中" : "打开位置"}
                        </button>
                      </div>
                      {resultActionMessage?.path === result.path ? (
                        <p className={`file-action-hint file-action-hint--${resultActionMessage.tone}`} role={resultActionMessage.tone === "error" ? "alert" : "status"}>
                          <ResultActionIcon tone={resultActionMessage.tone} />
                          {resultActionMessage.text}
                        </p>
                      ) : null}
                    </div>
                  </article>
                );
              })
            ) : searchStatus === "empty" ? (
              <div className="empty-state file-empty-guide">
                <strong>{searchMeta?.truncated ? "没有找到完整结果" : "没有找到结果"}</strong>
                <p>
                  {searchMeta?.truncated
                    ? `已检查 ${formatCount(searchMeta.scanned)} 个文件，但当前范围还没扫完。可以缩小范围，或换一个更具体的关键词再试。`
                    : hasKnownFolderShortcuts
                      ? "换个关键词，或切换到桌面、下载、文档、图片再试。"
                      : "换个关键词，或点“选择要整理的位置”/粘贴路径后再试。"}
                </p>
              </div>
            ) : searchStatus === "error" ? (
              <div className="empty-state file-empty-guide">
                <strong>这次搜索未完成</strong>
                <p>{searchMessage || "文件搜索失败，请稍后重试。"}</p>
                <p>可以换一个小一点的范围，或只搜文件名、扩展名再试。</p>
              </div>
            ) : searchStatus === "missing_scope" ? (
              <div className="empty-state file-empty-guide">
                <strong>还没有选择范围</strong>
                <p>先点“选择要整理的位置”，或从桌面、下载、文档、图片里选一个位置。</p>
              </div>
            ) : searchStatus === "missing_query" ? (
              <div className="empty-state file-empty-guide">
                <strong>还没有输入关键词</strong>
                <p>输入文件名、扩展名或内容关键词后再搜索。</p>
              </div>
            ) : (
              <div className="empty-state file-empty-guide">
                <strong>{currentScope ? "已选择范围，输入关键词开始搜索" : "先选择范围，再输入关键词"}</strong>
                <p>
                  {currentScope
                    ? "输入文件名、扩展名或内容关键词后，Mavris 只会在当前范围里查找。"
                    : "可以从桌面、下载、文档、图片开始，也可以点“选择要整理的位置”指定位置。"}
                </p>
                <p>搜索、分组和清理只会查看当前范围；移动、重命名或删除前都会再次确认。</p>
              </div>
            )}
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
                <span className="muted">选中文档后，可以读取、总结、提问</span>
              </div>
              <Badge tone={selectedDocumentPathValue ? "info" : "neutral"}>{selectedDocumentPathValue ? "已选文档" : "未选文档"}</Badge>
            </div>
            {!selectedDocumentPathValue ? (
              <div className="document-start-hero">
                <div>
                  <strong>先选择一份文档</strong>
                  <p>直接选择文件即可开始；Mavris 会把它所在文件夹设为当前范围，再读取、总结或提问。</p>
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
                  <p>{documentResult ? "内容已读取，可查看预览。" : "点击读取，先确认 Mavris 能打开这份文档。"}</p>
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
              <p className="file-status file-status--success">当前文档：{selectedDocument || selectedDocumentPathValue}</p>
            )}
            <div className="file-tool__actions">
              <button className="button button--secondary document-action-button document-action-button--read" type="button" data-loading={documentWorkingAction === "read" ? "true" : undefined} onClick={() => void parseDocument(undefined, "正在读取这份文档...")} disabled={isDocumentWorking || !selectedDocumentPathValue}>
                {documentWorkingAction === "read" ? <Loader2 size={16} aria-hidden="true" /> : <BookOpenText size={16} aria-hidden="true" />}
                {documentWorkingAction === "read" ? "读取中" : "读取"}
              </button>
              <button className="button button--secondary document-action-button document-action-button--summarize" type="button" data-loading={documentWorkingAction === "summarize" ? "true" : undefined} onClick={() => void summarizeDocument()} disabled={isDocumentWorking || !selectedDocumentPathValue}>
                {documentWorkingAction === "summarize" ? <Loader2 size={16} aria-hidden="true" /> : <Sparkles size={16} aria-hidden="true" />}
                {documentWorkingAction === "summarize" ? "总结中" : "总结"}
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
                {documentWorkingAction === "ask" ? "提问中" : "提问"}
              </button>
            </div>
            {!selectedDocumentPathValue ? (
              <p className="file-action-hint">选择或粘贴文档后，“读取 / 总结 / 提问”会按顺序变为可用。</p>
            ) : !documentQuestion.trim() ? (
              <p className="file-action-hint">想追问这份文档时，先输入问题，再点“提问”。</p>
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

      {api && activeTool === "cleanup" ? (
        <section className="file-tool-pane" aria-label="清理预览">
          <div className="file-tool">
            <div className="file-tool__head">
              <div>
                <strong>清理预览</strong>
                <span className="muted">只扫描当前范围，扫描后再决定，不会直接删除</span>
              </div>
              <Badge tone={cleanupBuckets.permanent.length ? "warning" : "neutral"}>
                {formatBytes(cleanupPlan?.reclaimableBytes)} 可释放
              </Badge>
            </div>
            {!cleanupPlan ? (
              <div className="cleanup-safety-gate">
                <div>
                  <strong>先扫描，不执行</strong>
                  <p>这一步只读取文件信息，不移动、不删除。生成预览后，你再决定是否继续。</p>
                </div>
                <span>只读</span>
              </div>
            ) : null}
            <button className="button button--secondary" type="button" onClick={() => void scanCleanup()} disabled={isCleanupWorking}>
              <Trash2 size={16} aria-hidden="true" />
              {isCleanupWorking && cleanupApprovalStatus === "idle" ? "正在扫描" : "只读扫描可清理项"}
            </button>
            {cleanupError ? <p className="field-error">{cleanupError}</p> : null}
            {cleanupPlan ? (
              <>
                <div className="cleanup-action-row" aria-label="清理确认动作">
                  <button
                    className="button button--ghost"
                    type="button"
                    onClick={resetCleanupPreview}
                    disabled={isCleanupWorking}
                  >
                    <RotateCcw size={16} aria-hidden="true" />
                    放弃本次预览
                  </button>
                  <button
                    className="button button--secondary"
                    type="button"
                    onClick={() => void generateCleanupApprovalPreview()}
                    disabled={isCleanupWorking}
                  >
                    <ShieldCheck size={16} aria-hidden="true" />
                    {cleanupApprovalStatus === "planning" ? "正在生成确认预览" : "生成确认预览"}
                  </button>
                  {onRequestCleanupApproval ? (
                    <button
                      className="button button--primary"
                      type="button"
                      onClick={() => void requestCleanupApproval()}
                      disabled={isCleanupWorking || executableCleanupCount === 0}
                      title={executableCleanupCount === 0 ? "当前没有可执行清理项" : "发起一个需要你批准的清理任务"}
                    >
                      <ShieldCheck size={16} aria-hidden="true" />
                      {cleanupApprovalStatus === "requesting" ? "正在发起确认任务" : "发起确认任务"}
                    </button>
                  ) : null}
                  {hasPendingApproval && onOpenApprovals ? (
                    <button className="button button--ghost" type="button" onClick={onOpenApprovals}>
                      去确认
                    </button>
                  ) : null}
                </div>
                {cleanupApprovalMessage ? (
                  <p
                    className={`file-status file-status--${cleanupApprovalStatus === "error" ? "error" : cleanupApprovalStatus === "ready" || cleanupApprovalStatus === "requested" ? "success" : "info"}`}
                    role={cleanupApprovalStatus === "error" ? "alert" : "status"}
                  >
                    {cleanupApprovalMessage}
                  </p>
                ) : null}
                <CleanupPlanPreview
                  plan={cleanupPlan}
                  permanent={cleanupBuckets.permanent}
                  trash={cleanupBuckets.trash}
                  suggestions={cleanupBuckets.suggestions}
                />
              </>
            ) : (
              <p className="file-status file-status--info">清理工具只会先给预览。真正移动或删除文件前，还会让你确认。</p>
            )}
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
      {result.differences.length ? (
        <ul>
          {result.differences.slice(0, 5).map((difference) => (
            <li key={difference.id}>
              <span className="muted">{difference.severity || "差异"}</span>
              <p><strong>{difference.title}</strong>：{difference.detail}</p>
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
  const needsApproval = plan.status === "needs_approval" || permanent.length > 0 || plan.riskWarnings.length > 0;
  return (
    <div className="cleanup-preview">
      <div className={needsApproval ? "cleanup-safety-gate cleanup-safety-gate--approval" : "cleanup-safety-gate"}>
        <div>
          <strong>{needsApproval ? "等待你确认后才会执行" : "当前只是安全预览"}</strong>
          <p>
            {needsApproval
              ? "包含永久删除或风险项。Mavris 会先生成审批预览，确认后才允许执行。"
              : "扫描不会移动或删除文件；你可以先看清单，再决定下一步。"}
          </p>
        </div>
        <span>{needsApproval ? "需确认" : "只读"}</span>
      </div>
      <div className="cleanup-preview__metrics">
        <span><strong>{formatBytes(plan.reclaimableBytes)}</strong> 可释放</span>
        <span><strong>{permanent.length}</strong> 永久删除</span>
        <span><strong>{trash.length}</strong> 进回收站</span>
      </div>
      <div className="cleanup-approval-steps" aria-label="清理安全步骤">
        <span className="cleanup-approval-step cleanup-approval-step--done">1 只读扫描</span>
        <span className={plan.items.length ? "cleanup-approval-step cleanup-approval-step--done" : "cleanup-approval-step"}>2 风险分桶</span>
        <span className={needsApproval ? "cleanup-approval-step cleanup-approval-step--current" : "cleanup-approval-step"}>3 用户确认</span>
        <span className="cleanup-approval-step">4 执行或放弃</span>
      </div>
      <CleanupBucket title="永久删除" tone="danger" items={permanent} emptyText="没有永久删除项" />
      <CleanupBucket title="进回收站" tone="warning" items={trash} emptyText="没有回收站项" />
      <CleanupBucket title="仅建议" description="仅供你查看，Mavris 不会删除这些项目。" tone="neutral" items={suggestions} emptyText="没有建议项" />
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
  description,
  tone,
  items,
  emptyText
}: {
  title: string;
  description?: string;
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
      {description ? <p className="muted">{description}</p> : null}
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

function isExecutableCleanupItem(item: CleanupPlan["items"][number]): boolean {
  return item.disposition === "permanent_delete" || item.disposition === "trash";
}

function normalizedDirectories(settings: AppSettings): string[] {
  return [
    ...(settings.allowedDirectories ?? []),
    settings.workspaceRoot
  ].filter((path, index, values): path is string => Boolean(path?.trim()) && values.indexOf(path) === index);
}

function ResultActionIcon({ tone }: { tone: ResultActionMessage["tone"] }) {
  if (tone === "success") return <CheckCircle2 size={14} aria-hidden="true" />;
  if (tone === "error") return <AlertCircle size={14} aria-hidden="true" />;
  return <Loader2 size={14} aria-hidden="true" />;
}

interface FileOnboardingStep {
  id: "scope" | "search" | "document" | "cleanup";
  label: string;
  state: "done" | "current" | "next";
  tool: FileToolTab;
}

function buildFileOnboardingSteps({
  currentScope,
  activeTool,
  searchStatus,
  resultsCount,
  selectedDocumentPath,
  documentReady,
  cleanupReady
}: {
  currentScope: string;
  activeTool: FileToolTab;
  searchStatus: SearchStatus;
  resultsCount: number;
  selectedDocumentPath: string;
  documentReady: boolean;
  cleanupReady: boolean;
}): FileOnboardingStep[] {
  const scopeDone = Boolean(currentScope);
  const searchDone = searchStatus === "success" || resultsCount > 0;
  const documentDone = documentReady || Boolean(selectedDocumentPath);
  const currentId: FileOnboardingStep["id"] =
    !scopeDone
      ? "scope"
      : activeTool === "cleanup"
        ? cleanupReady
          ? "cleanup"
          : "cleanup"
        : activeTool === "document"
          ? "document"
          : searchDone
            ? "document"
            : "search";

  return [
    { id: "scope", label: "选范围", state: stepState("scope", currentId, scopeDone), tool: "search" },
    { id: "search", label: "找文件", state: stepState("search", currentId, searchDone), tool: "search" },
    { id: "document", label: "读文档", state: stepState("document", currentId, documentDone), tool: "document" },
    { id: "cleanup", label: "先预览", state: stepState("cleanup", currentId, cleanupReady), tool: "cleanup" }
  ];
}

function stepState(id: FileOnboardingStep["id"], currentId: FileOnboardingStep["id"], done: boolean): FileOnboardingStep["state"] {
  if (done) return "done";
  return id === currentId ? "current" : "next";
}

function fileOnboardingHeadline(steps: FileOnboardingStep[]) {
  const current = steps.find((step) => step.state === "current") ?? steps.find((step) => step.state === "next");
  if (!current) return "文件工具已准备好";
  if (steps.some((step) => step.id === "cleanup" && step.state === "done")) return "清理预览已生成，下一步只等确认";
  if (current.id === "scope") return "先给 Mavris 一个明确文件夹";
  if (current.id === "search") return "输入关键词，结果只在当前范围里找";
  if (current.id === "document") return "选中文档后读取、总结或提问";
  return "清理前先预览，不直接删除";
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

function formatCount(value?: number): string {
  if (!Number.isFinite(value)) return "0";
  return Math.max(0, Number(value)).toLocaleString("zh-CN");
}

function displayFilePath(path: string): { name: string; parent: string } {
  const normalized = path.replace(/\\/g, "/").replace(/\/+$/, "");
  const parts = normalized.split("/").filter(Boolean);
  const name = parts.at(-1) || path || "未命名文件";
  const parentParts = parts.slice(0, -1);
  const parent = parentParts.length > 3
    ? `.../${parentParts.slice(-3).join("/")}`
    : parentParts.join("/");
  return { name, parent };
}

function displaySearchMatch(match: string, fileName: string, fullPath: string): string {
  const value = match.trim();
  if (!value || value === fileName || value === fullPath) return "";
  return value;
}

function compactPath(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length <= 2) return path;
  return `${parts.at(-2)}/${parts.at(-1)}`;
}

function clusterDimensionOption(value: FileClusterDimension): FileClusterDimensionOption {
  return CLUSTER_DIMENSION_OPTIONS.find((option) => option.value === value) ?? CLUSTER_DIMENSION_OPTIONS[0];
}

function shortcutsHaveAnyPath(folders: Record<KnownFolderShortcut["id"], string | null>): boolean {
  return Object.values(folders).some((path) => Boolean(path?.trim()));
}

function noticeForSearchStatus(
  status: SearchStatus,
  message: string | null,
  resultCount: number,
  meta?: FileSearchMeta | null
): { tone: SearchNoticeTone; text: string } | null {
  switch (status) {
    case "missing_scope":
      return { tone: "error", text: message || "请先选择搜索范围，再开始查找文件。" };
    case "missing_query":
      return { tone: "error", text: message || "请输入要查找的文件名或关键词。" };
    case "loading":
      return { tone: "info", text: "正在查找当前范围里的匹配文件..." };
    case "empty":
      return {
        tone: "empty",
        text: message || (
          meta?.truncated
            ? `已检查 ${formatCount(meta?.scanned)} 个文件，暂时没有找到匹配项；当前范围还没完全扫完，结果可能不完整。`
            : meta?.scanned
              ? `已检查 ${formatCount(meta.scanned)} 个文件，没有找到匹配项。可以换个关键词，或换一个文件夹范围再试。`
              : "没有找到匹配文件。可以换个关键词，或换一个文件夹范围再试。"
        )
      };
    case "success":
      return {
        tone: meta?.truncated ? "empty" : "success",
        text: message || (
          meta?.truncated
            ? `已显示 ${resultCount} 条结果，已检查 ${formatCount(meta?.scanned)} 个文件；当前范围还没完全扫完，结果可能不完整。`
            : meta?.scanned
              ? `已在当前范围找到 ${resultCount} 条结果，检查了 ${formatCount(meta.scanned)} 个文件。`
              : `已在当前范围找到 ${resultCount} 条结果。`
        )
      };
    case "error":
      return { tone: "error", text: message || "文件搜索失败，请稍后重试。" };
    case "idle":
    default:
      return null;
  }
}

function userFileError(error: unknown, fallback: string): string {
  const raw = error instanceof Error ? error.message : typeof error === "string" ? error : "";
  const friendly = zhUserFacingError(raw);
  return friendly || fallback;
}

function fileActionError(error: unknown, action: "read" | "summarize" | "reveal"): string {
  const raw = error instanceof Error ? error.message : typeof error === "string" ? error : "";
  const lower = raw.toLowerCase();
  const prefix =
    action === "reveal"
      ? "暂时无法打开所在位置"
      : action === "summarize"
        ? "暂时无法总结这份文档"
        : "暂时无法读取这份文档";

  if (!raw) {
    return `${prefix}。请稍后重试；如果仍失败，可以先换一份文档或重新选择文件夹范围。`;
  }
  if (/network|fetch|failed to fetch|connection|refused|aborted|timeout|超时|连接|后端|服务/i.test(raw)) {
    return `${prefix}：Mavris 服务暂时没连接好。请先刷新或重启服务，连接恢复后再试。`;
  }
  if (
    lower.includes("no authorized directories configured") ||
    lower.includes("outside authorized directories") ||
    lower.includes("not authorized") ||
    raw.includes("不在你已选择的文件夹")
  ) {
    return `${prefix}：这个文件不在当前授权范围内。请先把它所在文件夹加入“当前范围”，再继续操作。`;
  }
  if (lower.includes("path is not a file") || lower.includes("not found") || lower.includes("does not exist") || raw.includes("不存在")) {
    return `${prefix}：文件可能已移动、删除，或路径不是一个文件。请重新搜索或粘贴新的文件位置。`;
  }
  if (lower.includes("permission") || lower.includes("access is denied") || lower.includes("denied") || raw.includes("权限")) {
    return `${prefix}：当前没有足够权限读取这个文件。请确认文件未受系统权限限制，或换到你有权限的文件夹。`;
  }
  if (lower.includes("being used") || lower.includes("in use") || lower.includes("locked") || raw.includes("占用")) {
    return `${prefix}：文件可能正被其他应用占用。请关闭正在打开它的程序后再试。`;
  }
  if (lower.includes("unsupported") || lower.includes("format") || lower.includes("mime") || raw.includes("格式")) {
    return `${prefix}：当前格式暂不支持。可以先转换为 PDF、Word、TXT 或常见表格格式后再试。`;
  }

  return userFileError(raw, `${prefix}。请确认文件存在、在当前范围内，并且格式受支持。`);
}

function searchErrorText(error: unknown, fallback: string): string {
  const text = userFileError(error, fallback);
  if (/等得有点久|timeout|aborted|超时/i.test(text)) {
    return `${text} 这不是“没有结果”，是本次搜索未完成。`;
  }
  return text;
}

function validateDocumentPath(path: string): string | null {
  const value = path.trim();
  if (!value) return "请先填写文档位置。";
  const hasWindowsDrive = /^[a-z]:[\\/]/i.test(value);
  const hasUncPath = value.startsWith("\\\\");
  const hasPosixRoot = value.startsWith("/");
  if (!hasWindowsDrive && !hasUncPath && !hasPosixRoot) {
    return "请填写完整的文档位置，例如 C:\\Users\\你\\Documents\\文件.pdf。";
  }
  const extension = value.match(/\.[a-z0-9]+$/i)?.[0]?.toLowerCase() ?? "";
  if (!isDocumentPathSupported(value)) {
    return "这个文件格式暂不支持文档读取。请换 PDF、Word、文本、表格、PPT 或常见代码/网页文件。";
  }
  return null;
}

function isDocumentPathSupported(path: string): boolean {
  const extension = path.match(/\.[a-z0-9]+$/i)?.[0]?.toLowerCase() ?? "";
  return Boolean(extension && SUPPORTED_DOCUMENT_EXTENSIONS.has(extension));
}

const SUPPORTED_DOCUMENT_EXTENSIONS = new Set([
  ".pdf",
  ".docx",
  ".txt",
  ".md",
  ".markdown",
  ".log",
  ".rst",
  ".json",
  ".yaml",
  ".yml",
  ".py",
  ".ts",
  ".tsx",
  ".js",
  ".csv",
  ".xlsx",
  ".pptx",
  ".html",
  ".htm",
  ".png",
  ".jpg",
  ".jpeg",
  ".webp",
  ".bmp",
  ".tif",
  ".tiff"
]);

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
