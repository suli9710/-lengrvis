import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  SafeAreaView,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import * as Device from "expo-device";
import { Link2, QrCode, Smartphone } from "lucide-react-native";

import {
  AuthExpiredError,
  BackendHttpError,
  describeBaseUrlSecurity,
  ForbiddenError,
  formatTlsFingerprint,
  InsecureLanBaseUrlError,
  pairWithBackend,
  type BaseUrlSecurity,
  type PairingSession,
} from "../api/client";
import {
  PairingPayloadParseError,
  classifyPairingPayloadSecurity,
  parsePairingPayload,
  type PairingPayload,
  type PairingPayloadSecurityState,
} from "../api/pairingPayload";
import { saveSession } from "../store/auth";

interface SecurityNotice {
  tone: "safe" | "warning" | "danger";
  title: string;
  detail: string;
}

interface PairingFailureNotice {
  title: string;
  detail: string;
  action: string;
  checks?: Array<{
    title: string;
    detail: string;
  }>;
}

export function PairScreen({ onPaired }: { onPaired: (session: PairingSession) => void }) {
  const [baseUrl, setBaseUrl] = useState("");
  const [pairCode, setPairCode] = useState("");
  const [pairingPayload, setPairingPayload] = useState("");
  const [detectedPayload, setDetectedPayload] = useState<PairingPayload | null>(null);
  const [showManualEntry, setShowManualEntry] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [failure, setFailure] = useState<PairingFailureNotice | null>(null);
  const [showScanFallback, setShowScanFallback] = useState(false);

  const securityHint = showManualEntry ? baseUrlSecurityHint(baseUrl) : null;
  const detectedPayloadSecurity = detectedPayload ? classifyPairingPayloadSecurity(detectedPayload) : null;
  const isDetectedPayloadBlocked = Boolean(!showManualEntry && detectedPayloadSecurity && !detectedPayloadSecurity.canPair);
  const canSubmit = !isBusy && !isDetectedPayloadBlocked;

  const persistPairedSession = async (session: PairingSession) => {
    setIsBusy(true);
    try {
      await saveSession(session);
      setPairCode("");
      setPairingPayload("");
      setDetectedPayload(null);
      onPaired(session);
    } catch (currentError) {
      setFailure(pairingFailureNotice(currentError, session.baseUrlSecurity));
    } finally {
      setIsBusy(false);
    }
  };

  const handlePayloadChange = (value: string) => {
    setPairingPayload(value);
    setFailure(null);
    if (!value.trim()) {
      setDetectedPayload(null);
      if (!showManualEntry) {
        setBaseUrl("");
        setPairCode("");
      }
      return;
    }
    try {
      const payload = parsePairingPayload(value);
      applyPayload(payload);
    } catch {
      setDetectedPayload(null);
      if (!showManualEntry) {
        setBaseUrl("");
        setPairCode("");
      }
    }
  };

  const applyPayload = (payload: PairingPayload) => {
    setDetectedPayload(payload);
    setBaseUrl(payload.baseUrl);
    setPairCode(payload.code);
  };

  const handlePair = async () => {
    setFailure(null);

    let nextBaseUrl = baseUrl;
    let nextPairCode = pairCode;
    if (!showManualEntry && pairingPayload.trim()) {
      try {
        const payload = parsePairingPayload(pairingPayload);
        applyPayload(payload);
        nextBaseUrl = payload.baseUrl;
        nextPairCode = payload.code;
      } catch (currentError) {
        setFailure(pairingFailureNotice(currentError));
        return;
      }
    }

    const code = nextPairCode.replace(/[^a-z0-9]/gi, "").toLowerCase();
    if (code.length !== 6) {
      setFailure({
        title: "配对码不可用",
        detail: "手机没有识别到电脑端生成的 6 位配对码。",
        action: "请粘贴电脑端最新的配对信息；手动输入只作为备用方式。",
      });
      return;
    }
    if (!nextBaseUrl.trim()) {
      setFailure({
        title: "缺少电脑地址",
        detail: "手机还不知道要连接哪台电脑。",
        action: "请粘贴电脑端生成的配对信息；手动输入只作为备用方式。",
      });
      return;
    }

    let baseUrlSecurity: BaseUrlSecurity;
    try {
      baseUrlSecurity = describeBaseUrlSecurity(nextBaseUrl);
    } catch (currentError) {
      setFailure(pairingFailureNotice(currentError));
      return;
    }
    if (baseUrlSecurity.isLoopback) {
      setFailure({
        title: "这个地址不是电脑",
        detail: "这个地址会指向手机自己，所以手机找不到你的电脑。",
        action: "请使用电脑端 Lengrvis 生成的配对信息重新连接。",
      });
      return;
    }
    if (baseUrlSecurity.isInsecureLan) {
      setFailure({
        title: "需要启用 HTTPS",
        detail: "为了保护手机 token、远程输入授权和屏幕连接，非本机局域网不能再通过 HTTP 配对。",
        action: "请在电脑端启用 HTTPS/WSS 或使用受信任证书后，重新生成配对信息。",
      });
      return;
    }

    setIsBusy(true);
    try {
      const nextSession = await pairWithBackend(baseUrlSecurity.normalizedBaseUrl, code, Device.deviceName ?? "安卓设备");
      if (requiresServerTrustConfirmation(nextSession)) {
        Alert.alert("核对电脑证书", serverTrustConfirmationMessage(nextSession), [
          { text: "取消", style: "cancel" },
          { text: "已核对并保存", onPress: () => void persistPairedSession(nextSession) },
        ]);
        return;
      }
      await persistPairedSession(nextSession);
    } catch (currentError) {
      setFailure(pairingFailureNotice(currentError, baseUrlSecurity));
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar barStyle="dark-content" backgroundColor="#f7f9fb" />
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={styles.screen}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <View style={styles.pairIcon}>
            <Smartphone size={34} color="#17323a" />
          </View>
          <Text style={styles.title}>连接 Lengrvis</Text>
          <Text style={styles.subtitle}>优先使用电脑端生成的配对信息，手机会自动识别电脑和配对码。</Text>

          <View style={styles.form}>
            <View style={styles.payloadPanel}>
              <View style={styles.payloadHeader}>
                <View style={styles.payloadIcon}>
                  <QrCode size={22} color="#0e5f76" />
                </View>
                <View style={styles.payloadCopy}>
                  <Text style={styles.sectionTitle}>粘贴二维码内容配对</Text>
                  <Text style={styles.sectionDetail}>复制电脑端二维码内容后粘贴，手机会自动识别地址和配对码；此移动包暂未内置相机扫码。</Text>
                </View>
              </View>
              <View style={styles.scanActionRow}>
                <Pressable
                  accessibilityRole="button"
                  onPress={() => {
                    setShowScanFallback(true);
                    setFailure(null);
                  }}
                  style={({ pressed }) => [styles.scanButton, pressed && styles.pressed]}
                >
                  <QrCode size={16} color="#0e5f76" />
                  <Text style={styles.scanButtonText}>查看粘贴方式</Text>
                </Pressable>
                <Text style={styles.scanActionText}>不会打开相机。</Text>
              </View>
              {showScanFallback ? (
                <View style={styles.scanFallbackNotice}>
                  <Text style={styles.scanFallbackTitle}>没有相机扫码组件</Text>
                  <Text style={styles.scanFallbackText}>这不是扫码入口。复制电脑端二维码内容后粘贴到下方即可识别；真机相机扫码仍未内置。</Text>
                </View>
              ) : null}
              <TextInput
                autoCapitalize="none"
                autoCorrect={false}
                multiline
                onChangeText={handlePayloadChange}
                placeholder="粘贴电脑端二维码内容或配对信息"
                style={[styles.input, styles.payloadInput]}
                textAlignVertical="top"
                value={pairingPayload}
              />
              {detectedPayload && detectedPayloadSecurity ? <PairingPayloadStatus payload={detectedPayload} state={detectedPayloadSecurity} /> : null}
            </View>

            <Pressable accessibilityRole="button" onPress={() => setShowManualEntry((current) => !current)} style={styles.manualToggle}>
              <Text style={styles.manualToggleText}>{showManualEntry ? "收起手动输入" : "无法粘贴？手动输入"}</Text>
            </Pressable>

            {showManualEntry ? (
              <View style={styles.manualPanel}>
                <Text style={styles.label}>电脑地址</Text>
                <TextInput
                  autoCapitalize="none"
                  autoCorrect={false}
                  inputMode="url"
                  onChangeText={(value) => {
                    setBaseUrl(value);
                    setFailure(null);
                  }}
                  placeholder="电脑端显示的地址"
                  style={styles.input}
                  value={baseUrl}
                />
                {securityHint ? (
                  <View
                    style={[
                      styles.securityNotice,
                      securityHint.tone === "safe" && styles.securityNoticeSafe,
                      securityHint.tone === "danger" && styles.securityNoticeDanger,
                    ]}
                  >
                    <Text style={styles.securityNoticeTitle}>{securityHint.title}</Text>
                    <Text style={styles.securityNoticeText}>{securityHint.detail}</Text>
                  </View>
                ) : null}
                <Text style={styles.label}>配对码</Text>
                <TextInput
                  autoCapitalize="none"
                  autoCorrect={false}
                  onChangeText={(value) => {
                    setPairCode(value.replace(/[^a-z0-9]/gi, "").toLowerCase());
                    setFailure(null);
                  }}
                  placeholder="6 位字母或数字"
                  style={[styles.input, styles.codeInput]}
                  value={pairCode}
                />
              </View>
            ) : null}

            {failure ? <PairingFailure notice={failure} /> : null}

            <Pressable
              accessibilityRole="button"
              accessibilityState={{ disabled: !canSubmit, busy: isBusy }}
              disabled={!canSubmit}
              onPress={() => void handlePair()}
              style={({ pressed }) => [styles.primaryButton, pressed && styles.pressed, !canSubmit && styles.disabledButton]}
            >
              {isBusy ? <ActivityIndicator color="#ffffff" /> : <Link2 size={18} color="#ffffff" />}
              <Text style={styles.primaryButtonText}>{isDetectedPayloadBlocked ? "等待 HTTPS/WSS 配对信息" : "连接手机"}</Text>
            </Pressable>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function PairingFailure({ notice }: { notice: PairingFailureNotice }) {
  return (
    <View style={styles.failureNotice}>
      <Text style={styles.failureTitle}>{notice.title}</Text>
      <Text style={styles.failureText}>{notice.detail}</Text>
      {notice.checks?.map((check) => (
        <Text key={check.title} style={styles.failureCheck}>
          <Text style={styles.failureCheckTitle}>{check.title}：</Text>
          {check.detail}
        </Text>
      ))}
      <Text style={styles.failureAction}>{notice.action}</Text>
    </View>
  );
}

function PairingPayloadStatus({ payload, state }: { payload: PairingPayload; state: PairingPayloadSecurityState }) {
  const notice = pairingPayloadNotice(payload, state);
  const isDanger = notice.tone === "danger";
  const isWarning = notice.tone === "warning";
  return (
    <View style={[styles.detectedNotice, isWarning && styles.detectedNoticeWarning, isDanger && styles.detectedNoticeDanger]}>
      <Text style={[styles.detectedTitle, isDanger && styles.detectedTitleDanger]}>{notice.title}</Text>
      <Text style={[styles.detectedText, isDanger && styles.detectedTextDanger]}>{notice.detail}</Text>
    </View>
  );
}

function pairingPayloadNotice(payload: PairingPayload, state: PairingPayloadSecurityState): SecurityNotice {
  const validityText = payloadValidityText(payload);
  if (state.status === "requires_https_wss") {
    return {
      tone: "danger",
      title: "需要启用 HTTPS/WSS",
      detail: `${validityText} 但电脑地址是局域网 HTTP，不能直接连接；请在电脑端启用 HTTPS/WSS 或使用受信任证书后重新生成。`,
    };
  }
  if (state.status === "loopback") {
    return {
      tone: "danger",
      title: "这不是电脑地址",
      detail: `${validityText} 但这个地址会指向手机自己，请使用电脑端重新生成的配对信息。`,
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
      detail: `${validityText} 手机将使用 HTTPS/WSS 加密连接电脑。`,
    };
  }
  return {
    tone: "safe",
    title: "已识别电脑和配对码",
    detail: validityText,
  };
}

function baseUrlSecurityHint(value: string): SecurityNotice | null {
  if (!value.trim()) return null;
  try {
    const security = describeBaseUrlSecurity(value);
    if (security.isHttps) {
      return {
        tone: "safe",
        title: "安全连接",
        detail: "手机会通过加密连接访问电脑。若电脑使用本地证书，首次连接需要你核对证书。",
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
        title: "需要启用 HTTPS",
        detail: "非本机局域网 HTTP 已被阻断。请在电脑端启用 HTTPS/WSS 或使用受信任证书后重新生成配对信息。",
      };
    }
    return null;
  } catch {
    return null;
  }
}

function requiresServerTrustConfirmation(session: PairingSession): boolean {
  return Boolean(session.baseUrlSecurity.serverTls?.requiresTrust);
}

function serverTrustConfirmationMessage(session: PairingSession): string {
  const tls = session.baseUrlSecurity.serverTls;
  const fingerprint = formatTlsFingerprint(tls?.fingerprintSha256);
  return [
    "电脑返回的 HTTPS 证书需要你手动核对或信任。移动端会保存本次返回的信任信息用于展示，不会安装系统证书。",
    fingerprint ? `SHA-256 指纹：${fingerprint}` : "后端未提供证书指纹，请在电脑端核对证书信息后再保存。",
    "如果手机仍无法连接，请先在手机系统或浏览器中信任该证书，或在桌面端改用受信任证书。",
  ].join("\n\n");
}

function payloadValidityText(payload: PairingPayload): string {
  if (!payload.expiresAt) return "配对信息已识别。";
  const expiry = new Date(payload.expiresAt);
  if (Number.isNaN(expiry.getTime())) return "配对信息已识别。";
  return `${expiry.toLocaleTimeString()} 前有效。`;
}

function pairingFailureNotice(error: unknown, security?: BaseUrlSecurity): PairingFailureNotice {
  if (error instanceof PairingPayloadParseError) {
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
      detail: "手机没有从这段内容里识别到电脑和 6 位配对码。",
      action: "请粘贴电脑端完整配对信息，或在电脑端重新生成。",
    };
  }
  if (error instanceof InsecureLanBaseUrlError) {
    return {
      title: "需要启用 HTTPS",
      detail: "为了保护手机 token、远程输入授权和屏幕连接，非本机局域网不能再通过 HTTP 配对。",
      action: "请在电脑端启用 HTTPS/WSS 或使用受信任证书后，重新生成配对信息。",
    };
  }

  const status = errorStatus(error);
  const message = error instanceof Error ? error.message.toLowerCase() : "";
  if (error instanceof ForbiddenError || status === 403) {
    return {
      title: "权限不足",
      detail: "电脑端拒绝了这台手机的配对或授权请求。",
      action: "请在电脑端重新生成配对信息，并确认移动端权限没有被关闭。",
    };
  }
  if (error instanceof AuthExpiredError || status === 401 || message.includes("expired") || message.includes("invalid or expired")) {
    return {
      title: "配对码已过期",
      detail: "配对信息里的 6 位配对码只能短时间使用，过期后会被电脑端拒绝。",
      action: "请在电脑端重新生成配对信息后再试。",
    };
  }
  if (status === 422 || message.includes("url") || message.includes("address") || message.includes("must be 6 characters")) {
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
      title: "无法信任电脑证书",
      detail: "手机没有和这台电脑建立安全连接。",
      action: "请在手机系统或浏览器中信任电脑端证书，或让电脑端使用受信任证书。",
    };
  }
  if (isNetworkError(message)) {
    return {
      title: "手机找不到电脑",
      detail: "手机没有连上电脑端 Lengrvis。",
      checks: [
        { title: "不在同一网络", detail: "手机和电脑不在同一个 Wi-Fi 时会出现这个提示。" },
        { title: "后端未启动", detail: "如果已经同网，请在电脑端打开 Lengrvis，并保持配对页处于可用状态。" },
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

function errorStatus(error: unknown): number {
  return error && typeof error === "object" && "status" in error && typeof (error as { status?: unknown }).status === "number"
    ? (error as { status: number }).status
    : 0;
}

function isNetworkOrCertificateError(message: string): boolean {
  return isNetworkError(message) || message.includes("ssl") || message.includes("cert");
}

function isNetworkError(message: string): boolean {
  return (
    message.includes("fetch") ||
    message.includes("network") ||
    message.includes("failed") ||
    message.includes("load failed") ||
    message.includes("abort") ||
    message.includes("timeout")
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: "#f7f9fb",
  },
  screen: {
    flex: 1,
  },
  content: {
    flexGrow: 1,
    justifyContent: "center",
    padding: 24,
    paddingBottom: 34,
  },
  pairIcon: {
    width: 68,
    height: 68,
    borderRadius: 18,
    backgroundColor: "#e6f0ef",
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 22,
  },
  title: {
    color: "#17323a",
    fontSize: 30,
    fontWeight: "800",
  },
  subtitle: {
    color: "#52616d",
    fontSize: 16,
    lineHeight: 23,
    marginTop: 8,
  },
  form: {
    marginTop: 26,
    gap: 12,
  },
  payloadPanel: {
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#cbd9df",
    backgroundColor: "#ffffff",
    padding: 14,
    gap: 12,
  },
  payloadHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 11,
  },
  payloadIcon: {
    width: 42,
    height: 42,
    borderRadius: 8,
    backgroundColor: "#e8f3f4",
    alignItems: "center",
    justifyContent: "center",
  },
  payloadCopy: {
    flex: 1,
  },
  sectionTitle: {
    color: "#1e3139",
    fontSize: 16,
    fontWeight: "900",
  },
  sectionDetail: {
    color: "#60707b",
    fontSize: 13,
    lineHeight: 19,
    marginTop: 2,
  },
  scanActionRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    flexWrap: "wrap",
  },
  scanButton: {
    minHeight: 38,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#9ec6cf",
    backgroundColor: "#edf8fb",
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 7,
    paddingHorizontal: 12,
  },
  scanButtonText: {
    color: "#0e5f76",
    fontSize: 14,
    fontWeight: "900",
  },
  scanActionText: {
    flexShrink: 1,
    color: "#60707b",
    fontSize: 12,
    lineHeight: 18,
  },
  scanFallbackNotice: {
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#d6c17d",
    backgroundColor: "#fff8df",
    paddingHorizontal: 12,
    paddingVertical: 10,
    gap: 3,
  },
  scanFallbackTitle: {
    color: "#56451b",
    fontSize: 13,
    fontWeight: "900",
  },
  scanFallbackText: {
    color: "#605333",
    fontSize: 12,
    lineHeight: 18,
  },
  label: {
    color: "#31424c",
    fontSize: 13,
    fontWeight: "800",
  },
  input: {
    minHeight: 52,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#cbd4d9",
    backgroundColor: "#ffffff",
    color: "#1f2933",
    fontSize: 16,
    paddingHorizontal: 14,
  },
  payloadInput: {
    minHeight: 92,
    paddingTop: 12,
    lineHeight: 22,
  },
  codeInput: {
    fontSize: 24,
    fontWeight: "800",
    letterSpacing: 0,
    textAlign: "center",
  },
  detectedNotice: {
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#a8c7b7",
    backgroundColor: "#edf8f1",
    paddingHorizontal: 12,
    paddingVertical: 10,
    gap: 3,
  },
  detectedNoticeWarning: {
    borderColor: "#d6c17d",
    backgroundColor: "#fff8df",
  },
  detectedNoticeDanger: {
    borderColor: "#d2a0a7",
    backgroundColor: "#fff0f2",
  },
  detectedTitle: {
    color: "#244333",
    fontSize: 13,
    fontWeight: "900",
  },
  detectedTitleDanger: {
    color: "#782b36",
  },
  detectedText: {
    color: "#46594f",
    fontSize: 12,
    lineHeight: 18,
  },
  detectedTextDanger: {
    color: "#5c3a3f",
  },
  manualToggle: {
    alignSelf: "flex-start",
    minHeight: 36,
    justifyContent: "center",
  },
  manualToggleText: {
    color: "#0e5f76",
    fontSize: 14,
    fontWeight: "800",
  },
  manualPanel: {
    gap: 10,
  },
  primaryButton: {
    minHeight: 52,
    borderRadius: 8,
    backgroundColor: "#0e5f76",
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 9,
    marginTop: 2,
  },
  disabledButton: {
    opacity: 0.82,
  },
  primaryButtonText: {
    color: "#ffffff",
    fontSize: 16,
    fontWeight: "800",
  },
  failureNotice: {
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#d2a0a7",
    backgroundColor: "#fff5f6",
    paddingHorizontal: 12,
    paddingVertical: 11,
    gap: 5,
  },
  failureTitle: {
    color: "#782b36",
    fontSize: 14,
    fontWeight: "900",
  },
  failureText: {
    color: "#5c3a3f",
    fontSize: 13,
    lineHeight: 19,
  },
  failureCheck: {
    color: "#5c3a3f",
    fontSize: 13,
    lineHeight: 19,
  },
  failureCheckTitle: {
    color: "#782b36",
    fontWeight: "900",
  },
  failureAction: {
    color: "#473238",
    fontSize: 13,
    fontWeight: "800",
    lineHeight: 19,
  },
  securityNotice: {
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#d9b24c",
    backgroundColor: "#fff7dd",
    paddingHorizontal: 12,
    paddingVertical: 10,
    gap: 3,
  },
  securityNoticeSafe: {
    borderColor: "#a8c7b7",
    backgroundColor: "#edf8f1",
  },
  securityNoticeDanger: {
    borderColor: "#d2a0a7",
    backgroundColor: "#fff0f2",
  },
  securityNoticeTitle: {
    color: "#25313a",
    fontSize: 13,
    fontWeight: "900",
  },
  securityNoticeText: {
    color: "#4a5660",
    fontSize: 12,
    lineHeight: 18,
  },
  pressed: {
    opacity: 0.72,
  },
});
