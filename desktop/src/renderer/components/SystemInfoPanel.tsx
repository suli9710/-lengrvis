import {
  Archive,
  AppWindow,
  CheckCircle2,
  Cpu,
  FolderOpen,
  HardDrive,
  Info,
  ListStart,
  Loader2,
  PackageCheck,
  RefreshCw,
  Server,
  Settings,
  XCircle,
  Zap
} from "lucide-react";
import { useState } from "react";

import type {
  BackendStatus,
  DiagnosticExportResult,
  DiskInfo,
  SystemDiagnostic,
  SystemDiagnosticUpdateChannel,
  SystemInfo,
  SystemProcess
} from "../../shared/types";
import { zhBackendState, zhSource, zhSystemSuggestion } from "../lib/zh";
import { Panel } from "./Panel";

interface SystemInfoPanelProps {
  info: SystemInfo;
  backendStatus?: BackendStatus;
  onRefresh: () => Promise<void>;
  onExportDiagnostics?: () => Promise<DiagnosticExportResult | null>;
  onRevealPath?: (path: string) => Promise<void>;
  onOpenSettings?: (uri: string) => Promise<void>;
  isRefreshing?: boolean;
}

type DiagnosticExportState = {
  status: "idle" | "success" | "error";
  message: string;
  path?: string;
  bytes?: number;
};

type UpdateCheckState = {
  status: "idle" | "checking" | "checked" | "error";
  checkedAt?: string;
  message?: string;
};

const DEFAULT_UPDATE_CHANNEL: SystemDiagnosticUpdateChannel = {
  configured: false,
  status: "not_configured",
  label: "未配置在线更新通道",
  detail: "当前未配置在线更新通道，请以安装包/发布说明为准。",
  checkAction: "refresh_local_status",
  offlineOnly: true
};

export function SystemInfoPanel({
  info,
  backendStatus,
  onRefresh,
  onExportDiagnostics,
  onRevealPath,
  onOpenSettings,
  isRefreshing = false
}: SystemInfoPanelProps) {
  const [isExportingDiagnostics, setIsExportingDiagnostics] = useState(false);
  const [diagnosticExport, setDiagnosticExport] = useState<DiagnosticExportState>({ status: "idle", message: "" });
  const [updateCheck, setUpdateCheck] = useState<UpdateCheckState>({ status: "idle" });
  const [pathRevealError, setPathRevealError] = useState("");
  const diagnostics = info.diagnostics;
  const processes = info.processes ?? diagnostics?.topProcesses ?? [];
  const startupItems = info.startupItems ?? diagnostics?.startupItems ?? [];
  const apps = info.installedApps ?? [];
  const backendVersion = diagnostics?.product?.version || "未知";
  const updateChannel = diagnostics?.updateChannel ?? DEFAULT_UPDATE_CHANNEL;
  const logDirs = diagnostics?.localPaths?.logDirs ?? [];
  const backendSummary = backendStatusSummary(backendStatus);
  const memoryTotal = Number(diagnostics?.info.memory_total ?? 0);
  const memoryAvailable = Number(diagnostics?.info.memory_available ?? 0);
  const memoryUsedPercent = memoryTotal ? Math.round(((memoryTotal - memoryAvailable) / memoryTotal) * 100) : 0;
  const largestDisk = diagnostics?.disks
    ?.filter((disk) => disk.usage?.total)
    .sort((a, b) => Number(b.usage?.total ?? 0) - Number(a.usage?.total ?? 0))[0];
  const hasDiagnostics =
    Boolean(memoryTotal) ||
    Boolean(largestDisk) ||
    processes.length > 0 ||
    startupItems.length > 0 ||
    apps.length > 0 ||
    Boolean(diagnostics?.suggestions?.length);
  const healthSummary = buildHealthSummary({ hasDiagnostics, diagnostics, memoryUsedPercent, largestDisk });
  const updateSummary = buildUpdateSummary(updateCheck, info, backendVersion, updateChannel);

  const checkUpdateStatus = async () => {
    if (updateCheck.status === "checking") return;
    setUpdateCheck({ status: "checking" });
    try {
      await onRefresh();
      setUpdateCheck({ status: "checked", checkedAt: new Date().toISOString() });
    } catch (error) {
      setUpdateCheck({
        status: "error",
        checkedAt: new Date().toISOString(),
        message: error instanceof Error ? error.message : "本地版本状态刷新失败。"
      });
    }
  };

  const exportDiagnostics = async () => {
    if (!onExportDiagnostics || isExportingDiagnostics) return;
    setIsExportingDiagnostics(true);
    setDiagnosticExport({ status: "idle", message: "" });
    try {
      const result = await onExportDiagnostics();
      if (!result?.ok || !result.path) {
        throw new Error(result?.error || "诊断包导出失败，请刷新后再试。");
      }
      setDiagnosticExport({
        status: "success",
        message: `诊断包已生成：${result.filename || compactPath(result.path)}。下方显示的是本机保存位置，方便你打开文件；不要把完整路径当作可公开信息。`,
        path: result.path,
        bytes: result.bytes
      });
    } catch (error) {
      setDiagnosticExport({
        status: "error",
        message: error instanceof Error ? error.message : "诊断包导出失败，请刷新后再试。"
      });
    } finally {
      setIsExportingDiagnostics(false);
    }
  };

  const revealDiagnosticPackage = async () => {
    if (!diagnosticExport.path || !onRevealPath) return;
    try {
      await onRevealPath(diagnosticExport.path);
    } catch (error) {
      setDiagnosticExport({
        status: "error",
        message: error instanceof Error ? error.message : "无法打开诊断包位置。",
        path: diagnosticExport.path,
        bytes: diagnosticExport.bytes
      });
    }
  };

  const revealLocalPath = async (path: string) => {
    if (!onRevealPath) return;
    setPathRevealError("");
    try {
      await onRevealPath(path);
    } catch (error) {
      setPathRevealError(error instanceof Error ? error.message : "无法打开这个位置。");
    }
  };

  return (
    <Panel
      title="系统信息"
      eyebrow="Windows 核心能力"
      action={
        <button className="icon-button" aria-label="刷新系统信息" onClick={() => void onRefresh()} disabled={isRefreshing}>
          {isRefreshing ? <Loader2 className="settings-spinner" size={16} aria-hidden="true" /> : <RefreshCw size={16} aria-hidden="true" />}
        </button>
      }
    >
      <div className={`system-check-hero system-check-hero--${healthSummary.tone}`}>
        <div>
          <span className="system-check-hero__eyebrow">一键只读检查</span>
          <strong>{isRefreshing ? "正在读取电脑健康快照" : healthSummary.title}</strong>
          <p>{isRefreshing ? "Lengrvis 只读取 CPU、内存、磁盘、启动项和进程信息，不会更改系统设置。" : healthSummary.detail}</p>
        </div>
        <button className="button button--primary" type="button" onClick={() => void onRefresh()} disabled={isRefreshing}>
          {isRefreshing ? <Loader2 className="settings-spinner" size={16} aria-hidden="true" /> : <CheckCircle2 size={16} aria-hidden="true" />}
          {isRefreshing ? "检查中" : hasDiagnostics ? "重新检查" : "立即只读检查"}
        </button>
      </div>

      <div className="system-health-banner">
        <Info size={16} aria-hidden="true" />
        <div>
          <strong>只读诊断，不改设置</strong>
          <span>电脑健康、Lengrvis 连接、任务状态已经分开显示；“暂未读取”不代表电脑异常。</span>
        </div>
      </div>

      <div className={`system-health-banner system-health-banner--${backendSummary.tone}`}>
        <Server size={16} aria-hidden="true" />
        <div>
          <strong>Lengrvis 服务：{backendSummary.label}</strong>
          <span>{backendSummary.detail}</span>
        </div>
      </div>

      <div className={`system-update-card system-update-card--${updateSummary.tone}`} data-testid="system-update-card">
        <div className="system-update-card__body">
          <PackageCheck size={18} aria-hidden="true" />
          <div>
            <span>版本与更新</span>
            <strong>{updateSummary.title}</strong>
            <p data-testid="system-update-detail">{updateSummary.detail}</p>
            <div className="system-update-card__facts">
              <code>桌面 {info.appVersion || "未知"}</code>
              <code>后端 {backendVersion}</code>
              <code data-testid="system-update-channel-label">{updateSummary.shortLabel}</code>
            </div>
          </div>
        </div>
        <button
          className="button button--secondary"
          data-testid="system-update-refresh-button"
          type="button"
          onClick={() => void checkUpdateStatus()}
          disabled={isRefreshing || updateCheck.status === "checking"}
        >
          {updateCheck.status === "checking" || isRefreshing ? (
            <Loader2 className="settings-spinner" size={16} aria-hidden="true" />
          ) : (
            <RefreshCw size={16} aria-hidden="true" />
          )}
          {updateCheck.status === "checking" || isRefreshing ? "刷新中" : "刷新本机状态"}
        </button>
      </div>

      {onExportDiagnostics ? (
        <div className="diagnostic-export" data-testid="diagnostic-export-card">
          <div className="diagnostic-export__body">
            <Archive size={18} aria-hidden="true" />
            <div>
              <strong>遇到问题时导出诊断包</strong>
              <span>支持包会尽量使用脱敏路径和本机范围摘要，包含版本、服务状态、网络接口、进程摘要和最近失败统计；不需要打开配置文件，也不包含你的文档正文、文件内容或密钥。</span>
            </div>
          </div>
          <div className="diagnostic-export__actions">
            <button
              className="button button--primary"
              data-testid="diagnostic-export-button"
              type="button"
              onClick={() => void exportDiagnostics()}
              disabled={isExportingDiagnostics}
            >
              {isExportingDiagnostics ? <Loader2 className="settings-spinner" size={16} aria-hidden="true" /> : <Archive size={16} aria-hidden="true" />}
              {isExportingDiagnostics ? "正在导出" : "导出诊断包"}
            </button>
            {diagnosticExport.status === "success" && diagnosticExport.path && onRevealPath ? (
              <button className="button button--secondary" type="button" onClick={() => void revealDiagnosticPackage()}>
                <FolderOpen size={16} aria-hidden="true" />
                打开所在位置
              </button>
            ) : null}
          </div>
          {diagnosticExport.status !== "idle" ? (
            <div
              className={`diagnostic-export__status diagnostic-export__status--${diagnosticExport.status}`}
              data-testid="diagnostic-export-status"
              aria-live="polite"
            >
              {diagnosticExport.status === "success" ? <CheckCircle2 size={14} aria-hidden="true" /> : <XCircle size={14} aria-hidden="true" />}
              <span>
                {diagnosticExport.message}
                {diagnosticExport.status === "success" && diagnosticExport.bytes ? `（${formatBytes(diagnosticExport.bytes)}）` : ""}
              </span>
            </div>
          ) : null}
          {diagnosticExport.status === "success" && diagnosticExport.path ? (
            <div className="diagnostic-export__path" data-testid="diagnostic-export-path">
              <span>本机保存位置：仅用于在这台电脑上打开，不建议公开完整路径。</span>
              <code>{diagnosticExport.path}</code>
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="system-grid">
        <SystemMetric label="应用版本" value={`桌面 ${info.appVersion || "未知"}`} />
        <SystemMetric label="后端版本" value={backendVersion} />
        <SystemMetric label="更新状态" value={updateSummary.shortLabel} icon={PackageCheck} />
        <SystemMetric label="服务状态" value={backendSummary.label} icon={Server} />
        <SystemMetric label="运行环境" value={`Electron ${info.electronVersion} / Chrome ${info.chromeVersion}`} wide />
        <SystemMetric label="Node 版本" value={info.nodeVersion} />
        <SystemMetric label="系统平台" value={`${info.platform} ${info.arch}`} />
        <SystemMetric label="后端地址" value={info.backendBaseUrl} wide />
        <SystemMetric label="日志目录" value={logDirs[0] ? compactPath(logDirs[0]) : "暂未读取"} wide />
        <SystemMetric label="内存" value={memoryTotal ? `已用 ${memoryUsedPercent}%` : "暂未读取"} icon={Cpu} />
        <SystemMetric
          label="磁盘"
          value={largestDisk?.usage?.percent !== undefined ? `${largestDisk.mountpoint} 已用 ${largestDisk.usage.percent}%` : "暂未读取"}
          icon={HardDrive}
        />
        <SystemMetric label="启动项" value={`${startupItems.length} 项`} icon={ListStart} />
        <SystemMetric label="应用索引" value={`${apps.length} 个`} icon={AppWindow} />
      </div>

      <div className="system-section">
        <div className="system-section__head">
          <strong>日志位置</strong>
          <span>{logDirs.length ? "排查问题时可一并提供这些位置" : "刷新后会显示日志目录"}</span>
        </div>
        <div className="system-path-list">
          {logDirs.length ? (
            logDirs.map((path) => (
              <div className="system-path-row" key={path}>
                <code>{path}</code>
                {onRevealPath ? (
                  <button className="button button--secondary" type="button" onClick={() => void revealLocalPath(path)}>
                    <FolderOpen size={14} aria-hidden="true" />
                    打开
                  </button>
                ) : null}
              </div>
            ))
          ) : (
            <span className="system-empty">暂未读取到日志目录。</span>
          )}
        </div>
        {pathRevealError ? <span className="system-path-error">{pathRevealError}</span> : null}
      </div>

      <div className="system-section">
        <div className="system-section__head">
          <strong>诊断建议</strong>
          {onOpenSettings && diagnostics?.suggestions?.length ? (
            <button
              className="button button--secondary"
              type="button"
              onClick={() => void onOpenSettings("ms-settings:display")}
            >
              <Settings size={14} aria-hidden="true" />
              打开显示设置
            </button>
          ) : null}
        </div>
        <div className="system-suggestions">
          {(diagnostics?.suggestions?.length ? diagnostics.suggestions : ["暂无诊断建议。"]).map(
            (suggestion) => (
              <div className="system-suggestion" key={suggestion}>
                <Zap size={14} aria-hidden="true" />
                <span>{zhSystemSuggestion(suggestion)}</span>
              </div>
            )
          )}
        </div>
      </div>

      <div className="system-section">
        <div className="system-section__head">
          <strong>资源占用进程</strong>
          <span>显示 {processes.length} 个</span>
        </div>
        <div className="system-list">
          {processes.slice(0, 6).map((process) => (
            <ProcessRow key={`${process.pid}-${process.name}`} process={process} />
          ))}
          {!processes.length ? <span className="system-empty">暂无进程快照。</span> : null}
        </div>
      </div>

      <div className="system-section system-section--split">
        <MiniList
          title="启动项"
          items={startupItems.slice(0, 4).map((item) => `${item.name} · ${zhSource(item.source)}`)}
          empty="未检测到启动项。"
        />
        <MiniList
          title="白名单应用"
          items={apps.filter((app) => app.allowlisted).slice(0, 4).map((app) => `${app.name} · ${zhSource(app.source)}`)}
          empty="暂无已索引的白名单应用。"
        />
      </div>
    </Panel>
  );
}

function buildUpdateSummary(
  state: UpdateCheckState,
  info: SystemInfo,
  backendVersion: string,
  updateChannel: SystemDiagnosticUpdateChannel
): { title: string; detail: string; shortLabel: string; tone: "idle" | "ready" | "warning" } {
  const title = `桌面 ${info.appVersion || "未知"} / 后端 ${backendVersion || "未知"}`;
  const channelLabel = updateChannel.label || DEFAULT_UPDATE_CHANNEL.label || "未配置在线更新通道";
  const channelDetail = updateChannel.detail || DEFAULT_UPDATE_CHANNEL.detail || "当前未配置在线更新通道，请以安装包/发布说明为准。";
  const steadyTone = updateChannel.configured ? "ready" : "idle";
  if (state.status === "checking") {
    return {
      title,
      detail: "正在刷新本机版本、服务状态和诊断快照；不会联网查询、下载或自动安装更新。",
      shortLabel: "本机检查中",
      tone: "warning"
    };
  }
  if (state.status === "checked") {
    const checkedAt = state.checkedAt ? ` 上次检查：${formatDateTime(state.checkedAt)}。` : "";
    return {
      title,
      detail: `已刷新当前安装版本和后端版本。${channelDetail}${checkedAt}`,
      shortLabel: channelLabel,
      tone: steadyTone
    };
  }
  if (state.status === "error") {
    return {
      title,
      detail: `${state.message || "本地版本状态刷新失败。"} 这不是联网更新失败；当前只支持读取本机安装状态。`,
      shortLabel: "本机检查失败",
      tone: "warning"
    };
  }
  return {
    title,
    detail: `${channelDetail} “刷新本机状态”只会刷新本机版本和服务信息。`,
    shortLabel: channelLabel,
    tone: steadyTone
  };
}

function buildHealthSummary({
  hasDiagnostics,
  diagnostics,
  memoryUsedPercent,
  largestDisk
}: {
  hasDiagnostics: boolean;
  diagnostics: SystemDiagnostic | undefined;
  memoryUsedPercent: number;
  largestDisk: DiskInfo | undefined;
}): { title: string; detail: string; tone: "idle" | "ready" | "warning" } {
  if (!hasDiagnostics) {
    return {
      title: "还没有电脑健康快照",
      detail: "点击后会立即读取系统状态，给你一个不改设置的第一份检查结果。",
      tone: "idle"
    };
  }

  const diskPercent = largestDisk?.usage?.percent;
  const hasSuggestion = Boolean(diagnostics?.suggestions?.some((suggestion) => !/No critical system issue detected/i.test(suggestion)));
  if (memoryUsedPercent >= 85 || (diskPercent !== undefined && diskPercent >= 90) || hasSuggestion) {
    return {
      title: "发现需要留意的项目",
      detail: "下面列出了内存、磁盘、启动项和进程快照。Lengrvis 只给建议，不会自动修改系统。",
      tone: "warning"
    };
  }

  return {
    title: "未发现关键问题",
    detail: "电脑只读诊断已完成，可以继续让 Lengrvis 处理文件、文档或其他任务。",
    tone: "ready"
  };
}

function backendStatusSummary(status?: BackendStatus): { label: string; detail: string; tone: "ready" | "warning" | "error" } {
  if (!status) {
    return {
      label: "暂未检查",
      detail: "点击刷新后会确认本机服务是否可用。",
      tone: "warning"
    };
  }
  const latency = status.health?.latencyMs !== undefined ? `，响应 ${status.health.latencyMs}ms` : "";
  const checkedAt = status.lastCheckedAt ? `，上次检查 ${formatDateTime(status.lastCheckedAt)}` : "";
  if (status.state === "running" && status.health?.ok !== false) {
    return {
      label: "已连接",
      detail: `${status.message || "本机服务可以正常使用"}${latency}${checkedAt}`,
      tone: "ready"
    };
  }
  if (status.state === "starting") {
    return {
      label: "启动中",
      detail: `${status.message || "本机服务正在启动"}${latency}${checkedAt}`,
      tone: "warning"
    };
  }
  return {
    label: zhBackendState(status.state),
    detail: `${status.message || "本机服务暂时不可用，刷新或重启应用后再试。"}${latency}${checkedAt}`,
    tone: status.state === "error" ? "error" : "warning"
  };
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未知时间";
  return date.toLocaleString();
}

function compactPath(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length <= 3) return path;
  return `.../${parts.slice(-2).join("/")}`;
}

function SystemMetric({
  label,
  value,
  wide = false,
  icon: Icon = Info
}: {
  label: string;
  value: string;
  wide?: boolean;
  icon?: typeof Info;
}) {
  return (
    <div className={`system-metric ${wide ? "system-metric--wide" : ""}`}>
      <Icon size={14} aria-hidden="true" />
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ProcessRow({ process }: { process: SystemProcess }) {
  return (
    <div className="system-list-row">
      <strong>{process.name}</strong>
      <span>{formatBytes(process.memoryBytes)}</span>
      <em>进程号 {process.pid}</em>
    </div>
  );
}

function MiniList({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return (
    <div className="system-mini-list">
      <strong>{title}</strong>
      {items.length ? items.map((item, index) => <span key={`${item}-${index}`}>{item}</span>) : <span>{empty}</span>}
    </div>
  );
}

function formatBytes(value: number) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}
