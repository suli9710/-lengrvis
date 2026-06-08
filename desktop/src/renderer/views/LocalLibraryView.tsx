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
import { useEffect, useMemo, useState } from "react";

import type { IndexStatus, LocalLibraryItem, LocalLibraryResponse } from "../../shared/types";
import { absoluteRendererLoopbackBackendUrl, type LengrvisApiClient } from "../lib/apiClient";
import { sectionForView, type LocalLibrarySection } from "./localLibrarySections";

interface LocalLibraryViewProps {
  api: LengrvisApiClient;
  activeSection: LocalLibrarySection;
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

export function LocalLibraryView({ api, activeSection, onUseDocument }: LocalLibraryViewProps) {
  const sectionMeta = useMemo(
    () => SECTIONS.find((section) => section.id === activeSection) ?? SECTIONS[6],
    [activeSection]
  );
  const [query, setQuery] = useState("");
  const [library, setLibrary] = useState<LocalLibraryResponse | null>(null);
  const [selectedItem, setSelectedItem] = useState<LocalLibraryItem | null>(null);
  const [fileIcons, setFileIcons] = useState<Record<string, string>>({});
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadLibrary = async () => {
    setIsLoading(true);
    setError(null);
    const response = await api.listLocalLibrary(sectionMeta.apiSection, query.trim(), 260);
    if (response.ok && response.data) {
      setLibrary(response.data);
      setSelectedItem((current) => {
        if (current && response.data?.items.some((item) => item.id === current.id)) return current;
        return response.data?.items[0] ?? null;
      });
    } else {
      setLibrary(null);
      setSelectedItem(null);
      setError(response.error?.message ?? "读取本地内容失败");
    }
    setIsLoading(false);
  };

  useEffect(() => {
    void loadLibrary();
  }, [sectionMeta.apiSection]);

  const items = library?.items ?? [];
  const backendBaseUrl = window.lengrvis?.backendBaseUrl;
  const selectedIconUrl = selectedItem ? fileIcons[selectedItem.path] || selectedItem.iconUrl || "" : "";
  const indexSummary = libraryIndexSummary(library?.indexStatus);

  useEffect(() => {
    if (!window.lengrvis?.shell.getFileIcon) return;
    const targets = items
      .filter((item) => item.kind === "app" || item.extension === ".lnk" || item.extension === ".exe")
      .filter((item) => !fileIcons[item.path])
      .slice(0, 80);
    if (!targets.length) return;

    let cancelled = false;
    void Promise.all(
      targets.map(async (item) => {
        const iconUrl = await window.lengrvis.shell.getFileIcon(item.path).catch(() => null);
        return iconUrl ? [item.path, iconUrl] as const : null;
      })
    ).then((entries) => {
      if (cancelled) return;
      const nextEntries = entries.filter((entry): entry is readonly [string, string] => Boolean(entry));
      if (!nextEntries.length) return;
      setFileIcons((current) => {
        const next = { ...current };
        for (const [path, iconUrl] of nextEntries) {
          next[path] = iconUrl;
        }
        return next;
      });
    });

    return () => {
      cancelled = true;
    };
  }, [items, fileIcons]);

  return (
    <section className="local-library-view">
      <main className="library-stage">
        <div className="library-toolbar">
          <div className="library-toolbar__title">
            <sectionMeta.icon size={20} aria-hidden="true" />
            <div>
              <h2>{sectionMeta.title}</h2>
              <p>{sectionMeta.subtitle}</p>
            </div>
          </div>
          <div className="library-toolbar__actions">
            <label className="library-search">
              <Search size={16} aria-hidden="true" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void loadLibrary();
                }}
                placeholder="搜索本地内容"
              />
            </label>
            <button className="icon-button" type="button" aria-label="刷新本地内容" onClick={() => void loadLibrary()}>
              <RefreshCw size={16} aria-hidden="true" className={isLoading ? "spin-icon" : ""} />
            </button>
          </div>
        </div>

        <div className="library-summary">
          <span>{items.length} 项</span>
          <span>{formatBytes(library?.stats.size ?? 0)}</span>
          <span>{library?.roots[0] ?? "未配置授权目录"}</span>
          {indexSummary ? <span className="library-summary__index" title={indexSummary}>{indexSummary}</span> : null}
          {library?.truncated ? <span>仅显示前 {items.length} 项</span> : null}
        </div>

        {error ? <div className="library-empty">{error}</div> : null}
        {!error && !isLoading && items.length === 0 ? (
          <div className="library-empty">当前授权目录没有找到对应内容</div>
        ) : null}

        {sectionMeta.mode === "grid" ? (
          <div className="library-grid">
            {items.map((item) => (
              <button
                className={selectedItem?.id === item.id ? "library-tile library-tile--active" : "library-tile"}
                key={item.id}
                type="button"
                onClick={() => setSelectedItem(item)}
                title={item.path}
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
                onClick={() => setSelectedItem(item)}
                title={item.path}
              >
                <FileIcon item={item} iconUrl={fileIcons[item.path] || item.iconUrl} />
                <span>
                  <strong>{item.name}</strong>
                  <small>{item.parent}</small>
                </span>
                <em>{item.groupLabel || item.extension}</em>
                <em>{formatBytes(item.size)}</em>
              </button>
            ))}
          </div>
        )}
      </main>

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
                  <dd>{selectedItem.path}</dd>
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
    const detail = status.latestFailure?.message || status.retryHint || "最近处理失败";
    return `索引需重试：${detail}`;
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
