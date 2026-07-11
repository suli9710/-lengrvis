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
  Text,
  TextInput,
  View,
} from "react-native";
import { CameraView, useCameraPermissions, type BarcodeScanningResult } from "expo-camera";
import * as Device from "expo-device";
import { Camera, Link2, QrCode, Smartphone, X } from "lucide-react-native";

import { describeBaseUrlSecurity, pairWithBackend, type BaseUrlSecurity, type PairingSession } from "../api/client";
import { stageNativeTlsTrust } from "../api/client/nativeTlsTrust";
import {
  PAIRING_CODE_LENGTH,
  classifyPairingPayloadSecurity,
  parsePairingPayload,
  type PairingPayload,
  type PairingPayloadSecurityState,
} from "../api/pairingPayload";
import { saveSession } from "../store/auth";
import { MAX_BASE_URL_LENGTH, MAX_PAIRING_CODE_LENGTH, MAX_PAIRING_CODE_RAW_INPUT_LENGTH, MAX_PAIRING_PAYLOAD_LENGTH } from "./pairScreenConstants";
import { isPairingBaseUrlInputReady, normalizePairingCodeInput, protectBaseUrlInput, protectPairingPayloadInput, safeDeviceName } from "./pairScreenInput";
import {
  baseUrlSecurityHint,
  blockedPairingPayloadFailureNotice,
  cameraPermissionFailureNotice,
  cameraUnavailableFailureNotice,
  invalidBaseUrlFormatNotice,
  pairedSessionStorageFailureNotice,
  pairingButtonLabel,
  pairingFailureNotice,
  pairingInputTooLongNotice,
  pairingPayloadNotice,
} from "./pairScreenNotices";
import { styles } from "./pairScreenStyles";
import { requiresServerTrustConfirmation, serverTrustConfirmationMessage } from "./pairScreenTrust";
import type { PairingFailureNotice } from "./pairScreenTypes";

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
          detail: `手机没有识别到电脑端生成的 ${PAIRING_CODE_LENGTH} 位配对码。`,
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
        if (requiresServerTrustConfirmation(baseUrlSecurity)) {
          const confirmed = await confirmServerTrust(baseUrlSecurity);
          if (!confirmed) {
            return;
          }
          await stageNativeTlsTrust(baseUrlSecurity);
        }
        const nextSession = await pairWithBackend(
          baseUrlSecurity.normalizedBaseUrl,
          code,
          safeDeviceName(Device.deviceName),
          nextPayload?.security,
          nextPayload?.claimSecret,
        );
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
                  accessibilityHint={`输入电脑端显示的 ${PAIRING_CODE_LENGTH} 位字母或数字`}
                  accessibilityLabel="配对码"
                  accessibilityValue={{ text: pairCodeAccessibilityValue }}
                  autoCapitalize="none"
                  autoComplete="off"
                  autoCorrect={false}
                  importantForAutofill="no"
                  inputMode="text"
                  maxLength={MAX_PAIRING_CODE_RAW_INPUT_LENGTH}
                  onChangeText={handleManualPairCodeChange}
                  placeholder={`${PAIRING_CODE_LENGTH} 位字母或数字`}
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
          <Text style={styles.scannerHintText}>{scanLocked ? "如果内容不是 Lengrvis 配对信息，手机会回到上一页并给出下一步。" : `识别后会自动填入电脑地址和 ${PAIRING_CODE_LENGTH} 位配对码。`}</Text>
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

function confirmServerTrust(security: BaseUrlSecurity): Promise<boolean> {
  return new Promise((resolve) => {
    Alert.alert(
      "确认这是你的电脑",
      serverTrustConfirmationMessage(security),
      [
        { text: "取消", style: "cancel", onPress: () => resolve(false) },
        { text: "确认并连接", onPress: () => resolve(true) },
      ],
      { cancelable: true, onDismiss: () => resolve(false) },
    );
  });
}
