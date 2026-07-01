import { AlertCircle, CheckCircle2, Copy, KeyRound, Loader2, MousePointer2, ShieldCheck, Trash2, XCircle } from "lucide-react";
import { lazy, Suspense } from "react";

import { buildMobilePairingQrContent, formatMobilePairingBaseUrl } from "../../../shared/mobilePairingPayload";
import type { MobileDevice, MobilePairingCode, RemoteInputGrant } from "../../lib/apiClient";
import { activeRemoteInputGrantForDevice, mobileDeviceCanReceiveRemoteInputGrant } from "../../lib/remoteInputGrant";
import {
  formatDeviceDate,
  mobileDevicePermissionChips,
  mobilePairingTransportSummary,
  mobilePairingTransportWarning,
  PairingVisualCodeFallback
} from "./SettingsPanelHelpers";

const PairingVisualCode = lazy(() =>
  import("./PairingVisualCode").then((module) => ({ default: module.PairingVisualCode }))
);

interface MobilePairingSectionProps {
  pairing: MobilePairingCode | null;
  pairingCopyStatus: string;
  pairingError: string;
  pairedDevices: MobileDevice[];
  remoteDesktopEnabled: boolean;
  isPairing: boolean;
  revokingDeviceId: string;
  remoteInputGrantingDeviceId: string;
  remoteInputRevokingGrantId: string;
  onCopyPairingPayload: () => void;
  onCreatePairingCode: () => void;
  onCreateRemoteInputGrant: (device: MobileDevice) => void;
  onRevokeRemoteInputGrant: (device: MobileDevice, grant: RemoteInputGrant) => void;
  onRevokePairedDevice: (device: MobileDevice) => void;
}

export function MobilePairingSection({
  pairing,
  pairingCopyStatus,
  pairingError,
  pairedDevices,
  remoteDesktopEnabled,
  isPairing,
  revokingDeviceId,
  remoteInputGrantingDeviceId,
  remoteInputRevokingGrantId,
  onCopyPairingPayload,
  onCreatePairingCode,
  onCreateRemoteInputGrant,
  onRevokeRemoteInputGrant,
  onRevokePairedDevice
}: MobilePairingSectionProps) {
  const pairingQrContent = pairing ? buildMobilePairingQrContent(pairing) : null;
  const pairingBaseUrl = pairing ? formatMobilePairingBaseUrl(pairing) : "";
  const transportWarning = pairingBaseUrl ? mobilePairingTransportWarning(pairingBaseUrl) : "";
  const transportSummary = pairing && pairingBaseUrl ? mobilePairingTransportSummary(pairing, pairingBaseUrl) : null;

  return (
    <div className="mobile-pairing">
      <div className="mobile-pairing__copy">
        <strong>手机扫码配对</strong>
        <span>点击生成后，打开手机 App 的扫码入口扫二维码；桌面地址、端口和一次性配对码会一起带过去。</span>
        {pairing ? (
          <div className="mobile-pairing__payload" aria-label="手机扫码配对状态">
            <div className="mobile-pairing__payload-head">
              <small>
                二维码已生成：{pairingBaseUrl} · {new Date(pairing.expires_at).toLocaleTimeString()} 过期
              </small>
              <button
                type="button"
                className="button button--ghost mobile-pairing__copy-button"
                onClick={onCopyPairingPayload}
                aria-label="复制备用手机配对信息"
                title="复制备用手机配对信息"
              >
                {pairingCopyStatus.startsWith("已复制") ? <CheckCircle2 size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
                备用复制
              </button>
            </div>
            <small>优先扫码；复制只是备用，不会在界面展开 token。</small>
            {pairingCopyStatus ? (
              <small className="mobile-pairing__copy-status" role="status">
                {pairingCopyStatus}
              </small>
            ) : null}
            {transportWarning ? (
              <small className="mobile-pairing__error" role="status">
                {transportWarning}
              </small>
            ) : null}
            {transportSummary ? (
              <div
                className={`mobile-pairing__transport mobile-pairing__transport--${transportSummary.tone}`}
                aria-label="手机连接安全状态"
              >
                <div className="mobile-pairing__transport-head">
                  {transportSummary.tone === "ready" ? (
                    <ShieldCheck size={14} aria-hidden="true" />
                  ) : (
                    <AlertCircle size={14} aria-hidden="true" />
                  )}
                  <strong>{transportSummary.label}</strong>
                </div>
                <span>{transportSummary.detail}</span>
                <dl>
                  <div>
                    <dt>HTTPS</dt>
                    <dd>{transportSummary.origin}</dd>
                  </div>
                  <div>
                    <dt>WSS</dt>
                    <dd>{transportSummary.wssPaths.join(" · ")}</dd>
                  </div>
                  {transportSummary.fingerprint ? (
                    <div>
                      <dt>证书 SHA-256</dt>
                      <dd className="mobile-pairing__fingerprint">{transportSummary.fingerprint}</dd>
                    </div>
                  ) : null}
                </dl>
                {transportSummary.trustNotice ? <span>{transportSummary.trustNotice}</span> : null}
                <span>真机证据仍需单独采集：扫码配对、审批 WSS、远程屏幕、输入授权、撤销和过期都不会由桌面 UI 自动标记通过。</span>
              </div>
            ) : null}
          </div>
        ) : (
          <small>先生成二维码，然后打开手机 App 扫码。无需手动输入局域网地址或 token。</small>
        )}
        <small>HTTPS/WSS 会直接用于手机连接；局域网 HTTP 会被拦截，请在电脑端启用 HTTPS/WSS 后重新生成。</small>
        {pairedDevices.length ? (
          <ul className="mobile-pairing__devices" aria-label="已配对手机">
            {pairedDevices.map((device) => {
              const activeGrant = activeRemoteInputGrantForDevice(device);
              return (
                <li key={device.device_id}>
                  <div className="mobile-pairing__device-main">
                    <span>{device.device_name || device.device_id}</span>
                    <small>{device.device_id}</small>
                    <div className="mobile-pairing__chips" aria-label="设备权限和状态">
                      {mobileDevicePermissionChips(device, remoteDesktopEnabled).map((chip) => (
                        <em key={chip}>{chip}</em>
                      ))}
                    </div>
                    {activeGrant ? (
                      <small className="mobile-pairing__grant-status">
                        远程点击授权至 {formatDeviceDate(activeGrant.expires_at)}
                      </small>
                    ) : null}
                    <small>
                      {device.revoked_at
                        ? `已于 ${formatDeviceDate(device.revoked_at)} 断开`
                        : `最后同步 ${formatDeviceDate(device.updated_at)}`}
                    </small>
                  </div>
                  <div className="mobile-pairing__device-actions">
                    {activeGrant ? (
                      <button
                        type="button"
                        className="button button--ghost mobile-pairing__action mobile-pairing__action--remote"
                        onClick={() => onRevokeRemoteInputGrant(device, activeGrant)}
                        disabled={remoteInputRevokingGrantId === activeGrant.id || revokingDeviceId === device.device_id}
                        aria-label={`撤销手机 ${device.device_name || device.device_id} 的远程点击授权`}
                        title="撤销远程点击授权"
                      >
                        {remoteInputRevokingGrantId === activeGrant.id ? (
                          <Loader2 className="settings-spinner" size={14} aria-hidden="true" />
                        ) : (
                          <XCircle size={14} aria-hidden="true" />
                        )}
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="button button--ghost mobile-pairing__action mobile-pairing__action--remote"
                        onClick={() => onCreateRemoteInputGrant(device)}
                        disabled={
                          !mobileDeviceCanReceiveRemoteInputGrant(device, remoteDesktopEnabled) ||
                          remoteInputGrantingDeviceId === device.device_id ||
                          revokingDeviceId === device.device_id
                        }
                        aria-label={`授权手机 ${device.device_name || device.device_id} 远程点击`}
                        title="授权远程点击"
                      >
                        {remoteInputGrantingDeviceId === device.device_id ? (
                          <Loader2 className="settings-spinner" size={14} aria-hidden="true" />
                        ) : (
                          <MousePointer2 size={14} aria-hidden="true" />
                        )}
                      </button>
                    )}
                    <button
                      type="button"
                      className="button button--ghost mobile-pairing__action mobile-pairing__action--revoke"
                      onClick={() => onRevokePairedDevice(device)}
                      disabled={revokingDeviceId === device.device_id}
                      aria-label={`断开手机 ${device.device_name || device.device_id}`}
                      title="断开手机"
                    >
                      {revokingDeviceId === device.device_id ? (
                        <Loader2 className="settings-spinner" size={14} aria-hidden="true" />
                      ) : (
                        <Trash2 size={14} aria-hidden="true" />
                      )}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <small>暂无已配对设备。</small>
        )}
        <small>
          指针按钮会给该手机 5 分钟远程点击授权；每次点击仍会在电脑端生成审批。断开设备会立即撤销它的审批、屏幕查看和远程输入令牌；重新使用需要重新配对。
        </small>
        {pairingError ? <small className="mobile-pairing__error">{pairingError}</small> : null}
      </div>
      <Suspense fallback={<PairingVisualCodeFallback code={pairing?.code} />}>
        <PairingVisualCode code={pairing?.code} qrContent={pairingQrContent} />
      </Suspense>
      <button className="button button--secondary" onClick={onCreatePairingCode} disabled={isPairing} type="button">
        {isPairing ? <Loader2 className="settings-spinner" size={16} aria-hidden="true" /> : <KeyRound size={16} aria-hidden="true" />}
        生成配对码
      </button>
    </div>
  );
}
