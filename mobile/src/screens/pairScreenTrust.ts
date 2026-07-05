import { formatTlsFingerprint, type BaseUrlSecurity } from "../api/client";

export function requiresServerTrustConfirmation(security: BaseUrlSecurity): boolean {
  return Boolean(security.serverTls?.requiresTrust);
}

export function serverTrustConfirmationMessage(security: BaseUrlSecurity): string {
  const tls = security.serverTls;
  const fingerprint = formatTlsFingerprint(tls?.fingerprintSha256);
  return [
    "这台电脑使用了本地安全设置，手机需要你确认一次。",
    fingerprint ? `电脑指纹：${fingerprint}` : "如果你不确定，请先取消，并在电脑端重新生成配对信息。",
    "确认它和电脑端显示的一致后再保存；不确定时请取消。",
  ].join("\n\n");
}
