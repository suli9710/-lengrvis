import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  SafeAreaView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import * as Device from "expo-device";
import { Link2, Smartphone } from "lucide-react-native";

import {
  BACKEND_TLS_DISABLED_WARNING,
  describeBaseUrlSecurity,
  formatTlsFingerprint,
  InsecureLanBaseUrlError,
  pairWithBackend,
  type BaseUrlSecurity,
  type PairingSession,
} from "../api/client";
import { saveSession } from "../store/auth";

interface SecurityNotice {
  tone: "safe" | "warning" | "danger";
  title: string;
  detail: string;
}

export function PairScreen({ onPaired }: { onPaired: (session: PairingSession) => void }) {
  const [baseUrl, setBaseUrl] = useState("");
  const [pairCode, setPairCode] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState("");

  const securityHint = baseUrlSecurityHint(baseUrl);

  const persistPairedSession = async (session: PairingSession) => {
    setIsBusy(true);
    try {
      await saveSession(session);
      setPairCode("");
      onPaired(session);
    } catch (currentError) {
      setError(errorMessage(currentError, session.baseUrlSecurity));
    } finally {
      setIsBusy(false);
    }
  };

  const handlePair = async (allowInsecureLan = false) => {
    const code = pairCode.replace(/[^a-z0-9]/gi, "").toLowerCase();
    setError("");
    if (code.length !== 6) {
      Alert.alert("配对码", "请输入电脑端 Lengrvis 显示的 6 位配对码。");
      return;
    }
    if (!baseUrl.trim()) {
      Alert.alert("电脑地址", "请输入电脑端 Lengrvis 显示的地址，例如 http://192.168.1.20:8000。");
      return;
    }
    let baseUrlSecurity: BaseUrlSecurity;
    try {
      baseUrlSecurity = describeBaseUrlSecurity(baseUrl);
    } catch (currentError) {
      setError(errorMessage(currentError));
      return;
    }
    if (baseUrlSecurity.isLoopback) {
      Alert.alert("电脑地址", "这个地址指向手机本机，请使用电脑端 Lengrvis 显示的地址。");
      return;
    }
    if (baseUrlSecurity.isInsecureLan && !allowInsecureLan) {
      Alert.alert("后端未启用 TLS（LAN HTTP）", `${baseUrlSecurity.warning}\n\n如果这是你信任的同一网络，可以继续；否则请在电脑端启用 HTTPS 后再连接。`, [
        { text: "取消", style: "cancel" },
        { text: "我信任此局域网，继续", onPress: () => void handlePair(true), style: "destructive" },
      ]);
      return;
    }
    setIsBusy(true);
    try {
      const nextSession = await pairWithBackend(baseUrlSecurity.normalizedBaseUrl, code, Device.deviceName ?? "安卓设备", {
        allowInsecureLan: baseUrlSecurity.isInsecureLan,
      });
      if (requiresServerTrustConfirmation(nextSession)) {
        Alert.alert("核对 HTTPS 证书", serverTrustConfirmationMessage(nextSession), [
          { text: "取消", style: "cancel" },
          { text: "已核对并保存", onPress: () => void persistPairedSession(nextSession) },
        ]);
        return;
      }
      await persistPairedSession(nextSession);
    } catch (currentError) {
      setError(errorMessage(currentError, baseUrlSecurity));
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar barStyle="dark-content" backgroundColor="#f6f4ee" />
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={styles.centerScreen}>
        <View style={styles.pairIcon}>
          <Smartphone size={34} color="#1f2933" />
        </View>
        <Text style={styles.title}>连接 Lengrvis</Text>
        <Text style={styles.subtitle}>使用电脑端显示的地址和 6 位配对码。</Text>

        <View style={styles.form}>
          <Text style={styles.label}>电脑地址</Text>
          <TextInput
            autoCapitalize="none"
            autoCorrect={false}
            inputMode="url"
            onChangeText={setBaseUrl}
            placeholder="http://192.168.1.20:8000"
            style={styles.input}
            value={baseUrl}
          />
          {securityHint ? (
            <View style={[styles.securityNotice, securityHint.tone === "safe" && styles.securityNoticeSafe, securityHint.tone === "danger" && styles.securityNoticeDanger]}>
              <Text style={styles.securityNoticeTitle}>{securityHint.title}</Text>
              <Text style={styles.securityNoticeText}>{securityHint.detail}</Text>
            </View>
          ) : null}
          <Text style={styles.label}>配对码</Text>
          <TextInput
            autoCapitalize="none"
            autoCorrect={false}
            onChangeText={(value) => setPairCode(value.replace(/[^a-z0-9]/gi, "").toLowerCase())}
            placeholder="6 位字母或数字"
            style={[styles.input, styles.codeInput]}
            value={pairCode}
          />
          {error ? <Text style={styles.errorText}>{error}</Text> : null}
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ disabled: isBusy, busy: isBusy }}
            disabled={isBusy}
            onPress={() => void handlePair()}
            style={({ pressed }) => [styles.primaryButton, pressed && styles.pressed]}
          >
            {isBusy ? <ActivityIndicator color="#ffffff" /> : <Link2 size={18} color="#ffffff" />}
            <Text style={styles.primaryButtonText}>连接手机</Text>
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function baseUrlSecurityHint(value: string): SecurityNotice | null {
  if (!value.trim()) return null;
  try {
    const security = describeBaseUrlSecurity(value);
    if (security.isHttps) {
      return {
        tone: "safe",
        title: "HTTPS / wss 加密连接",
        detail: "将使用加密通道连接电脑。若桌面端使用自签证书，需要先在手机系统中信任证书；本应用只保存信任信息，不安装系统证书。",
      };
    }
    if (security.isLoopback) {
      return {
        tone: "danger",
        title: "这是手机本机地址",
        detail: "127.0.0.1 或 localhost 会指向手机自己，请改用电脑端 Lengrvis 显示的局域网地址。",
      };
    }
    if (security.isInsecureLan) {
      return {
        tone: "warning",
        title: "后端未启用 TLS（LAN HTTP）",
        detail: `${BACKEND_TLS_DISABLED_WARNING} 点击连接时会要求你显式确认，确认后屏幕和远程输入 WebSocket 将使用 ws 明文通道。`,
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
    "后端返回的 HTTPS 证书需要你手动核对或信任。移动端会保存本次返回的 TLS 信任信息用于展示，不会安装系统证书。",
    fingerprint ? `SHA-256 指纹：${fingerprint}` : "后端未提供证书指纹，请在电脑端核对证书信息后再保存。",
    "如果手机仍无法连接 HTTPS，请先在手机系统或浏览器中信任该证书，或在桌面端改用受信任证书。",
  ].join("\n\n");
}

function errorMessage(error: unknown, security?: BaseUrlSecurity): string {
  if (error instanceof InsecureLanBaseUrlError) {
    return "这是未加密的局域网 HTTP 地址。请确认信任当前网络并在提示中选择继续，或改用 HTTPS。";
  }
  if (!(error instanceof Error)) return "无法完成配对。请确认手机和电脑在同一网络，后端已启动，地址和配对码无误。";
  const message = error.message.toLowerCase();
  if (security?.isHttps && (message.includes("fetch") || message.includes("network") || message.includes("failed") || message.includes("ssl") || message.includes("cert"))) {
    return "无法建立 HTTPS 连接。若桌面端使用自签证书，请先在手机系统中信任该证书，或在桌面端改用受信任证书；本应用不会安装系统证书。";
  }
  if (message.includes("fetch") || message.includes("network") || message.includes("failed")) {
    return "无法连接到这台电脑。请确认手机和电脑在同一网络，Lengrvis 后端已启动，地址和端口正确。";
  }
  if (message.includes("code") || message.includes("invalid") || message.includes("expired")) {
    return "配对码无效或已过期。请在电脑端查看最新 6 位配对码后重试。";
  }
  if (message.includes("url") || message.includes("address") || message.includes("http")) {
    return "电脑地址格式不正确。请输入 Lengrvis 显示的完整地址，例如 http://192.168.1.20:8000。";
  }
  return "无法完成配对。请确认同一网络、后端已启动，并检查地址和配对码。";
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: "#f6f4ee",
  },
  centerScreen: {
    flex: 1,
    justifyContent: "center",
    padding: 24,
  },
  pairIcon: {
    width: 68,
    height: 68,
    borderRadius: 18,
    backgroundColor: "#e7ece8",
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 22,
  },
  title: {
    color: "#1f2933",
    fontSize: 31,
    fontWeight: "800",
  },
  subtitle: {
    color: "#5f6b76",
    fontSize: 16,
    lineHeight: 23,
    marginTop: 8,
  },
  form: {
    marginTop: 30,
    gap: 10,
  },
  label: {
    color: "#3a4651",
    fontSize: 13,
    fontWeight: "700",
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
  codeInput: {
    fontSize: 24,
    fontWeight: "800",
    letterSpacing: 0,
    textAlign: "center",
  },
  primaryButton: {
    minHeight: 52,
    borderRadius: 8,
    backgroundColor: "#0e5f76",
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 9,
    marginTop: 8,
  },
  primaryButtonText: {
    color: "#ffffff",
    fontSize: 16,
    fontWeight: "800",
  },
  errorText: {
    color: "#8c2f39",
    lineHeight: 20,
  },
  warningText: {
    color: "#7a5700",
    lineHeight: 20,
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
