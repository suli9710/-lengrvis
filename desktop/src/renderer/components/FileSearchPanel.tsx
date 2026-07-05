import { Download, FileText, FolderOpen, Image as ImageIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { FileSearchMeta, FileSearchResult } from "../../shared/fileLibraryTypes";
import type { AppSettings } from "../../shared/settingsTypes";
import type { BackendClusterEntry, LengrvisApiClient } from "../lib/apiClient";
import { documentScopesForFiles, mergeScopePaths } from "../lib/documentScope";
import { motionAwareScrollBehavior } from "../lib/motion";
import type { ConnectionState } from "../store";
import { useFileCleanupWorkspace } from "./file-search/FileCleanupWorkspace";
import { FileDocumentPane } from "./file-search/FileDocumentPane";
import { useFileDocumentWorkspace, type DocumentIntentAction } from "./file-search/FileDocumentWorkspace";
import {
  SERVICE_OFFLINE_TEXT,
  buildFileOnboardingSteps,
  clusterDimensionOption,
  clusterPayloadFor,
  compactPath,
  fileActionError,
  noticeForIndexStatus,
  noticeForSearchStatus,
  normalizedDirectories,
  searchErrorText,
  shortcutsHaveAnyPath,
  userFileError,
  type FileClusterDimension,
  type FileToolTabValue,
  type ResultActionMessage,
  type ResultDocumentAction,
  type SearchStatus
} from "./file-search/FileSearchModels";
import { FileSearchPane } from "./file-search/FileSearchPane";
import { FileScopePanel, type KnownFolderShortcut } from "./file-search/FileScopePanel";
import { FileToolTabs } from "./file-search/FileToolTabs";
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
export type { DocumentIntentAction } from "./file-search/FileDocumentWorkspace";

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
  const scopePanelRef = useRef<HTMLElement>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  const allowedDirectories = useMemo(() => normalizedDirectories(settings), [settings]);
  const currentScope = allowedDirectories[0] ?? "";
  const selectTool = useCallback((tool: FileToolTab) => {
    setActiveTool(tool);
    onToolChange?.(tool);
  }, [onToolChange]);
  const selectDocumentTool = useCallback(() => {
    selectTool("document");
  }, [selectTool]);
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
    } catch (error) { // broad-exception-boundary
      setScopeError(userFileError(error, "保存文档所在文件夹范围失败，请稍后重试。"));
      return false;
    } finally {
      setIsSavingScope(false);
    }
  }, [allowedDirectories, currentScope, onSaveSettings, settings]);
  const documentWorkspace = useFileDocumentWorkspace({
    api,
    ensureDocumentScopes,
    selectedDocumentPath,
    selectedDocumentAction,
    selectedDocumentQuestion,
    selectedDocumentIntentId,
    onDocumentIntentHandled,
    onSelectDocumentTool: selectDocumentTool
  });
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
  const selectedDocumentPathValue = documentWorkspace.selectedDocumentPathValue;
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
      documentReady: Boolean(documentWorkspace.documentResult),
      cleanupReady: cleanupWorkspace.hasPreview
    }),
    [activeTool, cleanupWorkspace.hasPreview, currentScope, documentWorkspace.documentResult, results.length, searchStatus, selectedDocumentPathValue]
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
    } catch (error) { // broad-exception-boundary
      setScopeError(userFileError(error, "保存当前范围失败，请稍后重试。"));
    } finally {
      setIsSavingScope(false);
    }
  };

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
    } catch (error) { // broad-exception-boundary
      setClusters([]);
      setClusterError(userFileError(error, "分组失败，请稍后重试。"));
    } finally {
      setIsClustering(false);
    }
  };

  const useSearchResultAsDocument = async (path: string, action: ResultDocumentAction) => {
    selectTool("document");
    documentWorkspace.setDocumentPath(path);
    setResultDocumentAction({ path, action });
    setResultActionMessage({
      path,
      tone: "info",
      text: action === "read" ? "正在切到文档操作区并读取文件..." : "正在切到文档操作区并总结文件..."
    });
    documentWorkspace.clearDocumentOutput();
    documentWorkspace.scrollIntoView();
    try {
      if (action === "read") {
        documentWorkspace.setDocumentQuestion("");
        const result = await documentWorkspace.readDocument(path, "正在读取这份文档...");
        if (!result.ok) {
          setResultActionMessage({ path, tone: "error", text: result.error || fileActionError(null, "read") });
          return;
        }
        setResultActionMessage({ path, tone: "success", text: "已切到文档操作区并读取完成。" });
        return;
      }
      const result = await documentWorkspace.summarizeDocument(path);
      if (!result.ok) {
        setResultActionMessage({ path, tone: "error", text: result.error || fileActionError(null, "summarize") });
        return;
      }
      setResultActionMessage({ path, tone: "success", text: "已切到文档操作区并生成总结。" });
    } catch (error) { // broad-exception-boundary
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
    } catch (error) { // broad-exception-boundary
      setResultActionMessage({ path, tone: "error", text: fileActionError(error, "reveal") });
    } finally {
      setRevealingPath(null);
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

      <FileToolTabs activeTool={activeTool} onSelectTool={selectTool} />

      {activeTool === "search" ? (
        <FileSearchPane
          apiAvailable={Boolean(api)}
          clusterDimension={clusterDimension}
          clusterError={clusterError}
          clusters={clusters}
          currentScope={currentScope}
          hasKnownFolderShortcuts={hasKnownFolderShortcuts}
          indexStatusNotice={indexStatusNotice}
          isClustering={isClustering}
          isSavingScope={isSavingScope}
          isSearching={isSearching}
          query={query}
          resultActionMessage={resultActionMessage}
          resultClusterDimension={resultClusterDimension}
          resultDocumentAction={resultDocumentAction}
          results={results}
          revealingPath={revealingPath}
          searchButtonLabel={searchButtonLabel}
          searchInputRef={searchInputRef}
          searchMessage={searchMessage}
          searchMeta={searchMeta}
          searchNotice={searchNotice}
          searchStatus={searchStatus}
          selectedClusterDimension={selectedClusterDimension}
          onClusterDimensionChange={setClusterDimension}
          onFocusScopePicker={focusScopePicker}
          onQueryChange={setQuery}
          onRevealSearchResult={(path) => void revealSearchResult(path)}
          onRunCluster={() => void runCluster()}
          onSubmit={() => void submit()}
          onUseSearchResultAsDocument={(path, action) => void useSearchResultAsDocument(path, action)}
        />
      ) : null}

      {api && activeTool === "document" ? (
        <FileDocumentPane
          {...documentWorkspace.paneProps}
          results={results}
          serviceUnavailable={serviceUnavailable}
          onSelectTool={selectTool}
        />
      ) : null}

      {api && activeTool === "cleanup" ? cleanupWorkspace.pane : null}
    </Panel>
  );
}
