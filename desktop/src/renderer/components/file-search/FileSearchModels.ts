import type { AppSettings, FileSearchMeta, IndexStatus } from "../../../shared/types";
import type { FileClusterOptions } from "../../lib/apiClient";
import { zhUserFacingError } from "../../lib/zh";

export type FileToolTabValue = "search" | "document" | "cleanup";
export type SearchStatus = "idle" | "missing_scope" | "missing_query" | "loading" | "empty" | "success" | "error";
export type SearchNoticeTone = "info" | "error" | "empty" | "success";
export type ResultDocumentAction = "read" | "summarize";
export type ResultActionMessage = {
  path: string;
  tone: "info" | "success" | "error";
  text: string;
};
export type FileClusterDimension =
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

export interface FileClusterDimensionOption {
  value: FileClusterDimension;
  label: string;
  description: string;
}

export interface FileOnboardingStep {
  id: "scope" | "search" | "document" | "cleanup";
  label: string;
  state: "done" | "current" | "next";
  tool: FileToolTabValue;
}

export const CLUSTER_DIMENSION_OPTIONS: FileClusterDimensionOption[] = [
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

export const DEFAULT_SUMMARY_QUESTION = "请用简单的话总结这份文档的重点。";
export const SERVICE_OFFLINE_TEXT = "助手暂时连不上，本机文件没有问题。请先点右上角刷新或到设置里启动服务，连接恢复后再继续。";

export function normalizedDirectories(settings: AppSettings): string[] {
  return [
    ...(settings.allowedDirectories ?? []),
    settings.workspaceRoot
  ].filter((path, index, values): path is string => Boolean(path?.trim()) && values.indexOf(path) === index);
}

export function buildFileOnboardingSteps({
  currentScope,
  activeTool,
  searchStatus,
  resultsCount,
  selectedDocumentPath,
  documentReady,
  cleanupReady
}: {
  currentScope: string;
  activeTool: FileToolTabValue;
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
    { id: "scope", label: "选文件夹", state: stepState("scope", currentId, scopeDone), tool: "search" },
    { id: "search", label: "找文件", state: stepState("search", currentId, searchDone), tool: "search" },
    { id: "document", label: "读文档", state: stepState("document", currentId, documentDone), tool: "document" },
    { id: "cleanup", label: "先预览", state: stepState("cleanup", currentId, cleanupReady), tool: "cleanup" }
  ];
}

function stepState(id: FileOnboardingStep["id"], currentId: FileOnboardingStep["id"], done: boolean): FileOnboardingStep["state"] {
  if (done) return "done";
  return id === currentId ? "current" : "next";
}

export function fileOnboardingHeadline(steps: FileOnboardingStep[]) {
  const current = steps.find((step) => step.state === "current") ?? steps.find((step) => step.state === "next");
  if (!current) return "文件工具已准备好";
  if (steps.some((step) => step.id === "cleanup" && step.state === "done")) return "清理预览已生成，下一步只等确认";
  if (current.id === "scope") return "先给 Lengrvis 一个明确文件夹";
  if (current.id === "search") return "输入关键词，只在已选文件夹里找";
  if (current.id === "document") return "选中文档后读取、总结或提问";
  return "清理前先预览，不直接删除";
}

export function formatCount(value?: number): string {
  if (!Number.isFinite(value)) return "0";
  return Math.max(0, Number(value)).toLocaleString("zh-CN");
}

export function displayFilePath(path: string): { name: string; parent: string } {
  const normalized = path.replace(/\\/g, "/").replace(/\/+$/, "");
  const parts = normalized.split("/").filter(Boolean);
  const name = parts.at(-1) || path || "未命名文件";
  const parentParts = parts.slice(0, -1);
  const parent = parentParts.length > 3
    ? `.../${parentParts.slice(-3).join("/")}`
    : parentParts.join("/");
  return { name, parent };
}

export function displaySearchMatch(match: string, fileName: string, fullPath: string): string {
  const value = match.trim();
  if (!value || value === fileName || value === fullPath) return "";
  return value;
}

export function compactPath(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length <= 2) return path;
  return `${parts.at(-2)}/${parts.at(-1)}`;
}

export function clusterDimensionOption(value: FileClusterDimension): FileClusterDimensionOption {
  return CLUSTER_DIMENSION_OPTIONS.find((option) => option.value === value) ?? CLUSTER_DIMENSION_OPTIONS[0];
}

export function shortcutsHaveAnyPath(folders: Record<string, string | null>): boolean {
  return Object.values(folders).some((path) => Boolean(path?.trim()));
}

export function noticeForSearchStatus(
  status: SearchStatus,
  message: string | null,
  resultCount: number,
  meta?: FileSearchMeta | null
): { tone: SearchNoticeTone; text: string } | null {
  switch (status) {
    case "missing_scope":
      return { tone: "error", text: message || "请先选择要查找的文件夹，再开始查找文件。" };
    case "missing_query":
      return { tone: "error", text: message || "请输入要查找的文件名或关键词。" };
    case "loading":
      return { tone: "info", text: "正在查找已选文件夹里的匹配文件..." };
    case "empty":
      return {
        tone: "empty",
        text: message || (
          meta?.truncated
            ? `已检查 ${formatCount(meta?.scanned)} 个文件，暂时没有找到匹配项；当前范围还没完全扫完，结果可能不完整。`
            : meta?.scanned
              ? `已检查 ${formatCount(meta.scanned)} 个文件，没有找到匹配项。可以换个关键词，或换一个文件夹再试。`
              : "没有找到匹配文件。可以换个关键词，或换一个文件夹再试。"
        )
      };
    case "success":
      return {
        tone: meta?.truncated ? "empty" : "success",
        text: message || (
          meta?.truncated
            ? `已显示 ${resultCount} 条结果，已检查 ${formatCount(meta?.scanned)} 个文件；当前范围还没完全扫完，结果可能不完整。`
            : meta?.scanned
              ? `已在已选文件夹找到 ${resultCount} 条结果，检查了 ${formatCount(meta.scanned)} 个文件。`
              : `已在已选文件夹找到 ${resultCount} 条结果。`
        )
      };
    case "error":
      return { tone: "error", text: message || "文件搜索失败，请稍后重试。" };
    case "idle":
    default:
      return null;
  }
}

export function noticeForIndexStatus(status?: IndexStatus | null): { tone: SearchNoticeTone; text: string } | null {
  if (!status) return null;
  const latest = formatIndexTimestamp(status.lastIndexedAt);
  const count = formatCount(status.filesIndexed);
  if (status.status === "ready") {
    return {
      tone: "success",
      text: latest
        ? `全文索引已就绪：${count} 个文件，最近更新 ${latest}。`
        : `全文索引已就绪：${count} 个文件。`
    };
  }
  if (status.status === "degraded") {
    const failure = status.latestFailure?.message ? `最近失败：${status.latestFailure.message}。` : "";
    const retry = status.retryHint || "修复本地嵌入服务后，可重建索引或重新搜索。";
    return {
      tone: "empty",
      text: `索引可用但需要留意：${count} 个文件${latest ? `，最近更新 ${latest}` : ""}。${failure}${retry}`
    };
  }
  if (status.status === "empty") {
    return {
      tone: "info",
      text: status.retryHint || "全文索引暂时为空；文件名搜索仍会实时扫描。重建索引后可搜索文档正文。"
    };
  }
  if (status.status === "missing_scope") {
    return {
      tone: "info",
      text: status.retryHint || "先选择授权文件夹，再开始索引或搜索文件。"
    };
  }
  return null;
}

function formatIndexTimestamp(value?: string): string {
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

export function userFileError(error: unknown, fallback: string): string {
  const raw = error instanceof Error ? error.message : typeof error === "string" ? error : "";
  const friendly = zhUserFacingError(raw);
  return friendly || fallback;
}

export function fileActionError(error: unknown, action: "read" | "summarize" | "reveal"): string {
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
    return `${prefix}：Lengrvis 服务暂时没连接好。请先刷新或重启服务，连接恢复后再试。`;
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

export function searchErrorText(error: unknown, fallback: string): string {
  const text = userFileError(error, fallback);
  if (/等得有点久|timeout|aborted|超时/i.test(text)) {
    return `${text} 这不是“没有结果”，是本次搜索未完成。`;
  }
  return text;
}

export function validateDocumentPath(path: string): string | null {
  const value = path.trim();
  if (!value) return "请先填写文档位置。";
  const hasWindowsDrive = /^[a-z]:[\\/]/i.test(value);
  const hasUncPath = value.startsWith("\\\\");
  const hasPosixRoot = value.startsWith("/");
  if (!hasWindowsDrive && !hasUncPath && !hasPosixRoot) {
    return "请填写完整的文档位置，例如 C:\\Users\\你\\Documents\\文件.pdf。";
  }
  if (!isDocumentPathSupported(value)) {
    return "这个文件格式暂不支持文档读取。请换 PDF、Word、文本、表格、PPT 或常见代码/网页文件。";
  }
  return null;
}

export function isDocumentPathSupported(path: string): boolean {
  const extension = path.match(/\.[a-z0-9]+$/i)?.[0]?.toLowerCase() ?? "";
  return Boolean(extension && SUPPORTED_DOCUMENT_EXTENSIONS.has(extension));
}

const SUPPORTED_DOCUMENT_EXTENSIONS = new Set([
  ".pdf", ".docx", ".txt", ".md", ".markdown", ".log", ".rst", ".json", ".yaml", ".yml",
  ".py", ".ts", ".tsx", ".js", ".csv", ".xlsx", ".pptx", ".html", ".htm", ".png",
  ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"
]);

export function clusterPayloadFor(dimension: FileClusterDimension): FileClusterOptions {
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
