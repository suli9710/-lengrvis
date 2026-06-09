import { useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Modal,
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
import { CameraView, useCameraPermissions, type BarcodeScanningResult } from "expo-camera";
import * as Device from "expo-device";
import { Camera, Link2, QrCode, Smartphone, X } from "lucide-react-native";

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

type PairingFailureSource = "scan" | "input";

const MAX_PAIRING_PAYLOAD_LENGTH = 4096;
const MAX_BASE_URL_LENGTH = 2048;
const MAX_PAIRING_CODE_LENGTH = 6;
const MAX_PAIRING_CODE_RAW_INPUT_LENGTH = 16;
const MAX_DEVICE_NAME_LENGTH = 80;

interface ProtectedPairingPayloadInput {
  value: string;
  wasTruncated: boolean;
}

export function PairScreen({ onPaired }: { onPaired: (session: PairingSession) => void }) {
  const [baseUrl, setBaseUrl] = useState("");
  const [pairCode, setPairCode] = useState("");
  const [pairingPayload, setPairingPayload] = useState("");
  const [detectedPayload, setDetectedPayload] = useState<PairingPayload | null>(null);
  const [showManualEntry, setShowManualEntry] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [failure, setFailure] = useState<PairingFailureNotice | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [scanLocked, setScanLocked] = useState(false);
  const scanLockedRef = useRef(false);
  const pairRequestLockedRef = useRef(false);
  const [isCameraPermissionBusy, setIsCameraPermissionBusy] = useState(false);
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();

  const normalizedPairCode = normalizePairingCodeInput(pairCode);
  const activeDetectedPayload = detectedPayload;
  const securityHint = showManualEntry ? baseUrlSecurityHint(baseUrl, activeDetectedPayload?.security) : null;
  const manualBaseUrlFormatNotice = showManualEntry && baseUrl.trim() && !isPairingBaseUrlInputReady(baseUrl) ? invalidBaseUrlFormatNotice() : null;
  const manualBaseUrlNotice = manualBaseUrlFormatNotice ?? securityHint;
  const detectedPayloadSecurity = activeDetectedPayload ? classifyPairingPayloadSecurity(activeDetectedPayload) : null;
  const isDetectedPayloadBlocked = Boolean(detectedPayloadSecurity && !detectedPayloadSecurity.canPair);
  const hasSubmitInput = showManualEntry ? Boolean(isPairingBaseUrlInputReady(baseUrl) && normalizedPairCode.length === MAX_PAIRING_CODE_LENGTH) : Boolean(pairingPayload.trim());
  const canSubmit = !isBusy && hasSubmitInput && !isDetectedPayloadBlocked;
  const payloadAccessibilityValue = `${pairingPayload.length}/${MAX_PAIRING_PAYLOAD_LENGTH} 个字符`;
  const baseUrlAccessibilityValue = `${baseUrl.length}/${MAX_BASE_URL_LENGTH} 个字符`;
  const pairCodeAccessibilityValue = `${normalizedPairCode.length}/${MAX_PAIRING_CODE_LENGTH} 位`;
  const primaryButtonLabel = pairingButtonLabel({
    isBusy,
    hasSubmitInput,
    showManualEntry,
    blockedStatus: isDetectedPayloadBlocked ? detectedPayloadSecurity?.status : undefined,
  });

  const persistPairedSession = async (session: PairingSession) => {
    setIsBusy(true);
    try {
      await saveSession(session);
      setPairCode("");
      setPairingPayload("");
      setDetectedPayload(null);
      onPaired(session);
    } catch {
      setFailure(pairedSessionStorageFailureNotice());
    } finally {
      setIsBusy(false);
    }
  };

  const handlePayloadChange = (value: string) => {
    const protectedInput = protectPairingPayloadInput(value);
    const nextValue = protectedInput.value;
    setPairingPayload(nextValue);
    setFailure(null);
    if (protectedInput.wasTruncated) {
      setDetectedPayload(null);
      if (!showManualEntry) {
        setBaseUrl("");
        setPairCode("");
      }
      setFailure(pairingInputTooLongNotice("payload"));
      return;
    }
    if (!nextValue.trim()) {
      setDetectedPayload(null);
      if (!showManualEntry) {
        setBaseUrl("");
        setPairCode("");
      }
      return;
    }
    try {
      const payload = parsePairingPayload(nextValue);
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

  const updateScanLocked = (locked: boolean) => {
    scanLockedRef.current = locked;
    setScanLocked(locked);
  };

  const openScanner = async () => {
    if (isBusy || isCameraPermissionBusy) return;
    setFailure(null);
    updateScanLocked(false);
    if (cameraPermission?.granted) {
      setIsScanning(true);
      return;
    }
    if (cameraPermission && !cameraPermission.canAskAgain) {
      setFailure(cameraPermissionFailureNotice(false));
      return;
    }

    setIsCameraPermissionBusy(true);
    try {
      const nextPermission = await requestCameraPermission();
      if (nextPermission.granted) {
        setIsScanning(true);
        return;
      }
      setFailure(cameraPermissionFailureNotice(nextPermission.canAskAgain));
    } catch {
      setFailure(cameraUnavailableFailureNotice());
    } finally {
      setIsCameraPermissionBusy(false);
    }
  };

  const closeScanner = (nextFailure?: PairingFailureNotice) => {
    setIsScanning(false);
    updateScanLocked(false);
    if (nextFailure) {
      setFailure(nextFailure);
    }
  };

  const handleBarcodeScanned = (result: BarcodeScanningResult) => {
    if (scanLockedRef.current) return;
    updateScanLocked(true);
    const protectedInput = protectPairingPayloadInput(result.data);
    if (protectedInput.wasTruncated) {
      setShowManualEntry(false);
      setPairingPayload("");
      setDetectedPayload(null);
      setBaseUrl("");
      setPairCode("");
      closeScanner(pairingInputTooLongNotice("scan"));
      return;
    }
    try {
      const payload = parsePairingPayload(protectedInput.value);
      setPairingPayload(protectedInput.value);
      applyPayload(payload);
      setShowManualEntry(false);
      setFailure(null);
      closeScanner();
    } catch (currentError) {
      setPairingPayload("");
      setDetectedPayload(null);
      if (!showManualEntry) {
        setBaseUrl("");
        setPairCode("");
      }
      closeScanner(pairingFailureNotice(currentError, undefined, "scan"));
    }
  };

  const handleManualBaseUrlChange = (value: string) => {
    const protectedInput = protectBaseUrlInput(value);
    setBaseUrl(protectedInput.value);
    setDetectedPayload(null);
    setPairingPayload("");
    setFailure(protectedInput.notice);
  };

  const handleManualPairCodeChange = (value: string) => {
    setPairCode(normalizePairingCodeInput(value));
    setFailure(null);
  };

  const handleManualToggle = () => {
    setFailure(null);
    setShowManualEntry((current) => !current);
  };

  const handlePair = async () => {
    if (isBusy || pairRequestLockedRef.current) return;
    pairRequestLockedRef.current = true;
    setIsBusy(true);
    try {
      setFailure(null);

      let nextBaseUrl = baseUrl;
      let nextPairCode = pairCode;
      let nextPayload = activeDetectedPayload;
      if (!showManualEntry && pairingPayload.trim()) {
        try {
          const protectedInput = protectPairingPayloadInput(pairingPayload);
          if (protectedInput.wasTruncated) {
            setPairingPayload(protectedInput.value);
            setDetectedPayload(null);
            setBaseUrl("");
            setPairCode("");
            setFailure(pairingInputTooLongNotice("payload"));
            return;
          }
          const payload = parsePairingPayload(protectedInput.value);
          setPairingPayload(protectedInput.value);
          applyPayload(payload);
          nextBaseUrl = payload.baseUrl;
          nextPairCode = payload.code;
          nextPayload = payload;
        } catch (currentError) {
          setFailure(pairingFailureNotice(currentError));
          return;
        }
      }

      const nextPayloadSecurity = nextPayload ? classifyPairingPayloadSecurity(nextPayload) : null;
      if (nextPayloadSecurity && !nextPayloadSecurity.canPair) {
        setFailure(blockedPairingPayloadFailureNotice(nextPayloadSecurity.status));
        return;
      }

      const code = normalizePairingCodeInput(nextPairCode);
      if (code.length !== MAX_PAIRING_CODE_LENGTH) {
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
        baseUrlSecurity = describeBaseUrlSecurity(nextBaseUrl, nextPayload?.security);
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
          title: "需要安全连接",
          detail: "为了保护手机配对和远程操作，这个普通网络地址不能直接连接。",
          action: "请在电脑端开启安全连接后，重新生成配对信息。",
        });
        return;
      }

      try {
        const nextSession = await pairWithBackend(baseUrlSecurity.normalizedBaseUrl, code, safeDeviceName(Device.deviceName));
        if (requiresServerTrustConfirmation(nextSession)) {
          Alert.alert("确认这是你的电脑", serverTrustConfirmationMessage(nextSession), [
            { text: "取消", style: "cancel" },
            { text: "确认并保存", onPress: () => void persistPairedSession(nextSession) },
          ], { cancelable: true });
          return;
        }
        await persistPairedSession(nextSession);
      } catch (currentError) {
        setFailure(pairingFailureNotice(currentError, baseUrlSecurity));
      }
    } finally {
      pairRequestLockedRef.current = false;
      setIsBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.safeArea} testID="pair-screen">
      <StatusBar barStyle="dark-content" backgroundColor="#f7f9fb" />
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={styles.screen}>
        <ScrollView
          contentContainerStyle={styles.content}
          keyboardDismissMode={Platform.OS === "ios" ? "interactive" : "on-drag"}
          keyboardShouldPersistTaps="handled"
        >
          <View accessibilityElementsHidden importantForAccessibility="no" style={styles.pairIcon}>
            <Smartphone size={34} color="#17323a" />
          </View>
          <Text style={styles.title}>连接 Lengrvis</Text>
          <Text style={styles.subtitle}>优先使用电脑端生成的配对信息，手机会自动识别电脑和配对码。</Text>

          <View style={styles.form}>
            <View style={styles.payloadPanel}>
              <View style={styles.payloadHeader}>
                <View accessibilityElementsHidden importantForAccessibility="no" style={styles.payloadIcon}>
                  <QrCode size={22} color="#0e5f76" />
                </View>
                <View style={styles.payloadCopy}>
                  <Text style={styles.sectionTitle}>扫码或粘贴二维码内容</Text>
                  <Text style={styles.sectionDetail}>扫描电脑端二维码，或复制二维码内容后粘贴，手机会自动识别地址和配对码。</Text>
                </View>
              </View>
              <View style={styles.scanActionRow}>
                <Pressable
                  accessibilityHint="使用手机相机扫描电脑端 Lengrvis 配对二维码"
                  accessibilityLabel="打开相机扫码"
                  accessibilityRole="button"
                  accessibilityState={{ busy: isCameraPermissionBusy, disabled: isCameraPermissionBusy }}
                  disabled={isCameraPermissionBusy}
                  hitSlop={10}
                  onPress={() => void openScanner()}
                  style={({ pressed }) => [styles.scanButton, pressed && styles.pressed, isCameraPermissionBusy && styles.scanButtonDisabled]}
                  testID="pair-open-scanner-button"
                >
                  {isCameraPermissionBusy ? <ActivityIndicator size="small" color="#0e5f76" /> : <Camera size={16} color="#0e5f76" />}
                  <Text style={styles.scanButtonText}>{isCameraPermissionBusy ? "请求相机权限" : "打开相机扫码"}</Text>
                </Pressable>
                <Text style={styles.scanActionText}>扫码失败时也可以直接粘贴。</Text>
              </View>
              <TextInput
                accessibilityHint="粘贴电脑端显示的二维码文本，手机会自动识别地址和配对码"
                accessibilityLabel="二维码内容或配对信息"
                accessibilityValue={{ text: payloadAccessibilityValue }}
                autoCapitalize="none"
                autoComplete="off"
                autoCorrect={false}
                importantForAutofill="no"
                maxLength={MAX_PAIRING_PAYLOAD_LENGTH}
                multiline
                onChangeText={handlePayloadChange}
                placeholder="粘贴电脑端二维码内容或配对信息"
                spellCheck={false}
                style={[styles.input, styles.payloadInput]}
                testID="pair-payload-input"
                textAlignVertical="top"
                textContentType="none"
                value={pairingPayload}
              />
              {detectedPayload && detectedPayloadSecurity ? <PairingPayloadStatus payload={detectedPayload} state={detectedPayloadSecurity} /> : null}
            </View>

            <Pressable
              accessibilityHint="显示或隐藏备用的电脑地址和配对码输入"
              accessibilityLabel="手动输入配对信息"
              accessibilityRole="button"
              accessibilityState={{ expanded: showManualEntry }}
              hitSlop={10}
              onPress={handleManualToggle}
              style={styles.manualToggle}
              testID="pair-manual-toggle"
            >
              <Text style={styles.manualToggleText}>{showManualEntry ? "收起手动输入" : "无法粘贴？手动输入"}</Text>
            </Pressable>

            {showManualEntry ? (
              <View style={styles.manualPanel}>
                <Text style={styles.label}>电脑地址</Text>
                <TextInput
                  accessibilityHint="输入电脑端 Lengrvis 显示的地址"
                  accessibilityLabel="电脑地址"
                  accessibilityValue={{ text: baseUrlAccessibilityValue }}
                  autoCapitalize="none"
                  autoComplete="off"
                  autoCorrect={false}
                  importantForAutofill="no"
                  inputMode="url"
                  maxLength={MAX_BASE_URL_LENGTH}
                  onChangeText={handleManualBaseUrlChange}
                  placeholder="电脑端显示的地址"
                  spellCheck={false}
                  style={styles.input}
                  testID="pair-base-url-input"
                  textContentType="none"
                  value={baseUrl}
                />
                {manualBaseUrlNotice ? (
                  <View
                    accessibilityLabel={`${manualBaseUrlNotice.title}。${manualBaseUrlNotice.detail}`}
                    accessibilityLiveRegion="polite"
                    accessibilityRole={manualBaseUrlNotice.tone === "danger" ? "alert" : undefined}
                    style={[
                      styles.securityNotice,
                      manualBaseUrlNotice.tone === "safe" && styles.securityNoticeSafe,
                      manualBaseUrlNotice.tone === "danger" && styles.securityNoticeDanger,
                    ]}
                  >
                    <Text style={styles.securityNoticeTitle}>{manualBaseUrlNotice.title}</Text>
                    <Text style={styles.securityNoticeText}>{manualBaseUrlNotice.detail}</Text>
                  </View>
                ) : null}
                <Text style={styles.label}>配对码</Text>
                <TextInput
                  accessibilityHint="输入电脑端显示的 6 位字母或数字"
                  accessibilityLabel="配对码"
                  accessibilityValue={{ text: pairCodeAccessibilityValue }}
                  autoCapitalize="none"
                  autoComplete="off"
                  autoCorrect={false}
                  importantForAutofill="no"
                  inputMode="text"
                  maxLength={MAX_PAIRING_CODE_RAW_INPUT_LENGTH}
                  onChangeText={handleManualPairCodeChange}
                  placeholder="6 位字母或数字"
                  spellCheck={false}
                  style={[styles.input, styles.codeInput]}
                  testID="pair-code-input"
                  textContentType="none"
                  value={pairCode}
                />
              </View>
            ) : null}

            {failure ? <PairingFailure notice={failure} /> : null}

            <Pressable
              accessibilityHint="使用当前配对信息连接这台手机"
              accessibilityLabel={primaryButtonLabel}
              accessibilityRole="button"
              accessibilityState={{ disabled: !canSubmit, busy: isBusy }}
              disabled={!canSubmit}
              hitSlop={10}
              onPress={() => void handlePair()}
              style={({ pressed }) => [styles.primaryButton, pressed && styles.pressed, !canSubmit && styles.disabledButton]}
              testID="pair-submit-button"
            >
              {isBusy ? <ActivityIndicator color="#ffffff" /> : <Link2 size={18} color="#ffffff" />}
              <Text style={styles.primaryButtonText}>{primaryButtonLabel}</Text>
            </Pressable>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
      <PairingScanner visible={isScanning} scanLocked={scanLocked} onClose={closeScanner} onScanned={handleBarcodeScanned} />
    </SafeAreaView>
  );
}

function PairingScanner({
  visible,
  scanLocked,
  onClose,
  onScanned,
}: {
  visible: boolean;
  scanLocked: boolean;
  onClose: (notice?: PairingFailureNotice) => void;
  onScanned: (result: BarcodeScanningResult) => void;
}) {
  return (
    <Modal animationType="slide" onRequestClose={() => onClose()} presentationStyle="fullScreen" testID="pairing-scanner-modal" visible={visible}>
      <SafeAreaView accessibilityLabel="扫码配对" accessibilityViewIsModal style={styles.scannerScreen} testID="pairing-scanner-screen">
        <StatusBar barStyle="light-content" backgroundColor="#101820" />
        <View style={styles.scannerHeader}>
          <View accessibilityElementsHidden importantForAccessibility="no">
            <QrCode size={22} color="#ffffff" />
          </View>
          <Text style={styles.scannerTitle}>扫描配对二维码</Text>
          <Pressable
            accessibilityHint="返回配对输入页面"
            accessibilityLabel="关闭扫码"
            accessibilityRole="button"
            hitSlop={10}
            onPress={() => onClose()}
            style={({ pressed }) => [styles.scannerCloseButton, pressed && styles.pressed]}
            testID="pairing-scanner-close-button"
          >
            <X size={22} color="#ffffff" />
          </Pressable>
        </View>
        <CameraView
          accessibilityHint="只识别二维码，识别后会自动关闭相机"
          accessibilityLabel="二维码扫码取景框"
          accessibilityRole="image"
          barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
          facing="back"
          onMountError={() => onClose(cameraUnavailableFailureNotice())}
          onBarcodeScanned={scanLocked ? undefined : onScanned}
          style={styles.cameraPreview}
          testID="pairing-scanner-camera"
        />
        <View style={styles.scannerHint} testID="pairing-scanner-hint">
          {scanLocked ? <ActivityIndicator color="#ffffff" /> : null}
          <Text style={styles.scannerHintTitle}>{scanLocked ? "正在识别二维码" : "将电脑端二维码放入取景框"}</Text>
          <Text style={styles.scannerHintText}>{scanLocked ? "如果内容不是 Lengrvis 配对信息，手机会回到上一页并给出下一步。" : "识别后会自动填入电脑地址和 6 位配对码。"}</Text>
        </View>
      </SafeAreaView>
    </Modal>
  );
}

function PairingFailure({ notice }: { notice: PairingFailureNotice }) {
  return (
    <View
      accessibilityLabel={`${notice.title}。${notice.detail}。${notice.action}`}
      accessibilityLiveRegion="assertive"
      accessibilityRole="alert"
      style={styles.failureNotice}
      testID="pair-failure-notice"
    >
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
    <View
      accessibilityLabel={`${notice.title}。${notice.detail}`}
      accessibilityLiveRegion="polite"
      accessibilityRole={isDanger ? "alert" : undefined}
      style={[styles.detectedNotice, isWarning && styles.detectedNoticeWarning, isDanger && styles.detectedNoticeDanger]}
      testID="pair-payload-status"
    >
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

function baseUrlSecurityHint(value: string, metadata?: PairingPayload["security"]): SecurityNotice | null {
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

function requiresServerTrustConfirmation(session: PairingSession): boolean {
  return Boolean(session.baseUrlSecurity.serverTls?.requiresTrust);
}

function serverTrustConfirmationMessage(session: PairingSession): string {
  const tls = session.baseUrlSecurity.serverTls;
  const fingerprint = formatTlsFingerprint(tls?.fingerprintSha256);
  return [
    "这台电脑使用了本地安全设置，手机需要你确认一次。",
    fingerprint ? `电脑指纹：${fingerprint}` : "如果你不确定，请先取消，并在电脑端重新生成配对信息。",
    "确认它和电脑端显示的一致后再保存；不确定时请取消。",
  ].join("\n\n");
}

function payloadValidityText(payload: PairingPayload): string {
  if (!payload.expiresAt) return "配对信息已识别。";
  const expiry = new Date(payload.expiresAt);
  if (Number.isNaN(expiry.getTime())) return "配对信息已识别。";
  return `${expiry.toLocaleTimeString()} 前有效。`;
}

function cameraPermissionFailureNotice(canAskAgain: boolean | undefined): PairingFailureNotice {
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

function cameraUnavailableFailureNotice(): PairingFailureNotice {
  return {
    title: "无法打开相机",
    detail: "手机暂时没有可用的相机取景框。",
    action: "请检查系统相机权限后重试；也可以直接粘贴电脑端二维码内容。",
  };
}

function pairingButtonLabel({
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
  if (!hasSubmitInput) return showManualEntry ? "输入电脑地址和 6 位配对码" : "先扫码或粘贴配对信息";
  return "连接手机";
}

function blockedPairingButtonLabel(status: PairingPayloadSecurityState["status"]): string {
  if (status === "requires_https_wss") return "等待安全配对信息";
  if (status === "loopback") return "等待电脑地址";
  if (status === "expired") return "重新生成配对码";
  if (status === "invalid_address") return "等待完整配对信息";
  return "检查配对信息";
}

function blockedPairingPayloadFailureNotice(status: PairingPayloadSecurityState["status"]): PairingFailureNotice {
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

function protectPairingPayloadInput(value: string): ProtectedPairingPayloadInput {
  const withoutUnsafeCharacters = value.replace(/[\u0000-\u001f\u007f]+/g, " ");
  return {
    value: withoutUnsafeCharacters.slice(0, MAX_PAIRING_PAYLOAD_LENGTH),
    wasTruncated: withoutUnsafeCharacters.length > MAX_PAIRING_PAYLOAD_LENGTH,
  };
}

function protectBaseUrlInput(value: string): { value: string; notice: PairingFailureNotice | null } {
  const withoutUnsafeCharacters = value.replace(/[\u0000-\u001f\u007f\s]+/g, "");
  const nextValue = withoutUnsafeCharacters.slice(0, MAX_BASE_URL_LENGTH);
  if (nextValue === value) return { value: nextValue, notice: null };
  return {
    value: nextValue,
    notice: withoutUnsafeCharacters.length > MAX_BASE_URL_LENGTH ? pairingInputTooLongNotice("baseUrl") : baseUrlInputCleanedNotice(),
  };
}

function normalizePairingCodeInput(value: string): string {
  return value.replace(/[^a-z0-9]/gi, "").toLowerCase().slice(0, MAX_PAIRING_CODE_LENGTH);
}

function isPairingBaseUrlInputReady(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed) return false;
  try {
    const withProtocol = /^[a-z][a-z\d+\-.]*:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`;
    const parsed = new URL(withProtocol);
    return Boolean(parsed.hostname && (parsed.protocol === "http:" || parsed.protocol === "https:"));
  } catch {
    return false;
  }
}

function safeDeviceName(value: string | null): string {
  const normalized = value?.replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim().slice(0, MAX_DEVICE_NAME_LENGTH);
  return normalized || "安卓设备";
}

function invalidBaseUrlFormatNotice(): SecurityNotice {
  return {
    tone: "danger",
    title: "电脑地址格式错误",
    detail: "请输入电脑端显示的地址；地址必须是 http 或 https，不能包含空格或换行。",
  };
}

function baseUrlInputCleanedNotice(): PairingFailureNotice {
  return {
    title: "已整理电脑地址",
    detail: "电脑地址不能包含空格或换行，手机已自动移除这些字符。",
    action: "请确认地址仍和电脑端显示的一致，然后继续输入配对码。",
  };
}

function pairingInputTooLongNotice(kind: "payload" | "baseUrl" | "scan"): PairingFailureNotice {
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

function pairedSessionStorageFailureNotice(): PairingFailureNotice {
  return {
    title: "无法保存配对",
    detail: "手机已收到配对结果，但没有把会话安全保存下来。",
    action: "请确认系统安全存储可用，然后重新配对。",
  };
}

function pairingFailureNotice(error: unknown, security?: BaseUrlSecurity, source: PairingFailureSource = "input"): PairingFailureNotice {
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
        detail: "手机扫到的内容里没有同时包含电脑地址和 6 位配对码。",
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
      detail: "手机没有从这段内容里识别到电脑和 6 位配对码。",
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
      detail: "配对信息里的 6 位配对码只能短时间使用，过期后会被电脑端拒绝。",
      action: "请回到电脑端重新生成配对信息，不要复用旧截图或旧粘贴内容。",
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
    justifyContent: "flex-start",
    padding: 24,
    paddingTop: Platform.select({ android: 28, default: 34 }),
    paddingBottom: Platform.select({ android: 96, default: 34 }),
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
    minHeight: 48,
    maxWidth: "100%",
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#9ec6cf",
    backgroundColor: "#edf8fb",
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 7,
    paddingHorizontal: 12,
    flexShrink: 1,
  },
  scanButtonDisabled: {
    opacity: 0.68,
  },
  scanButtonText: {
    flexShrink: 1,
    color: "#0e5f76",
    fontSize: 14,
    fontWeight: "900",
    textAlign: "center",
  },
  scanActionText: {
    flexShrink: 1,
    color: "#60707b",
    fontSize: 12,
    lineHeight: 18,
  },
  scannerScreen: {
    flex: 1,
    backgroundColor: "#101820",
  },
  scannerHeader: {
    minHeight: 58,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingHorizontal: 18,
    paddingVertical: 8,
  },
  scannerTitle: {
    flex: 1,
    color: "#ffffff",
    fontSize: 18,
    fontWeight: "900",
  },
  scannerCloseButton: {
    width: 48,
    height: 48,
    borderRadius: 8,
    backgroundColor: "rgba(255,255,255,0.14)",
    alignItems: "center",
    justifyContent: "center",
  },
  cameraPreview: {
    flex: 1,
  },
  scannerHint: {
    paddingHorizontal: 18,
    paddingTop: 14,
    paddingBottom: 20,
    gap: 4,
  },
  scannerHintTitle: {
    color: "#ffffff",
    fontSize: 15,
    fontWeight: "900",
  },
  scannerHintText: {
    color: "#cbd9df",
    fontSize: 13,
    lineHeight: 19,
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
    minHeight: 48,
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
    paddingHorizontal: 16,
  },
  disabledButton: {
    opacity: 0.68,
  },
  primaryButtonText: {
    flexShrink: 1,
    color: "#ffffff",
    fontSize: 16,
    fontWeight: "800",
    textAlign: "center",
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
