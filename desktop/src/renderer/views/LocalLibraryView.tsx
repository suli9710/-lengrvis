import {
  AppWindow,
  CalendarDays,
  FileImage,
  FileQuestion,
  FileText,
  FolderOpen,
  Image as ImageIcon,
  LayoutGrid,
  MapPin,
  RefreshCw,
  Search,
  UserRound,
  type LucideIcon
} from "lucide-react";
import { useEffect, useMemo, useReducer, useRef, useState } from "react";

import type { IndexStatus, LocalLibraryItem, LocalLibraryResponse } from "../../shared/fileLibraryTypes";
import { absoluteRendererLoopbackBackendUrl, type LengrvisApiClient } from "../lib/apiClient";
import {
  gallerySectionKeys,
  knowledgeSectionKeys,
  libraryFamilyForView,
  sectionForView,
  type LocalLibrarySection
} from "./localLibrarySections";
import {
  initialLocalLibraryLoadState,
  localLibraryContextKey,
  localLibraryLoadReducer,
  LocalLibraryLoadCoordinator,
  localLibraryStateForContext
} from "./localLibraryLoadCoordinator";

interface LocalLibraryViewProps {
  api: LengrvisApiClient;
  activeSection: LocalLibrarySection;
  onSectionChange?: (section: LocalLibrarySection) => void;
  onUseDocument?: (path: string, action: "read" | "summarize" | "ask") => void;
}

interface LibrarySectionMeta {
  id: LocalLibrarySection;
  apiSection: string;
  label: string;
  title: string;
  subtitle: string;
  icon: LucideIcon;
  mode: "grid" | "list";
}

interface LibraryEmptyGuide {
  title: string;
  detail: string;
  steps: string[];
}

const SECTIONS: LibrarySectionMeta[] = [
  {
    id: "apps",
    apiSection: "apps",
    label: "应用",
    title: "应用",
    subtitle: "查看授权目录中的本地应用和脚本",
    icon: AppWindow,
    mode: "list"
  },
  {
    id: "documents",
    apiSection: "documents",
    label: "文档",
    title: "文档",
    subtitle: "直接浏览本地文档、表格、PDF 和文本",
    icon: FileText,
    mode: "list"
  },
  {
    id: "documentOcr",
    apiSection: "document_ocr",
    label: "文档识别",
    title: "文档识别",
    subtitle: "先定位本地文档，再交给识别和问答工具",
    icon: FileText,
    mode: "list"
  },
  {
    id: "papers",
    apiSection: "papers",
    label: "论文",
    title: "论文",
    subtitle: "筛出 PDF、论文和研究材料",
    icon: FileText,
    mode: "list"
  },
  {
    id: "courseware",
    apiSection: "courseware",
    label: "课件",
    title: "课件",
    subtitle: "查看 PPT、讲义和课程资料",
    icon: LayoutGrid,
    mode: "list"
  },
  {
    id: "reports",
    apiSection: "reports",
    label: "报告",
    title: "报告",
    subtitle: "汇总报告、周报和总结文件",
    icon: FileText,
    mode: "list"
  },
  {
    id: "gallery",
    apiSection: "gallery",
    label: "图库",
    title: "图库",
    subtitle: "直接查看本地图片缩略图",
    icon: ImageIcon,
    mode: "grid"
  },
  {
    id: "imageOcr",
    apiSection: "image_ocr",
    label: "图片识别",
    title: "图片识别",
    subtitle: "先浏览图片，再按需做 OCR 或视觉理解",
    icon: FileImage,
    mode: "grid"
  },
  {
    id: "people",
    apiSection: "people",
    label: "人物印象",
    title: "人物印象",
    subtitle: "以图片为入口查看本地人物相关素材",
    icon: UserRound,
    mode: "grid"
  },
  {
    id: "places",
    apiSection: "places",
    label: "足迹地点",
    title: "足迹地点",
    subtitle: "按图片线索浏览地点素材",
    icon: MapPin,
    mode: "grid"
  },
  {
    id: "timeline",
    apiSection: "timeline",
    label: "时光长廊",
    title: "时光长廊",
    subtitle: "按最近修改时间查看本地图片",
    icon: CalendarDays,
    mode: "grid"
  }
];

export function libraryMetaForView(view: LocalLibrarySection): LibrarySectionMeta {
  const section = SECTIONS.find((item) => item.id === sectionForView(view));
  return section ?? SECTIONS[6];
}

export function LocalLibraryView({ api, activeSection, onSectionChange, onUseDocument }: LocalLibraryViewProps) {
  const sectionMeta = useMemo(
    () => SECTIONS.find((section) => section.id === activeSection) ?? SECTIONS[6],
    [activeSection]
  );
  const familySections = useMemo(() => {
    const keys = libraryFamilyForView(sectionMeta.id) === "knowledge" ? knowledgeSectionKeys : gallerySectionKeys;
    return SECTIONS.filter((section) => keys.includes(section.id));
  }, [sectionMeta.id]);
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [loadState, dispatchLoadState] = useReducer(localLibraryLoadReducer, initialLocalLibraryLoadState);
  const [fileIcons, setFileIcons] = useState<Record<string, string>>({});
  const iconLoadRef = useRef({
    contextKey: "",
    pendingPaths: new Set<string>(),
    resolvedPaths: new Set<string>()
  });
  const iconMountedRef = useRef(true);
  const requestCoordinatorRef = useRef<LocalLibraryLoadCoordinator | null>(null);
  if (!requestCoordinatorRef.current) {
    requestCoordinatorRef.current = new LocalLibraryLoadCoordinator();
  }
  const requestCoordinator = requestCoordinatorRef.current;
  const contextKey = localLibraryContextKey(sectionMeta.apiSection, submittedQuery);
  const currentLoadState = localLibraryStateForContext(loadState, contextKey);
  const { library, selectedItem, isLoading, error } = currentLoadState;

  const loadLibrary = async (requestedQuery = query) => {
    const normalizedQuery = requestedQuery.trim();
    const requestContextKey = localLibraryContextKey(sectionMeta.apiSection, normalizedQuery);
    setSubmittedQuery(normalizedQuery);
    await requestCoordinator.load({
      contextKey: requestContextKey,
      preferredItemId: requestContextKey === contextKey ? selectedItem?.id : null,
      request: () => api.listLocalLibrary(sectionMeta.apiSection, normalizedQuery, 260),
      dispatch: dispatchLoadState
    });
  };

  useEffect(() => {
    requestCoordinator.activate();
    iconMountedRef.current = true;
    return () => requestCoordinator.dispose();
  }, [requestCoordinator]);

  useEffect(() => () => {
    iconMountedRef.current = false;
  }, []);

  useEffect(() => {
    const normalizedQuery = query.trim();
    const requestContextKey = localLibraryContextKey(sectionMeta.apiSection, normalizedQuery);
    setSubmittedQuery(normalizedQuery);
    void requestCoordinator.load({
      contextKey: requestContextKey,
      preferredItemId: localLibraryStateForContext(loadState, requestContextKey).selectedItem?.id,
      request: () => api.listLocalLibrary(sectionMeta.apiSection, normalizedQuery, 260),
      dispatch: dispatchLoadState
    });
    // Query changes remain explicit through Enter/refresh; section changes load automatically.
  }, [api, requestCoordinator, sectionMeta.apiSection]);

  const items = library?.items ?? [];
  const backendBaseUrl = window.lengrvis?.backendBaseUrl;
  const selectedIconUrl = selectedItem ? fileIcons[selectedItem.path] || selectedItem.iconUrl || "" : "";
  const indexSummary = libraryIndexSummary(library?.indexStatus);
  const scopeLabel = library?.scopeSummary?.displayLabel ?? (library?.roots.length ? `${library.roots.length} 个授权范围` : "未选择授权目录");
  const emptyGuide = libraryEmptyGuide(library, submittedQuery, sectionMeta);

  useEffect(() => {
    if (iconLoadRef.current.contextKey !== contextKey) {
      iconLoadRef.current = {
        contextKey,
        pendingPaths: new Set<string>(),
        resolvedPaths: new Set<string>()
      };
      setFileIcons({});
    }
    if (!window.lengrvis?.shell.getFileIcon) return;
    if (!items.length) return;
    const iconLoad = iconLoadRef.current;
    const targets = items
      .filter((item) => item.kind === "app" || item.extension === ".lnk" || item.extension === ".exe")
      .filter((item) => !iconLoad.pendingPaths.has(item.path) && !iconLoad.resolvedPaths.has(item.path))
      .slice(0, 80);
    if (!targets.length) return;
    for (const item of targets) iconLoad.pendingPaths.add(item.path);

    void Promise.all(
      targets.map(async (item) => {
        const iconUrl = await window.lengrvis.shell.getFileIcon(item.path).catch(() => null);
        return iconUrl ? [item.path, iconUrl] as const : null;
      })
    ).then((entries) => {
      for (const item of targets) iconLoad.pendingPaths.delete(item.path);
      if (!iconMountedRef.current || iconLoadRef.current !== iconLoad) return;
      const nextEntries = entries.filter((entry): entry is readonly [string, string] => Boolean(entry));
      if (!nextEntries.length) return;
      for (const [path] of nextEntries) iconLoad.resolvedPaths.add(path);
      setFileIcons((current) => ({ ...current, ...Object.fromEntries(nextEntries) }));
    });
  }, [contextKey, isLoading, items]);

  return (
    <section className="local-library-view">
      <section className="library-stage" aria-labelledby="local-library-title" aria-busy={isLoading}>
        <div className="library-toolbar">
          <div className="library-toolbar__title">
            <sectionMeta.icon size={20} aria-hidden="true" />
            <div>
              <h2 id="local-library-title">{sectionMeta.title}</h2>
              <p>{sectionMeta.subtitle}</p>
            </div>
          </div>
          <div className="library-toolbar__actions">
            <label className="library-search">
              <Search size={16} aria-hidden="true" />
              <input
                aria-label="搜索本地内容"
                value={query}
                onChange={(event) => setQuery(event.currentTarget.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                    void loadLibrary(event.currentTarget.value);
                  }
                }}
                placeholder="搜索本地内容"
              />
            </label>
            <button className="icon-button" type="button" aria-label="刷新本地内容" disabled={isLoading} onClick={() => void loadLibrary()}>
              <RefreshCw size={16} aria-hidden="true" className={isLoading ? "spin-icon" : ""} />
            </button>
          </div>
        </div>

        {onSectionChange && familySections.length > 1 ? (
          <nav className="library-tabs" aria-label="内容分类">
            {familySections.map((section) => (
              <button
                key={section.id}
                type="button"
                className={section.id === sectionMeta.id ? "library-tab library-tab--active" : "library-tab"}
                aria-current={section.id === sectionMeta.id ? "page" : undefined}
                onClick={() => onSectionChange(section.id)}
              >
                <section.icon size={14} aria-hidden="true" />
                {section.label}
              </button>
            ))}
          </nav>
        ) : null}

        <div className="library-summary" aria-live="polite">
          <span>{items.length} 项</span>
          <span>{formatBytes(library?.stats.size ?? 0)}</span>
          <span>{scopeLabel}</span>
          {indexSummary ? <span className="library-summary__index" title={indexSummary}>{indexSummary}</span> : null}
          {library?.truncated ? <span>仅显示前 {items.length} 项</span> : null}
        </div>

        {error ? <div className="library-empty" role="alert">{error}</div> : null}
        {!error && !isLoading && items.length === 0 ? (
          <LibraryEmptyState guide={emptyGuide} />
        ) : null}

        {sectionMeta.mode === "grid" ? (
          <div className="library-grid">
            {items.map((item) => (
              <button
                className={selectedItem?.id === item.id ? "library-tile library-tile--active" : "library-tile"}
                key={item.id}
                type="button"
                aria-pressed={selectedItem?.id === item.id}
                onClick={() => dispatchLoadState({ type: "select", contextKey, selectedItem: item })}
                title={item.name}
              >
                <img src={absolutePreviewUrl(backendBaseUrl, item.previewUrl)} alt={item.name} loading="lazy" />
              </button>
            ))}
          </div>
        ) : (
          <div className="library-list">
            {items.map((item) => (
              <button
                className={selectedItem?.id === item.id ? "library-row library-row--active" : "library-row"}
                key={item.id}
                type="button"
                aria-pressed={selectedItem?.id === item.id}
                onClick={() => dispatchLoadState({ type: "select", contextKey, selectedItem: item })}
                title={item.name}
              >
                <FileIcon item={item} iconUrl={fileIcons[item.path] || item.iconUrl} />
                <span>
                  <strong>{item.name}</strong>
                  <small>{item.parentLabel || "授权范围内"}</small>
                </span>
                <em>{item.groupLabel || item.extension}</em>
                <em>{formatBytes(item.size)}</em>
              </button>
            ))}
          </div>
        )}
      </section>

      <aside className="library-inspector">
        {selectedItem ? (
          <>
            {selectedItem.kind === "image" ? (
              <div className="library-inspector__preview">
                <img src={absolutePreviewUrl(backendBaseUrl, selectedItem.previewUrl)} alt={selectedItem.name} />
              </div>
            ) : (
              <div className="library-inspector__file">
                <FileIcon item={selectedItem} iconUrl={selectedIconUrl} large />
              </div>
            )}
            <div className="library-inspector__meta">
              <h3>{selectedItem.name}</h3>
              {isDocumentItem(selectedItem) && onUseDocument ? (
                <div className="library-document-actions" aria-label="文档操作">
                  <button className="button button--secondary button--small" type="button" onClick={() => onUseDocument(selectedItem.path, "read")}>
                    <FileText size={14} aria-hidden="true" />
                    读取
                  </button>
                  <button className="button button--secondary button--small" type="button" onClick={() => onUseDocument(selectedItem.path, "summarize")}>
                    <FileQuestion size={14} aria-hidden="true" />
                    总结
                  </button>
                  <button className="button button--ghost button--small" type="button" onClick={() => onUseDocument(selectedItem.path, "ask")}>
                    提问
                  </button>
                </div>
              ) : null}
              <dl>
                <div>
                  <dt>类型</dt>
                  <dd>{selectedItem.groupLabel || selectedItem.extension || "文件"}</dd>
                </div>
                <div>
                  <dt>大小</dt>
                  <dd>{formatBytes(selectedItem.size)}</dd>
                </div>
                <div>
                  <dt>修改时间</dt>
                  <dd>{formatDate(selectedItem.modifiedAt)}</dd>
                </div>
                {selectedItem.width || selectedItem.height ? (
                  <div>
                    <dt>尺寸</dt>
                    <dd>{selectedItem.width} x {selectedItem.height}</dd>
                  </div>
                ) : null}
                <div>
                  <dt>位置</dt>
                  <dd>{selectedItem.parentLabel ? `${selectedItem.parentLabel} · ${selectedItem.pathLabel || selectedItem.name}` : selectedItem.pathLabel || selectedItem.name}</dd>
                </div>
                <div>
                  <dt>路径安全</dt>
                  <dd>完整路径仅用于本机读取、总结或提问；普通列表和证据截图默认只显示授权范围标签。</dd>
                </div>
              </dl>
            </div>
          </>
        ) : (
          <div className="library-empty library-empty--compact">选择一个本地项目查看详情</div>
        )}
      </aside>
    </section>
  );
}

function LibraryEmptyState({ guide }: { guide: LibraryEmptyGuide }) {
  return (
    <div className="library-empty library-empty--guide">
      <strong>{guide.title}</strong>
      <p>{guide.detail}</p>
      <ul>
        {guide.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ul>
    </div>
  );
}

function libraryEmptyGuide(
  library: LocalLibraryResponse | null,
  query: string,
  section: LibrarySectionMeta
): LibraryEmptyGuide {
  const hasScope = Boolean(library?.scopeSummary?.hasAuthorizedRoots ?? library?.roots.length);
  if (!hasScope) {
    return {
      title: "先选择一个授权范围",
      detail: "Lengrvis 不会自动浏览整台电脑；这里只读取你已经授权的文件夹。",
      steps: ["到文件工具选择桌面、下载或一个文件夹", "回到这里刷新", "全文索引为空时也可以先按文件名浏览"]
    };
  }
  const trimmedQuery = query.trim();
  if (trimmedQuery) {
    return {
      title: "这个关键词没有命中",
      detail: `已在 ${library?.scopeSummary?.displayLabel ?? "授权范围"} 内查找 ${section.label}，没有展示完整本机路径。`,
      steps: ["换一个文件名、扩展名或更短关键词", "确认文档位于已授权范围", "需要搜正文时先重建全文索引"]
    };
  }
  if (library?.truncated) {
    return {
      title: "当前范围太大，先显示为空",
      detail: "扫描有时间和数量上限，避免新手第一次打开时卡住或扫到过宽范围。",
      steps: ["输入更具体的关键词", "改选更小的授权文件夹", "刷新后再查看前几项结果"]
    };
  }
  return {
    title: "授权范围里暂时没有对应内容",
    detail: `这里正在看 ${section.label} 类型文件；完整路径不会出现在普通列表状态里。`,
    steps: ["换到文档、报告、课件或图库分类", "把目标文件放入已授权文件夹", "刷新当前列表"]
  };
}

function isDocumentItem(item: LocalLibraryItem): boolean {
  const extension = item.extension.toLowerCase();
  return [
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
    ".htm"
  ].includes(extension);
}

function libraryIndexSummary(status?: IndexStatus): string {
  if (!status) return "";
  const count = Math.max(0, status.filesIndexed).toLocaleString("zh-CN");
  const updated = formatIndexDate(status.lastIndexedAt);
  if (status.status === "ready") {
    return updated ? `索引 ${count} 个，更新 ${updated}` : `索引 ${count} 个`;
  }
  if (status.status === "degraded") {
    const countLabel = count === "0" ? "" : `${count} 个文件，`;
    return `索引需重试：${countLabel}文件名浏览仍可用`;
  }
  if (status.status === "empty") {
    return "全文索引为空，文件名浏览仍可用";
  }
  if (status.status === "missing_scope") {
    return "未选择授权目录";
  }
  return "";
}

function formatIndexDate(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
}

function FileIcon({ item, iconUrl, large = false }: { item: LocalLibraryItem; iconUrl?: string; large?: boolean }) {
  if (iconUrl) {
    return (
      <span className={large ? "library-file-icon library-file-icon--large" : "library-file-icon"} aria-hidden="true">
        <img src={iconUrl} alt="" draggable={false} />
      </span>
    );
  }
  if (item.kind === "app") return <AppWindow size={large ? 54 : 20} aria-hidden="true" />;
  if (item.kind === "image") return <ImageIcon size={20} aria-hidden="true" />;
  if (item.extension === ".ppt" || item.extension === ".pptx") return <LayoutGrid size={large ? 54 : 20} aria-hidden="true" />;
  if (item.extension === ".pdf") return <FileText size={large ? 54 : 20} aria-hidden="true" />;
  return <FolderOpen size={large ? 54 : 20} aria-hidden="true" />;
}

function absolutePreviewUrl(baseUrl: string | undefined, previewUrl: string): string {
  if (!previewUrl) return "";
  return absoluteRendererLoopbackBackendUrl(previewUrl, baseUrl);
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(size >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatDate(value: number): string {
  if (!value) return "未知";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value * 1000));
}
