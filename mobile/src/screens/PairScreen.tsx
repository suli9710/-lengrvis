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

import { isLoopbackBaseUrl, pairWithBackend, type PairingSession } from "../api/client";
import { saveSession } from "../store/auth";

export function PairScreen({ onPaired }: { onPaired: (session: PairingSession) => void }) {
  const [baseUrl, setBaseUrl] = useState("");
  const [pairCode, setPairCode] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState("");

  const handlePair = async () => {
    const code = pairCode.replace(/[^a-z0-9]/gi, "").toLowerCase();
    if (code.length !== 6) {
      Alert.alert("配对码", "请输入电脑端 Mavris 显示的 6 位配对码。");
      return;
    }
    if (!baseUrl.trim()) {
      Alert.alert("电脑地址", "请输入电脑端 Mavris 显示的地址，例如 http://192.168.1.20:8000。");
      return;
    }
    if (isLoopbackBaseUrl(baseUrl)) {
      Alert.alert("电脑地址", "这个地址指向手机本机，请使用电脑端 Mavris 显示的地址。");
      return;
    }
    setIsBusy(true);
    setError("");
    try {
      const nextSession = await pairWithBackend(baseUrl.trim(), code, Device.deviceName ?? "安卓设备");
      await saveSession(nextSession);
      setPairCode("");
      onPaired(nextSession);
    } catch (currentError) {
      setError(errorMessage(currentError));
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
        <Text style={styles.title}>连接 Mavris</Text>
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
            onPress={handlePair}
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

function errorMessage(error: unknown): string {
  if (!(error instanceof Error)) return "无法连接。请检查地址和配对码后重试。";
  const message = error.message.toLowerCase();
  if (message.includes("fetch") || message.includes("network")) {
    return "无法连接到电脑。请确认 Mavris 已打开，且地址正确。";
  }
  if (message.includes("code") || message.includes("invalid") || message.includes("expired")) {
    return "配对码无效。请检查 Mavris 中显示的配对码后重试。";
  }
  return "无法连接。请检查地址和配对码后重试。";
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
  pressed: {
    opacity: 0.72,
  },
});
