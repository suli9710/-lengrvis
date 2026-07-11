import {
  AuthExpiredError,
  BackendHttpError,
  describeBaseUrlSecurity,
  ForbiddenError,
  InsecureLanBaseUrlError,
  type BaseUrlSecurity,
} from "../api/client";
import { PAIRING_CODE_LENGTH, PairingPayloadParseError, type PairingPayload, type PairingPayloadSecurityState } from "../api/pairingPayload";
import type { PairingFailureNotice, PairingFailureSource, SecurityNotice } from "./pairScreenTypes";

export function pairingPayloadNotice(payload: PairingPayload, state: PairingPayloadSecurityState): SecurityNotice {
  const validityText = payloadValidityText(payload);
  if (state.status === "requires_https_wss") {
    return {
      tone: "danger",
      title: "需要安全连接",
      detail: `${validityText} 但这个电脑地址还不是安全连接。请在电脑端开启安全连接后重新生成。`,
    };
  }
  if (state.status === "loopback") {
    return {
      tone: "danger",
      title: "这不是电脑地址",
      detail: `${validityText} 但这个地址会指向手机自己，请使用电脑端重新生成的配对信息。`,
    };
  }
  if (state.status === "expired") {
    return {
      tone: "danger",
      title: "配对码已过期",
      detail: "这份配对信息已经过期，手机不会继续发送请求。请在电脑端重新生成后再扫码或粘贴。",
    };
  }
  if (state.status === "invalid_address") {
    return {
      tone: "danger",
      title: "地址格式需要重新生成",
      detail: "已识别配对码，但电脑地址格式不可用。请粘贴电脑端生成的完整配对信息。",
    };
  }
  if (state.security?.isHttps) {
    return {
      tone: "safe",
      title: "已识别安全配对信息",
      detail: `${validityText} 手机将加密连接这台电脑。`,
    };
  }
  return {
    tone: "safe",
    title: "已识别电脑和配对码",
    detail: validityText,
  };
}

export function baseUrlSecurityHint(value: string, metadata?: PairingPayload["security"]): SecurityNotice | null {
  if (!value.trim()) return null;
  try {
    const security = describeBaseUrlSecurity(value, metadata);
    if (security.isHttps) {
      return {
        tone: "safe",
        title: "安全连接已开启",
        detail: "手机会加密连接这台电脑。首次连接时可能需要你确认一次。",
      };
    }
    if (security.isLoopback) {
      return {
        tone: "danger",
        title: "这不是电脑地址",
        detail: "这个地址会指向手机自己，请改用电脑端生成的配对信息。",
      };
    }
    if (security.isInsecureLan) {
      return {
        tone: "danger",
        title: "需要安全连接",
        detail: "这个电脑地址还不是安全连接。请在电脑端开启安全连接后重新生成配对信息。",
      };
    }
    return null;
  } catch {
    return null;
  }
}

export function cameraPermissionFailureNotice(canAskAgain: boolean | undefined): PairingFailureNotice {
  if (canAskAgain === false) {
    return {
      title: "需要在系统设置打开相机",
      detail: "手机已经关闭了 Lengrvis 的相机权限，应用内暂时不能再次弹出授权窗口。",
      action: "请到系统设置里允许 Lengrvis 使用相机；不方便授权时，也可以复制电脑端二维码内容后粘贴。",
    };
  }
  return {
    title: "需要相机权限",
    detail: "手机没有授权 Lengrvis 使用相机，因此暂时不能扫码。",
    action: "请再次点击“打开相机扫码”并允许相机权限；也可以直接粘贴电脑端二维码内容。",
  };
}

export function cameraUnavailableFailureNotice(): PairingFailureNotice {
  return {
    title: "无法打开相机",
    detail: "手机暂时没有可用的相机取景框。",
    action: "请检查系统相机权限后重试；也可以直接粘贴电脑端二维码内容。",
  };
}

export function pairingButtonLabel({
  isBusy,
  hasSubmitInput,
  showManualEntry,
  blockedStatus,
}: {
  isBusy: boolean;
  hasSubmitInput: boolean;
  showManualEntry: boolean;
  blockedStatus?: PairingPayloadSecurityState["status"];
}): string {
  if (isBusy) return "正在连接";
  if (blockedStatus) return blockedPairingButtonLabel(blockedStatus);
  if (!hasSubmitInput) return showManualEntry ? `输入电脑地址和 ${PAIRING_CODE_LENGTH} 位配对码` : "先扫码或粘贴配对信息";
  return "连接手机";
}

export function blockedPairingPayloadFailureNotice(status: PairingPayloadSecurityState["status"]): PairingFailureNotice {
  if (status === "requires_https_wss") {
    return {
      title: "需要安全连接",
      detail: "手机识别到这份配对信息没有使用安全连接，因此不会发送配对请求。",
      action: "请在电脑端开启安全连接后，重新生成配对信息。",
    };
  }
  if (status === "loopback") {
    return {
      title: "这个地址不是电脑",
      detail: "这份配对信息里的地址会指向手机自己，所以手机不会继续连接。",
      action: "请使用电脑端 Lengrvis 生成的配对信息重新连接。",
    };
  }
  if (status === "expired") {
    return {
      title: "配对码已过期",
      detail: "这份配对信息已经过期，手机不会把旧配对码发给电脑端。",
      action: "请回到电脑端重新生成配对信息，再扫码或粘贴。",
    };
  }
  return {
    title: "配对信息不可用",
    detail: "手机识别到这份配对信息不完整或地址不可用，因此不会发送请求。",
    action: "请粘贴电脑端刚生成的完整配对信息。",
  };
}

export function invalidBaseUrlFormatNotice(): SecurityNotice {
  return {
    tone: "danger",
    title: "电脑地址格式错误",
    detail: "请输入电脑端显示的地址；地址必须是 http 或 https，不能包含空格或换行。",
  };
}

export function baseUrlInputCleanedNotice(): PairingFailureNotice {
  return {
    title: "已整理电脑地址",
    detail: "电脑地址不能包含空格或换行，手机已自动移除这些字符。",
    action: "请确认地址仍和电脑端显示的一致，然后继续输入配对码。",
  };
}

export function pairingInputTooLongNotice(kind: "payload" | "baseUrl" | "scan"): PairingFailureNotice {
  if (kind === "payload") {
    return {
      title: "配对信息太长",
      detail: "这段内容超过了手机允许识别的长度，因此没有继续解析。",
      action: "请只粘贴电脑端刚生成的二维码内容，或改用手动输入。",
    };
  }
  if (kind === "scan") {
    return {
      title: "二维码内容太长",
      detail: "手机扫到的内容不像 Lengrvis 配对信息，因此没有继续处理。",
      action: "请对准电脑端 Lengrvis 配对页的二维码；如果仍失败，请复制二维码内容后粘贴。",
    };
  }
  return {
    title: "电脑地址太长",
    detail: "手机已经停止接收超出长度限制的地址内容。",
    action: "请只输入电脑端显示的地址，不要粘贴额外说明。",
  };
}

export function pairedSessionStorageFailureNotice(): PairingFailureNotice {
  return {
    title: "无法保存配对",
    detail: "手机已收到配对结果，但没有把会话安全保存下来。",
    action: "请确认系统安全存储可用，然后重新配对。",
  };
}

export function pairingFailureNotice(error: unknown, security?: BaseUrlSecurity, source: PairingFailureSource = "input"): PairingFailureNotice {
  if (error instanceof PairingPayloadParseError) {
    if (source === "scan") {
      if (error.code === "invalid_address") {
        return {
          title: "二维码里的电脑地址不可用",
          detail: "手机扫到了二维码，但里面的电脑地址格式不对。",
          action: "请回到电脑端重新生成配对二维码，再用手机扫描新的二维码。",
        };
      }
      if (error.code === "missing_address") {
        return {
          title: "二维码缺少电脑地址",
          detail: "手机扫到了配对码，但不知道要连接哪台电脑。",
          action: "请对准电脑端 Lengrvis 配对页的完整二维码；如果仍失败，请复制二维码内容后粘贴。",
        };
      }
      return {
        title: "没有识别到 Lengrvis 配对二维码",
        detail: `手机扫到的内容里没有同时包含电脑地址和 ${PAIRING_CODE_LENGTH} 位配对码。`,
        action: "请对准电脑端 Lengrvis 配对页的二维码；如果屏幕反光或太远，可以复制二维码内容后粘贴。",
      };
    }
    if (error.code === "invalid_address") {
      return {
        title: "地址格式错误",
        detail: "这段配对信息里没有可用的电脑地址。",
        action: "请粘贴电脑端刚生成的配对信息。",
      };
    }
    if (error.code === "missing_address") {
      return {
        title: "缺少电脑地址",
        detail: "这段配对信息里只有配对码，手机还不知道要连接哪台电脑。",
        action: "请在电脑端重新生成完整配对信息。",
      };
    }
    return {
      title: "配对信息不可用",
      detail: `手机没有从这段内容里识别到电脑和 ${PAIRING_CODE_LENGTH} 位配对码。`,
      action: "请粘贴电脑端完整配对信息，或在电脑端重新生成。",
    };
  }
  if (error instanceof InsecureLanBaseUrlError) {
    return {
      title: "需要安全连接",
      detail: "为了保护手机配对和远程操作，这个普通网络地址不能直接连接。",
      action: "请在电脑端开启安全连接后，重新生成配对信息。",
    };
  }

  const status = errorStatus(error);
  const message = error instanceof Error ? error.message.toLowerCase() : "";
  if (error instanceof ForbiddenError || status === 403) {
    return {
      title: "权限不足",
      detail: "电脑端拒绝了这台手机的配对或授权请求。",
      checks: [
        { title: "手机未被允许", detail: "如果电脑端有设备或权限开关，请确认这台手机可以连接。" },
        { title: "配对页已变化", detail: "旧二维码或旧配对码被撤销后，也会出现这个提示。" },
      ],
      action: "请在电脑端重新生成配对信息，并确认移动端权限没有被关闭。",
    };
  }
  if (error instanceof AuthExpiredError || status === 401 || message.includes("expired") || message.includes("invalid or expired")) {
    return {
      title: "配对码已过期",
      detail: `配对信息里的 ${PAIRING_CODE_LENGTH} 位配对码只能短时间使用，过期后会被电脑端拒绝。`,
      action: "请回到电脑端重新生成配对信息，不要复用旧截图或旧粘贴内容。",
    };
  }
  if (status === 422 || message.includes("url") || message.includes("address") || message.includes("must be 8 characters")) {
    return {
      title: "地址格式错误",
      detail: "手机无法识别这段地址或配对码。",
      action: "请粘贴电脑端生成的完整配对信息。",
    };
  }
  if (status === 429) {
    return {
      title: "尝试次数过多",
      detail: "电脑端为了保护配对入口，临时拒绝了新的尝试。",
      action: "稍等一分钟，在电脑端重新生成配对信息后再试。",
    };
  }
  if (security?.isHttps && isNetworkOrCertificateError(message)) {
    return {
      title: "需要确认这台电脑",
      detail: "手机还没有和这台电脑建立安全连接。",
      action: "请按电脑端提示确认安全连接后重试；不确定时请重新生成配对信息。",
    };
  }
  if (isNetworkError(message)) {
    return {
      title: "手机找不到电脑",
      detail: "手机没有连上电脑端 Lengrvis。",
      checks: [
        { title: "不在同一网络", detail: "手机和电脑不在同一个 Wi-Fi 时会出现这个提示。" },
        { title: "网络被隔离", detail: "公司网络、访客 Wi-Fi、VPN 或热点隔离可能会阻止手机访问电脑。" },
        { title: "电脑端未打开", detail: "如果已经同网，请在电脑端打开 Lengrvis，并保持配对页处于可用状态。" },
      ],
      action: "确认后在电脑端重新生成配对信息，再回到手机重试。",
    };
  }
  if (error instanceof BackendHttpError && status >= 500) {
    return {
      title: "电脑端服务异常",
      detail: "手机已经找到电脑，但电脑端没有完成配对请求。",
      action: "请重启电脑端 Lengrvis 后重新生成配对信息。",
    };
  }
  return {
    title: "无法完成配对",
    detail: "手机没有成功连接到电脑端 Lengrvis。",
    action: "请重新生成配对信息，并确认手机和电脑在同一网络。",
  };
}

function payloadValidityText(payload: PairingPayload): string {
  if (!payload.expiresAt) return "配对信息已识别。";
  const expiry = new Date(payload.expiresAt);
  if (Number.isNaN(expiry.getTime())) return "配对信息已识别。";
  return `${expiry.toLocaleTimeString()} 前有效。`;
}

function blockedPairingButtonLabel(status: PairingPayloadSecurityState["status"]): string {
  if (status === "requires_https_wss") return "等待安全配对信息";
  if (status === "loopback") return "等待电脑地址";
  if (status === "expired") return "重新生成配对码";
  if (status === "invalid_address") return "等待完整配对信息";
  return "检查配对信息";
}

function errorStatus(error: unknown): number {
  return error && typeof error === "object" && "status" in error && typeof (error as { status?: unknown }).status === "number"
    ? (error as { status: number }).status
    : 0;
}

export function isNetworkOrCertificateError(message: string): boolean {
  return isNetworkError(message) || message.includes("ssl") || message.includes("cert") || message.includes("tls") || message.includes("pinning");
}

export function isNetworkError(message: string): boolean {
  return (
    message.includes("fetch") ||
    message.includes("network") ||
    message.includes("failed") ||
    message.includes("load failed") ||
    message.includes("abort") ||
    message.includes("timeout")
  );
}
