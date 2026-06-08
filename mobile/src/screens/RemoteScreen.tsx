import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  AppState,
  type AppStateStatus,
  Alert,
  Image,
  Pressable,
  SafeAreaView,
  StatusBar,
  StyleSheet,
  Text,
  View,
  type GestureResponderEvent,
  type LayoutChangeEvent,
} from "react-native";
import { ArrowLeft, Monitor, MousePointer2, Pause, Play, RefreshCcw, ShieldCheck, TriangleAlert, Wifi, WifiOff, XCircle } from "lucide-react-native";

import {
  claimRemoteInputGrantToken,
  formatTlsFingerprint,
  remoteInputWebSocketConnectionInfo,
  remoteScreenWebSocketConnectionInfo,
  revokeRemoteInputGrant,
  type PairingSession,
  type RemoteInputGrant,
  type RemoteScreenEvent,
  type WebSocketConnectionInfo,
} from "../api/client";
import { shortDate } from "../format";
import { isRemoteInputGrantUsable, mapViewerPointToRemote, remoteInputGrantRemainingText } from "../remoteInputGrant";

type ConnectionState = "offline" | "connecting" | "online" | "paused";
type InputConnectionState = "disabled" | "ready" | "connecting" | "online" | "offline";
const FRAME_ACK_FALLBACK_MS = 900;

interface ScreenFrame {
  sequence: number;
  image: string;
  timestamp: string;
  width: number;
  height: number;
  originalWidth: number;
  originalHeight: number;
}

interface TransportNotice {
  tone: "secure" | "warning" | "danger";
  title: string;
  detail: string;
  warning?: string;
}

export function RemoteScreen({
  grant,
  session,
  onBack,
  onRemoteInputGrantRevoked,
}: {
  grant: RemoteInputGrant | null;
  session: PairingSession;
  onBack: () => void;
  onRemoteInputGrantRevoked: (grant: RemoteInputGrant) => void;
}) {
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [inputConnection, setInputConnection] = useState<InputConnectionState>(isRemoteInputGrantUsable(grant) ? "ready" : "disabled");
  const [frame, setFrame] = useState<ScreenFrame | null>(null);
  const [streamMeta, setStreamMeta] = useState({ fps: 0, quality: 0 });
  const [error, setError] = useState("");
  const [inputError, setInputError] = useState("");
  const [nowMs, setNowMs] = useState(Date.now());
  const [isRevokingInput, setIsRevokingInput] = useState(false);
  const [locallyRevokedGrantId, setLocallyRevokedGrantId] = useState("");
  const [viewerSize, setViewerSize] = useState({ width: 0, height: 0 });
  const socketRef = useRef<WebSocket | null>(null);
  const inputSocketRef = useRef<WebSocket | null>(null);
  const inputConnectionGenerationRef = useRef(0);
  const pausedByUserRef = useRef(false);
  const pendingFrameRef = useRef<ScreenFrame | null>(null);
  const frameRafRef = useRef<number | null>(null);
  const frameAckFallbackRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentFrameSequenceRef = useRef(0);
  const lastAcknowledgedSequenceRef = useRef(0);
  const lastRenderedAtRef = useRef(0);
  const degradedStreamRef = useRef(false);
  const effectiveGrant = grant?.id === locallyRevokedGrantId ? null : grant;
  const grantUsable = isRemoteInputGrantUsable(effectiveGrant, nowMs);
  const grantRemainingText = remoteInputGrantRemainingText(effectiveGrant, nowMs);
  const remoteModeText = grantUsable ? (inputConnection === "online" ? "已授权输入" : "可输入，待连接") : "只读观看";
  const screenConnectionInfo = remoteScreenWebSocketConnectionInfo(session);
  const transportNotice = remoteTransportNotice(screenConnectionInfo);
  const transportWarning = transportNotice.warning ?? "";

  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const clearFrameAckFallback = useCallback(() => {
    if (frameAckFallbackRef.current !== null) {
      clearTimeout(frameAckFallbackRef.current);
      frameAckFallbackRef.current = null;
    }
  }, []);

  const closeSocket = useCallback(() => {
    if (frameRafRef.current !== null) {
      cancelAnimationFrame(frameRafRef.current);
      frameRafRef.current = null;
    }
    clearFrameAckFallback();
    pendingFrameRef.current = null;
    currentFrameSequenceRef.current = 0;
    lastAcknowledgedSequenceRef.current = 0;
    degradedStreamRef.current = false;
    lastRenderedAtRef.current = 0;
    socketRef.current?.close();
    socketRef.current = null;
  }, [clearFrameAckFallback]);

  const closeInputSocket = useCallback(() => {
    inputConnectionGenerationRef.current += 1;
    inputSocketRef.current?.close();
    inputSocketRef.current = null;
  }, []);

  const resetInputConnection = useCallback(() => {
    setInputConnection(isRemoteInputGrantUsable(effectiveGrant) ? "ready" : "disabled");
  }, [effectiveGrant]);

  const sendStreamConfig = useCallback((fps: number, quality: number) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== 1) {
      return;
    }
    socket.send(JSON.stringify({ fps, quality }));
    setStreamMeta({ fps, quality });
  }, []);

  const acknowledgeFrame = useCallback((sequence: number) => {
    const socket = socketRef.current;
    if (
      !Number.isFinite(sequence) ||
      sequence <= 0 ||
      sequence !== currentFrameSequenceRef.current ||
      sequence <= lastAcknowledgedSequenceRef.current ||
      !socket ||
      socket.readyState !== 1
    ) {
      return;
    }
    lastAcknowledgedSequenceRef.current = sequence;
    clearFrameAckFallback();
    socket.send(JSON.stringify({ type: "frame_ack", sequence }));
  }, [clearFrameAckFallback]);

  const scheduleFrameRender = useCallback(() => {
    if (frameRafRef.current !== null) {
      return;
    }
    frameRafRef.current = requestAnimationFrame(() => {
      frameRafRef.current = null;
      const nextFrame = pendingFrameRef.current;
      pendingFrameRef.current = null;
      if (!nextFrame) {
        return;
      }

      const now = Date.now();
      const elapsedMs = lastRenderedAtRef.current ? now - lastRenderedAtRef.current : 0;
      lastRenderedAtRef.current = now;
      currentFrameSequenceRef.current = nextFrame.sequence;
      setFrame(nextFrame);

      clearFrameAckFallback();
      frameAckFallbackRef.current = setTimeout(() => {
        if (lastAcknowledgedSequenceRef.current >= nextFrame.sequence) {
          return;
        }
        if (!degradedStreamRef.current) {
          degradedStreamRef.current = true;
          sendStreamConfig(1, 42);
        }
        acknowledgeFrame(nextFrame.sequence);
      }, FRAME_ACK_FALLBACK_MS);

      if (elapsedMs > 900 && !degradedStreamRef.current) {
        degradedStreamRef.current = true;
        sendStreamConfig(1, 42);
      }
    });
  }, [acknowledgeFrame, clearFrameAckFallback, sendStreamConfig]);

  const connect = useCallback(() => {
    closeSocket();
    setConnection("connecting");
    setError("");

    const connectionInfo = remoteScreenWebSocketConnectionInfo(session);
    const socket = new WebSocket(connectionInfo.url, connectionInfo.protocols);
    socketRef.current = socket;

    socket.onopen = () => {
      if (socketRef.current !== socket) return;
      setConnection("online");
      sendStreamConfig(2, 55);
    };

    socket.onmessage = (event) => {
      if (socketRef.current !== socket) return;
      try {
        const payload = JSON.parse(String(event.data)) as RemoteScreenEvent;
        if (payload.type === "connected") {
          setStreamMeta({ fps: payload.fps, quality: payload.quality });
          return;
        }
        if (payload.type === "frame") {
          pendingFrameRef.current = {
            sequence: payload.sequence,
            image: payload.image,
            timestamp: payload.timestamp,
            width: payload.width,
            height: payload.height,
            originalWidth: payload.original_width,
            originalHeight: payload.original_height,
          };
          scheduleFrameRender();
          return;
        }
        if (payload.type === "error") {
          setError(readableStreamError(payload.message));
        }
      } catch {
        setError("屏幕画面数据无法读取。请点击重试重新连接。");
      }
    };

    socket.onerror = () => {
      if (socketRef.current !== socket) return;
      setError("暂时无法显示屏幕。请确认 Lengrvis 已打开，然后点重试。");
    };

    socket.onclose = (event) => {
      if (socketRef.current !== socket) return;
      if (event.code === 1008) {
        setError("无法查看电脑屏幕。请在桌面端设置中确认已开启手机屏幕查看；如果仍失败，请在手机和电脑端重新配对。");
        setConnection("offline");
        return;
      }
      setConnection((current) => (current === "paused" ? current : "offline"));
    };
  }, [closeSocket, scheduleFrameRender, sendStreamConfig, session]);

  const connectInput = useCallback(async () => {
    closeInputSocket();
    if (!isRemoteInputGrantUsable(effectiveGrant)) {
      setInputConnection("disabled");
      setInputError("");
      return;
    }
    const connectionGeneration = inputConnectionGenerationRef.current;
    setInputConnection("connecting");
    setInputError("");
    try {
      const grantToken = await claimRemoteInputGrantToken(session, effectiveGrant.id);
      if (connectionGeneration !== inputConnectionGenerationRef.current || !isRemoteInputGrantUsable(effectiveGrant)) {
        return;
      }
      const connectionInfo = remoteInputWebSocketConnectionInfo(session, grantToken.token);
      const socket = new WebSocket(connectionInfo.url, connectionInfo.protocols);
      if (connectionGeneration !== inputConnectionGenerationRef.current) {
        socket.close();
        return;
      }
      inputSocketRef.current = socket;
      socket.onopen = () => {
        if (inputSocketRef.current !== socket) return;
        setInputConnection("online");
      };
      socket.onmessage = (event) => {
        if (inputSocketRef.current !== socket) return;
        try {
          const payload = JSON.parse(String(event.data)) as { type?: string; message?: string };
          if (payload.type === "approval_required") {
            setInputError("点击已发送到电脑端审批。");
            return;
          }
          if (payload.type === "error" || payload.type === "denied") {
            setInputError(payload.message || "电脑端拒绝了这次远程输入。");
          }
        } catch {
          setInputError("电脑端返回了无法读取的远程输入结果。");
        }
      };
      socket.onerror = () => {
        if (inputSocketRef.current !== socket) return;
        setInputConnection("offline");
        setInputError("远程点击连接不可用。请在电脑端重新授权。");
      };
      socket.onclose = () => {
        if (inputSocketRef.current !== socket) return;
        setInputConnection("offline");
      };
    } catch (currentError) {
      if (connectionGeneration !== inputConnectionGenerationRef.current) {
        return;
      }
      setInputConnection("offline");
      setInputError(inputErrorMessage(currentError));
    }
  }, [closeInputSocket, effectiveGrant, session]);

  useEffect(() => {
    resetInputConnection();
    setInputError("");
    closeInputSocket();
  }, [closeInputSocket, effectiveGrant, resetInputConnection]);

  useEffect(() => {
    connect();
    if (grantUsable) void connectInput();
    return () => {
      closeSocket();
      closeInputSocket();
    };
  }, [closeInputSocket, closeSocket, connect, connectInput, grantUsable]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state: AppStateStatus) => {
      if (state === "active") {
        if (!pausedByUserRef.current) {
          setConnection("connecting");
          connect();
          if (grantUsable) void connectInput();
        }
        return;
      }
      setConnection("paused");
      resetInputConnection();
      closeSocket();
      closeInputSocket();
    });
    return () => subscription.remove();
  }, [closeInputSocket, closeSocket, connect, connectInput, grantUsable, resetInputConnection]);

  const handleToggleStream = () => {
    if (connection === "online" || connection === "connecting") {
      pausedByUserRef.current = true;
      setConnection("paused");
      resetInputConnection();
      closeSocket();
      closeInputSocket();
      return;
    }
    pausedByUserRef.current = false;
    connect();
    if (grantUsable) void connectInput();
  };

  const handleViewerLayout = (event: LayoutChangeEvent) => {
    setViewerSize({
      width: event.nativeEvent.layout.width,
      height: event.nativeEvent.layout.height,
    });
  };

  const handleRemotePress = (event: GestureResponderEvent) => {
    const socket = inputSocketRef.current;
    if (!frame || !viewerSize.width || !viewerSize.height || !socket || socket.readyState !== 1) {
      if (grantUsable && inputConnection !== "online") void connectInput();
      return;
    }
    const point = mapViewerPointToRemote(event.nativeEvent.locationX, event.nativeEvent.locationY, viewerSize, frame);
    if (!point) return;
    socket.send(JSON.stringify({ type: "click", x: point.x, y: point.y }));
    setInputError("点击已发送，等待电脑端审批。");
  };

  const handleEndInputControl = async () => {
    if (!grantUsable || !grant) return;
    setIsRevokingInput(true);
    setInputError("");
    try {
      const revokedGrant = await revokeRemoteInputGrant(session, grant.id);
      setLocallyRevokedGrantId(revokedGrant.id);
      onRemoteInputGrantRevoked(revokedGrant);
      resetInputConnection();
      closeInputSocket();
      setInputConnection("disabled");
      setInputError("已结束远程输入授权。");
    } catch (currentError) {
      const message = currentError instanceof Error && currentError.message ? currentError.message : "结束接管失败，请稍后重试。";
      Alert.alert("结束接管失败", message);
    } finally {
      setIsRevokingInput(false);
    }
  };

  const online = connection === "online";
  const showLoading = connection === "connecting" && !frame;
  const aspectRatio = frame && frame.width > 0 && frame.height > 0 ? frame.width / frame.height : 16 / 9;
  const canRetry = connection === "offline" || !!error;

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar barStyle="light-content" backgroundColor="#17222b" />
      <View style={styles.header}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="返回审批列表"
          onPress={onBack}
          style={({ pressed }) => [styles.iconButton, pressed && styles.pressed]}
        >
          <ArrowLeft size={20} color="#f7faf8" />
        </Pressable>
        <View style={styles.headerText}>
          <Text style={styles.kicker}>仅查看</Text>
          <Text style={styles.headerTitle}>电脑屏幕</Text>
        </View>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={online || connection === "connecting" ? "暂停屏幕查看" : "继续屏幕查看"}
          accessibilityState={{ busy: connection === "connecting", selected: online }}
          onPress={handleToggleStream}
          style={({ pressed }) => [styles.iconButton, pressed && styles.pressed]}
        >
          {online || connection === "connecting" ? <Pause size={20} color="#f7faf8" /> : <Play size={20} color="#f7faf8" />}
        </Pressable>
      </View>

      <View style={styles.statusRow}>
        {online ? <Wifi size={16} color="#75d39a" /> : <WifiOff size={16} color="#ffcf72" />}
        <Text style={styles.statusText}>{statusText(connection)}</Text>
        {streamMeta.fps ? <Text style={styles.statusMeta}>低带宽模式</Text> : null}
      </View>

      <View
        style={[
          styles.transportStatusRow,
          transportNotice.tone === "secure" && styles.transportStatusSecure,
          transportNotice.tone === "danger" && styles.transportStatusDanger,
        ]}
      >
        {transportNotice.tone === "secure" ? <ShieldCheck size={15} color="#75d39a" /> : <TriangleAlert size={15} color="#ffcf72" />}
        <View style={styles.transportStatusTextWrap}>
          <Text style={styles.transportStatusLabel}>{transportNotice.title}</Text>
          <Text style={styles.transportStatusMeta}>{transportNotice.detail}</Text>
        </View>
      </View>

      <View style={styles.inputStatusRow}>
        <MousePointer2 size={15} color={inputConnection === "online" ? "#75d39a" : "#ffcf72"} />
        <Text style={styles.inputStatusText}>{inputStatusText(inputConnection)}</Text>
        {grantUsable && inputConnection !== "online" ? (
          <Pressable onPress={() => void connectInput()} style={({ pressed }) => [styles.inputReconnect, pressed && styles.pressed]}>
            <Text style={styles.inputReconnectText}>连接</Text>
          </Pressable>
        ) : null}
      </View>

      <View style={styles.grantStatusRow}>
        <View style={styles.grantStatusTextWrap}>
          <Text style={styles.grantStatusLabel}>{remoteModeText}</Text>
          <Text style={styles.grantStatusMeta}>剩余 {grantRemainingText}</Text>
        </View>
        {grantUsable ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="结束远程输入接管"
            accessibilityState={{ busy: isRevokingInput }}
            disabled={isRevokingInput}
            onPress={() => void handleEndInputControl()}
            style={({ pressed }) => [styles.endGrantButton, (pressed || isRevokingInput) && styles.pressed]}
          >
            <XCircle size={15} color="#ffd1d6" />
            <Text style={styles.endGrantText}>{isRevokingInput ? "结束中" : "结束接管"}</Text>
          </Pressable>
        ) : null}
      </View>

      <Pressable
        accessibilityRole="button"
        accessibilityLabel="发送远程点击审批"
        disabled={!grantUsable}
        onLayout={handleViewerLayout}
        onPress={handleRemotePress}
        style={styles.viewer}
      >
        {frame ? (
          <Image
            resizeMode="contain"
            source={{ uri: frame.image }}
            style={[styles.screenImage, { aspectRatio }]}
            onLoadEnd={() => acknowledgeFrame(frame.sequence)}
          />
        ) : (
          <View style={styles.emptyFrame}>
            {showLoading ? <ActivityIndicator color="#75d39a" /> : <Monitor size={42} color="#93a2ad" />}
            <Text style={styles.emptyTitle}>{showLoading ? "正在连接电脑" : "等待屏幕画面"}</Text>
            <Text style={styles.emptyText}>屏幕共享可用时，你可以在这里查看电脑画面。</Text>
          </View>
        )}
      </Pressable>

      <View style={styles.footer}>
        {transportWarning ? <Text style={styles.transportWarningText}>{transportWarning}</Text> : null}
        {error ? <Text style={styles.errorText}>{error}</Text> : null}
        {inputError ? <Text style={styles.inputHintText}>{inputError}</Text> : null}
        {canRetry ? (
          <Pressable onPress={connect} style={({ pressed }) => [styles.retryButton, pressed && styles.pressed]}>
            <RefreshCcw size={16} color="#17222b" />
            <Text style={styles.retryButtonText}>重试屏幕查看</Text>
          </Pressable>
        ) : null}
        <Text style={styles.footerText}>
          {frame
            ? `最后更新于 ${shortDate(frame.timestamp)}。仅查看。`
            : "当前仅查看，不能从此页面控制电脑。"}
        </Text>
      </View>
    </SafeAreaView>
  );
}

function statusText(connection: ConnectionState): string {
  if (connection === "online") return "实时";
  if (connection === "connecting") return "连接中";
  if (connection === "paused") return "已暂停";
  return "离线";
}

function readableStreamError(message: string): string {
  const normalized = message.trim().toLowerCase();
  if (!normalized) return "暂时无法显示屏幕。请点重试重新连接。";
  if (normalized.includes("disabled")) return "桌面端尚未开启远程屏幕。请在 Lengrvis 设置中打开手机屏幕查看。";
  if (normalized.includes("unauthorized") || normalized.includes("token") || normalized.includes("scope")) {
    return "这台手机没有屏幕查看权限。请在桌面端重新配对后再试。";
  }
  return "暂时无法显示屏幕。请点重试重新连接。";
}

function inputStatusText(connection: InputConnectionState): string {
  if (connection === "online") return "远程点击已授权，每次点击仍需电脑端审批";
  if (connection === "connecting") return "正在连接远程点击";
  if (connection === "ready") return "电脑端已授权远程点击";
  if (connection === "offline") return "远程点击授权不可用";
  return "仅查看，电脑端尚未授权远程点击";
}

function inputErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "无法领取远程点击授权。请在电脑端重新授权。";
}

function remoteTransportNotice(connectionInfo: WebSocketConnectionInfo): TransportNotice {
  const { security } = connectionInfo;
  const webSocketScheme = connectionInfo.url.startsWith("wss:") ? "wss" : "ws";
  const httpScheme = security.isHttps ? "HTTPS" : "HTTP";
  const tokenNote = "token 通过 WebSocket protocol 发送，不写入 URL";
  if (security.requiresTlsTrust) {
    const fingerprint = formatTlsFingerprint(security.serverTls?.fingerprintSha256);
    return {
      tone: "warning",
      title: "HTTPS / wss 需要证书信任",
      detail: fingerprint ? `连接 ${security.host}，证书 SHA-256 ${fingerprint}。` : `连接 ${security.host}，后端未提供证书指纹。`,
      warning: `${security.serverTls?.warning ?? "HTTPS 证书需要核对或手动信任。"} ${tokenNote}。`,
    };
  }
  if (security.isHttps) {
    return {
      tone: "secure",
      title: "HTTPS / wss 加密通道",
      detail: `屏幕使用 ${webSocketScheme}://${security.host}，${tokenNote}。`,
    };
  }
  if (security.isInsecureLan) {
    return {
      tone: "danger",
      title: "LAN HTTP 已阻断",
      detail: `${httpScheme}/ws 不能承载手机 token、屏幕或远程输入连接。`,
      warning: "请在电脑端启用 HTTPS/WSS 或使用受信任证书后重新配对。",
    };
  }
  return {
    tone: "warning",
    title: "本机 HTTP / ws 通道",
    detail: `${httpScheme}/ws 连接 ${security.host}，${tokenNote}。`,
  };
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: "#17222b",
  },
  header: {
    paddingHorizontal: 20,
    paddingTop: 18,
    paddingBottom: 12,
    flexDirection: "row",
    gap: 12,
    alignItems: "center",
  },
  iconButton: {
    width: 42,
    height: 42,
    borderRadius: 8,
    backgroundColor: "#23313d",
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: "#3b4d5b",
  },
  headerText: {
    flex: 1,
    minWidth: 0,
  },
  kicker: {
    color: "#93a2ad",
    fontSize: 12,
    fontWeight: "800",
    textTransform: "uppercase",
  },
  headerTitle: {
    color: "#f7faf8",
    fontSize: 25,
    fontWeight: "800",
    marginTop: 2,
  },
  statusRow: {
    marginHorizontal: 20,
    minHeight: 38,
    borderRadius: 8,
    backgroundColor: "#23313d",
    borderWidth: 1,
    borderColor: "#3b4d5b",
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    gap: 8,
  },
  statusText: {
    flex: 1,
    color: "#e3ece7",
    fontWeight: "700",
  },
  statusMeta: {
    color: "#93a2ad",
    fontSize: 12,
    fontWeight: "700",
  },
  transportStatusRow: {
    marginHorizontal: 20,
    marginTop: 8,
    minHeight: 46,
    borderRadius: 8,
    backgroundColor: "#2b2f2f",
    borderWidth: 1,
    borderColor: "#5f553d",
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    paddingVertical: 7,
    gap: 8,
  },
  transportStatusSecure: {
    backgroundColor: "#1c302c",
    borderColor: "#366f5d",
  },
  transportStatusDanger: {
    backgroundColor: "#34272a",
    borderColor: "#70404a",
  },
  transportStatusTextWrap: {
    flex: 1,
    minWidth: 0,
  },
  transportStatusLabel: {
    color: "#f7faf8",
    fontSize: 12,
    fontWeight: "900",
  },
  transportStatusMeta: {
    color: "#c8d2ce",
    fontSize: 11,
    lineHeight: 16,
    marginTop: 2,
  },
  inputStatusRow: {
    marginHorizontal: 20,
    marginTop: 8,
    minHeight: 36,
    borderRadius: 8,
    backgroundColor: "#1d2a35",
    borderWidth: 1,
    borderColor: "#344856",
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    gap: 8,
  },
  inputStatusText: {
    flex: 1,
    color: "#d6e2dd",
    fontSize: 12,
    fontWeight: "700",
  },
  inputReconnect: {
    minHeight: 28,
    borderRadius: 8,
    backgroundColor: "#ffcf72",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 10,
  },
  inputReconnectText: {
    color: "#17222b",
    fontSize: 12,
    fontWeight: "800",
  },
  grantStatusRow: {
    marginHorizontal: 20,
    marginTop: 8,
    minHeight: 42,
    borderRadius: 8,
    backgroundColor: "#23313d",
    borderWidth: 1,
    borderColor: "#3b4d5b",
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10,
    paddingHorizontal: 12,
    paddingVertical: 7,
  },
  grantStatusTextWrap: {
    flex: 1,
    minWidth: 0,
  },
  grantStatusLabel: {
    color: "#f7faf8",
    fontSize: 13,
    fontWeight: "900",
  },
  grantStatusMeta: {
    color: "#93a2ad",
    fontSize: 11,
    fontWeight: "700",
    marginTop: 2,
  },
  endGrantButton: {
    minHeight: 32,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#70404a",
    backgroundColor: "#4d2630",
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingHorizontal: 10,
  },
  endGrantText: {
    color: "#ffd1d6",
    fontSize: 12,
    fontWeight: "900",
  },
  viewer: {
    flex: 1,
    paddingHorizontal: 12,
    paddingVertical: 18,
    alignItems: "center",
    justifyContent: "center",
  },
  screenImage: {
    width: "100%",
    maxHeight: "100%",
    backgroundColor: "#0c1217",
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#3b4d5b",
  },
  emptyFrame: {
    width: "100%",
    aspectRatio: 16 / 9,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#3b4d5b",
    backgroundColor: "#0c1217",
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
    gap: 10,
  },
  emptyTitle: {
    color: "#f7faf8",
    fontSize: 18,
    fontWeight: "800",
    textAlign: "center",
  },
  emptyText: {
    color: "#93a2ad",
    lineHeight: 20,
    textAlign: "center",
  },
  footer: {
    paddingHorizontal: 20,
    paddingBottom: 20,
    gap: 8,
  },
  footerText: {
    color: "#93a2ad",
    lineHeight: 20,
  },
  errorText: {
    color: "#ffcf72",
    lineHeight: 20,
  },
  inputHintText: {
    color: "#75d39a",
    lineHeight: 20,
  },
  transportWarningText: {
    color: "#ffcf72",
    lineHeight: 20,
  },
  retryButton: {
    alignSelf: "flex-start",
    minHeight: 40,
    borderRadius: 8,
    backgroundColor: "#ffcf72",
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 12,
  },
  retryButtonText: {
    color: "#17222b",
    fontWeight: "800",
  },
  pressed: {
    opacity: 0.72,
  },
});
