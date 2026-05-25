import { AppWindow, Cpu, HardDrive, Info, ListStart, RefreshCw, Settings, Zap } from "lucide-react";

import type { SystemInfo, SystemProcess } from "../../shared/types";
import { zhSource, zhSystemSuggestion } from "../lib/zh";
import { Panel } from "./Panel";

interface SystemInfoPanelProps {
  info: SystemInfo;
  onRefresh: () => Promise<void>;
  onOpenSettings?: (uri: string) => Promise<void>;
}

export function SystemInfoPanel({ info, onRefresh, onOpenSettings }: SystemInfoPanelProps) {
  const diagnostics = info.diagnostics;
  const processes = info.processes ?? diagnostics?.topProcesses ?? [];
  const startupItems = info.startupItems ?? diagnostics?.startupItems ?? [];
  const apps = info.installedApps ?? [];
  const memoryTotal = Number(diagnostics?.info.memory_total ?? 0);
  const memoryAvailable = Number(diagnostics?.info.memory_available ?? 0);
  const memoryUsedPercent = memoryTotal ? Math.round(((memoryTotal - memoryAvailable) / memoryTotal) * 100) : 0;
  const largestDisk = diagnostics?.disks
    ?.filter((disk) => disk.usage?.total)
    .sort((a, b) => Number(b.usage?.total ?? 0) - Number(a.usage?.total ?? 0))[0];

  return (
    <Panel
      title="系统信息"
      eyebrow="Windows 核心能力"
      action={
        <button className="icon-button" aria-label="刷新系统信息" onClick={() => void onRefresh()}>
          <RefreshCw size={16} aria-hidden="true" />
        </button>
      }
    >
      <div className="system-grid">
        <SystemMetric label="应用版本" value={info.appVersion} />
        <SystemMetric label="Electron 版本" value={info.electronVersion} />
        <SystemMetric label="Chrome 版本" value={info.chromeVersion} />
        <SystemMetric label="Node 版本" value={info.nodeVersion} />
        <SystemMetric label="系统平台" value={`${info.platform} ${info.arch}`} />
        <SystemMetric label="后端地址" value={info.backendBaseUrl} wide />
        <SystemMetric label="内存" value={memoryTotal ? `已用 ${memoryUsedPercent}%` : "未知"} icon={Cpu} />
        <SystemMetric
          label="磁盘"
          value={largestDisk?.usage?.percent !== undefined ? `${largestDisk.mountpoint} 已用 ${largestDisk.usage.percent}%` : "未知"}
          icon={HardDrive}
        />
        <SystemMetric label="启动项" value={`${startupItems.length} 项`} icon={ListStart} />
        <SystemMetric label="应用索引" value={`${apps.length} 个`} icon={AppWindow} />
      </div>

      <div className="system-section">
        <div className="system-section__head">
          <strong>诊断建议</strong>
          {onOpenSettings ? (
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
      {items.length ? items.map((item) => <span key={item}>{item}</span>) : <span>{empty}</span>}
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
