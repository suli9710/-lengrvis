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

import { pairWithBackend, type PairingSession } from "../api/client";
import { saveSession } from "../store/auth";

const defaultBaseUrl = "http://127.0.0.1:8000";

export function PairScreen({ onPaired }: { onPaired: (session: PairingSession) => void }) {
  const [baseUrl, setBaseUrl] = useState(defaultBaseUrl);
  const [pairCode, setPairCode] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState("");

  const handlePair = async () => {
    const code = pairCode.replace(/[^a-z0-9]/gi, "").toLowerCase();
    if (code.length !== 6) {
      Alert.alert("Pairing code", "Enter the 6 character code from Mavris desktop.");
      return;
    }
    setIsBusy(true);
    setError("");
    try {
      const nextSession = await pairWithBackend(baseUrl, code, Device.deviceName ?? "Android device");
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
        <Text style={styles.title}>Mavris Approval</Text>
        <Text style={styles.subtitle}>Pair on the same LAN to approve desktop tasks from Android.</Text>

        <View style={styles.form}>
          <Text style={styles.label}>Server IP</Text>
          <TextInput
            autoCapitalize="none"
            autoCorrect={false}
            inputMode="url"
            onChangeText={setBaseUrl}
            placeholder="http://192.168.1.20:8000"
            style={styles.input}
            value={baseUrl}
          />
          <Text style={styles.label}>Pairing Code</Text>
          <TextInput
            autoCapitalize="none"
            autoCorrect={false}
            maxLength={6}
            onChangeText={(value) => setPairCode(value.replace(/[^a-z0-9]/gi, "").toLowerCase())}
            placeholder="6 chars"
            style={[styles.input, styles.codeInput]}
            value={pairCode}
          />
          {error ? <Text style={styles.errorText}>{error}</Text> : null}
          <Pressable disabled={isBusy} onPress={handlePair} style={({ pressed }) => [styles.primaryButton, pressed && styles.pressed]}>
            {isBusy ? <ActivityIndicator color="#ffffff" /> : <Link2 size={18} color="#ffffff" />}
            <Text style={styles.primaryButtonText}>Pair Device</Text>
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Request failed";
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
