import type { Dispatch, SetStateAction } from "react";

import type { AppSettings, McpServerConfig } from "../../../shared/types";
import type { MobileDevice, MobilePairingCode } from "../../lib/apiClient";
import { activeRemoteInputGrantForDevice } from "../../lib/remoteInputGrant";

type HardwareRuntime = "auto" | "winml" | "directml" | "openvino" | "cpu";

export function mobilePairingTransportWarning(baseUrl: string): string {
  try {
    const parsed = new URL(baseUrl);
    if (parsed.protocol !== "http:" || isLoopbackHostname(parsed.hostname)) return "";
    return "当前配对地址是局域网 HTTP。手机端会阻断 token 配对，请在电脑端启用 HTTPS/WSS 或受信任证书后重新生成。";
  } catch {
    return "";
  }
}

type MobilePairingTransportTone = "ready" | "warning" | "blocked";

export interface MobilePairingTransportSummary {
  tone: MobilePairingTransportTone;
  label: string;
  detail: string;
  origin: string;
  wssPaths: string[];
  fingerprint: string;
  trustNotice: string;
}

export function mobilePairingTransportSummary(pairing: MobilePairingCode, baseUrl: string): MobilePairingTransportSummary {
  const parsed = new URL(baseUrl);
  const transport = pairing.transport_security ?? pairing.server.transport_security ?? {};
  const tlsReady = boolTransportValue(transport, "tls_ready") || boolTransportValue(transport, "tls_enabled");
  const status = textTransportValue(transport, "status");
  const fingerprint =
    textTransportValue(transport, "certificate_fingerprint_sha256") || textTransportValue(transport, "fingerprint_sha256");
  const wssPaths = [
    webSocketPathLabel(parsed, "/ws/mobile/approvals"),
    webSocketPathLabel(parsed, "/ws/remote/screen"),
    webSocketPathLabel(parsed, "/ws/remote/input")
  ];
  const https = parsed.protocol === "https:";
  const loopback = isLoopbackHostname(parsed.hostname);
  const trustRequired = pairing.trust_required === true || boolTransportValue(transport, "trust_required") || boolTransportValue(transport, "requires_trust");

  if (https && tlsReady) {
    return {
      tone: "ready",
      label: "HTTPS/WSS 已写入二维码",
      detail: "手机会使用这个局域网 HTTPS 地址，并把审批、远程屏幕和远程输入升级为 WSS。",
      origin: parsed.origin,
      wssPaths,
      fingerprint,
      trustNotice: trustRequired
        ? "Android 首次连接需要信任这张本机证书；请在手机端确认指纹或按系统/应用提示安装信任后再继续。"
        : ""
    };
  }
  if (https) {
    return {
      tone: "warning",
      label: "HTTPS 已配置但证书未就绪",
      detail: status === "https_misconfigured"
        ? "后端报告证书或私钥未通过校验，请重新生成或重新指向证书后再生成二维码。"
        : "当前二维码是 HTTPS，但缺少可确认的 TLS 就绪状态。",
      origin: parsed.origin,
      wssPaths,
      fingerprint,
      trustNotice: "手机端会 fail closed；请先完成证书生成和信任引导。"
    };
  }
  return {
    tone: loopback ? "warning" : "blocked",
    label: loopback ? "仅限本机调试" : "局域网 HTTP 已阻断",
    detail: loopback
      ? "loopback 地址只适合本机或 emulator 调试，正式手机扫码需要真实局域网 HTTPS 地址。"
      : "token 承载的手机配对、审批、远控和输入授权不能走局域网 HTTP/ws。",
    origin: parsed.origin,
    wssPaths,
    fingerprint: "",
    trustNotice: "请用启动器或服务的自动 LAN TLS 生成 HTTPS/WSS 二维码后重试。"
  };
}

function textTransportValue(transport: Record<string, unknown>, key: string): string {
  const value = transport[key];
  return typeof value === "string" ? value.trim() : "";
}

function boolTransportValue(transport: Record<string, unknown>, key: string): boolean {
  const value = transport[key];
  if (typeof value === "boolean") return value;
  if (typeof value === "string") return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
  return false;
}

function webSocketPathLabel(origin: URL, path: string): string {
  const protocol = origin.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${origin.host}${path}`;
}

function isLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  return normalized === "localhost" || normalized === "::1" || normalized.startsWith("127.");
}

export function PairingVisualCodeFallback({ code }: { code?: string }) {
  return (
    <div className="mobile-pairing__visual" aria-label="正在生成手机配对二维码">
      <div className="mobile-pairing__code">{code ?? "------"}</div>
      <div className="mobile-pairing__matrix" aria-hidden="true" />
    </div>
  );
}


export function splitSettingList(value: string) {
  return value
    .replace(/\n/g, ";")
    .split(";")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function readableError(error: unknown, fallback: string): string {
  return error instanceof Error && error.message.trim() ? error.message : fallback;
}

export function updateWorkspaceRoot(current: AppSettings, workspaceRoot: string): AppSettings {
  const existing = current.allowedDirectories?.length
    ? current.allowedDirectories
    : current.workspaceRoot
      ? [current.workspaceRoot]
      : [];
  const nextDirectories = workspaceRoot
    ? [workspaceRoot, ...existing.slice(1).filter((directory) => directory !== workspaceRoot)]
    : existing.slice(1);
  return {
    ...current,
    workspaceRoot,
    allowedDirectories: nextDirectories
  };
}

export function mobileDeviceStatusLabel(device: MobileDevice): string {
  if (device.revoked_at || device.status === "revoked") return "已断开";
  if (device.status === "active" || !device.status) return "已连接";
  return device.status;
}

export function mobileDevicePermissionChips(device: MobileDevice, remoteDesktopEnabled: boolean): string[] {
  if (device.revoked_at || device.status === "revoked") {
    return [mobileDeviceStatusLabel(device), "无有效权限"];
  }
  const activeGrant = activeRemoteInputGrantForDevice(device);
  return [
    mobileDeviceStatusLabel(device),
    "审批",
    `全局屏幕查看：${remoteDesktopEnabled ? "开" : "关"}`,
    activeGrant ? "远程点击：临时开" : "远程点击：关"
  ];
}

export function formatDeviceDate(value?: string): string {
  if (!value) return "未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未知";
  return date.toLocaleString();
}

export function normalizeHardwareRuntime(value: string): string {
  const lowered = value.trim().toLowerCase();
  if (["", "auto"].includes(lowered)) return "";
  if (lowered === "winml" || lowered === "windowsml" || lowered === "windows_ml") return "WinML";
  if (lowered === "directml" || lowered === "dml") return "DirectML";
  if (lowered === "openvino") return "OpenVINO";
  if (lowered === "cpu") return "CPU";
  return value;
}

export function runtimeToProvider(value: HardwareRuntime): string {
  if (value === "winml") return "WinML";
  if (value === "directml") return "DirectML";
  if (value === "openvino") return "OpenVINO";
  if (value === "cpu") return "CPU";
  return "";
}

export function providerToRuntime(value: string): HardwareRuntime {
  const lowered = value.trim().toLowerCase();
  if (!lowered) return "auto";
  if (lowered === "winml" || lowered === "windowsml" || lowered === "windows_ml") return "winml";
  if (lowered === "directml" || lowered === "dml") return "directml";
  if (lowered === "openvino") return "openvino";
  if (lowered === "cpu") return "cpu";
  return "auto";
}

type SetDraft = Dispatch<SetStateAction<AppSettings>>;

export function addMcpServer(setDraft: SetDraft) {
  setDraft((current) => ({
    ...current,
    mcpServers: [...current.mcpServers, { name: "", url: "", enabled: true } satisfies McpServerConfig]
  }));
}

export function updateMcpServer(setDraft: SetDraft, index: number, patch: Partial<McpServerConfig>) {
  setDraft((current) => ({
    ...current,
    mcpServers: current.mcpServers.map((server, i) => (i === index ? { ...server, ...patch } : server))
  }));
}

export function removeMcpServer(setDraft: SetDraft, index: number) {
  setDraft((current) => ({
    ...current,
    mcpServers: current.mcpServers.filter((_, i) => i !== index)
  }));
}
