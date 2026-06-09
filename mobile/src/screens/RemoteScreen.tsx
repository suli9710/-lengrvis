import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  AppState,
  type AppStateStatus,
  Alert,
  Image,
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
  type GestureResponderEvent,
  type LayoutChangeEvent,
  useWindowDimensions,
} from "react-native";
import {
  ArrowLeft,
  ChevronsDown,
  ChevronsUp,
  CornerDownLeft,
  Delete,
  Keyboard,
  Monitor,
  MousePointer2,
  Pause,
  Play,
  RefreshCcw,
  Send,
  ShieldCheck,
  TriangleAlert,
  Wifi,
  WifiOff,
  XCircle,
} from "lucide-react-native";

import {
  AuthExpiredError,
  claimRemoteInputGrantToken,
  describeSessionBaseUrlSecurity,
  remoteInputWebSocketConnectionInfo,
  remoteScreenWebSocketConnectionInfo,
  revokeRemoteInputGrant,
  type BaseUrlSecurity,
  type PairingSession,
  type RemoteInputGrant,
  type RemoteScreenEvent,
} from "../api/client";
import { shortDate } from "../format";
import {
  isRemoteInputGrantUsable,
  remoteInputGrantDisplayStatus,
  type RemoteInputGrantDisplayStatus,
  mapViewerPointToRemote,
  remoteInputGrantExpiryDelayMs,
  remoteInputGrantRemainingText,
} from "../remoteInputGrant";

type ConnectionState = "offline" | "connecting" | "online" | "paused";
type InputConnectionState = "disabled" | "ready" | "connecting" | "online" | "offline";
type RemoteViewerZoom = "fit" | "close" | "detail";
const FRAME_ACK_FALLBACK_MS = 900;
const INITIAL_SCREEN_RECONNECT_DELAY_MS = 1000;
const MAX_SCREEN_RECONNECT_DELAY_MS = 15000;
const REMOTE_VIEWER_PADDING_HORIZONTAL = 12;
const REMOTE_VIEWER_PADDING_VERTICAL = 18;
const REMOTE_TEXT_INPUT_MAX_LENGTH = 180;
const REMOTE_VIEWER_ZOOM_OPTIONS: Array<{ value: RemoteViewerZoom; label: string; factor: number }> = [
  { value: "fit", label: "适应", factor: 1 },
  { value: "close", label: "放大", factor: 1.35 },
  { value: "detail", label: "细节", factor: 1.75 },
];
const REMOTE_KEY_CONTROLS: Array<{ key: string; label: string; icon?: "enter" | "delete" | "pageup" | "pagedown" }> = [
  { key: "enter", label: "回车", icon: "enter" },
  { key: "escape", label: "Esc" },
  { key: "tab", label: "Tab" },
  { key: "backspace", label: "退格", icon: "delete" },
  { key: "pageup", label: "上翻", icon: "pageup" },
  { key: "pagedown", label: "下翻", icon: "pagedown" },
];

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

type RemoteInputPayload =
  | { type: "click"; x: number; y: number }
  | { type: "type"; text: string }
  | { type: "key"; key: string };

export function RemoteScreen({
  grant,
  session,
  onBack,
  onRemoteInputGrantRevoked,
  onSessionExpired,
}: {
  grant: RemoteInputGrant | null;
  session: PairingSession;
  onBack: () => void;
  onRemoteInputGrantRevoked: (grant: RemoteInputGrant) => void;
  onSessionExpired: () => void;
}) {
  const windowSize = useWindowDimensions();
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [inputConnection, setInputConnection] = useState<InputConnectionState>(isRemoteInputGrantUsable(grant) ? "ready" : "disabled");
  const [frame, setFrame] = useState<ScreenFrame | null>(null);
  const [streamMeta, setStreamMeta] = useState({ fps: 0, quality: 0 });
  const [error, setError] = useState("");
  const [inputError, setInputError] = useState("");
  const [nowMs, setNowMs] = useState(Date.now());
  const [isRevokingInput, setIsRevokingInput] = useState(false);
  const [locallyRevokedGrantId, setLocallyRevokedGrantId] = useState("");
  const [viewerContainerSize, setViewerContainerSize] = useState({ width: 0, height: 0 });
  const [viewerSurfaceSize, setViewerSurfaceSize] = useState({ width: 0, height: 0 });
  const [viewerZoom, setViewerZoom] = useState<RemoteViewerZoom>("fit");
  const [textDraft, setTextDraft] = useState("");
  const [screenReconnectKey, setScreenReconnectKey] = useState(0);
  const [nextScreenReconnectAtMs, setNextScreenReconnectAtMs] = useState<number | null>(null);
  const [remoteTextFocused, setRemoteTextFocused] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const inputSocketRef = useRef<WebSocket | null>(null);
  const inputConnectionGenerationRef = useRef(0);
  const screenReconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const screenReconnectAttemptRef = useRef(0);
  const pausedByUserRef = useRef(false);
  const pendingFrameRef = useRef<ScreenFrame | null>(null);
  const frameRafRef = useRef<number | null>(null);
  const frameAckFallbackRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentFrameSequenceRef = useRef(0);
  const lastAcknowledgedSequenceRef = useRef(0);
  const lastRenderedAtRef = useRef(0);
  const degradedStreamRef = useRef(false);
  const isCompactRemoteLayout = windowSize.width > windowSize.height && windowSize.height < 520;
  const locallyRevokedGrant = grant?.id === locallyRevokedGrantId;
  const effectiveGrant = locallyRevokedGrant ? null : grant;
  const grantExpiryDelayMs = effectiveGrant ? remoteInputGrantExpiryDelayMs(effectiveGrant, nowMs) : null;
  const grantUsable = isRemoteInputGrantUsable(effectiveGrant, nowMs);
  const grantRemainingText = remoteInputGrantRemainingText(effectiveGrant, nowMs);
  const grantExpired = Boolean(effectiveGrant?.status === "active" && !effectiveGrant.revoked_at && grantExpiryDelayMs !== null && grantExpiryDelayMs <= 0);
  const remoteModeText = remoteInputModeText(grantUsable, inputConnection);
  const grantDisplayStatus = remoteInputGrantDisplayStatus(effectiveGrant, nowMs, { locallyRevoked: locallyRevokedGrant });
  const grantStatusMeta = remoteInputGrantStatusMeta({
    grantDisplayStatus,
    grantRemainingText,
    grantUsable,
  });
  const transportSecurity = useMemo(() => describeSessionBaseUrlSecurity(session), [session]);
  const transportNotice = remoteTransportNotice(transportSecurity);
  const transportWarning = transportNotice.warning ?? "";
  const transportBlocked = transportNotice.tone === "danger";

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

  const clearScreenReconnectTimer = useCallback(() => {
    if (screenReconnectTimerRef.current !== null) {
      clearTimeout(screenReconnectTimerRef.current);
      screenReconnectTimerRef.current = null;
    }
    setNextScreenReconnectAtMs(null);
  }, []);

  const closeSocket = useCallback(() => {
    clearScreenReconnectTimer();
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
  }, [clearFrameAckFallback, clearScreenReconnectTimer]);

  const closeInputSocket = useCallback(() => {
    inputConnectionGenerationRef.current += 1;
    inputSocketRef.current?.close();
    inputSocketRef.current = null;
  }, []);

  const resetInputConnection = useCallback(() => {
    setInputConnection(isRemoteInputGrantUsable(effectiveGrant) ? "ready" : "disabled");
  }, [effectiveGrant]);

  const clearRemoteFrame = useCallback(() => {
    pendingFrameRef.current = null;
    currentFrameSequenceRef.current = 0;
    lastAcknowledgedSequenceRef.current = 0;
    lastRenderedAtRef.current = 0;
    setFrame(null);
    setStreamMeta({ fps: 0, quality: 0 });
  }, []);

  const sendStreamConfig = useCallback((fps: number, quality: number) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== 1) {
      return;
    }
    socket.send(JSON.stringify({ fps, quality }));
    setStreamMeta({ fps, quality });
  }, []);

  const scheduleScreenReconnect = useCallback(() => {
    if (transportBlocked || pausedByUserRef.current || AppState.currentState !== "active" || screenReconnectTimerRef.current) {
      return;
    }
    const delay = Math.min(
      MAX_SCREEN_RECONNECT_DELAY_MS,
      INITIAL_SCREEN_RECONNECT_DELAY_MS * 2 ** screenReconnectAttemptRef.current,
    );
    screenReconnectAttemptRef.current += 1;
    setNextScreenReconnectAtMs(Date.now() + delay);
    screenReconnectTimerRef.current = setTimeout(() => {
      screenReconnectTimerRef.current = null;
      setNextScreenReconnectAtMs(null);
      if (transportBlocked || pausedByUserRef.current || AppState.currentState !== "active") {
        return;
      }
      setConnection("connecting");
      setScreenReconnectKey((current) => current + 1);
    }, delay);
  }, [transportBlocked]);

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
    clearScreenReconnectTimer();
    closeSocket();
    setConnection("connecting");
    setError("");

    let connectionInfo: ReturnType<typeof remoteScreenWebSocketConnectionInfo>;
    try {
      connectionInfo = remoteScreenWebSocketConnectionInfo(session);
    } catch (currentError) {
      if (currentError instanceof AuthExpiredError) {
        onSessionExpired();
        return;
      }
      setConnection("offline");
      setError(readableStreamConnectionError(currentError));
      return;
    }

    const socket = new WebSocket(connectionInfo.url, connectionInfo.protocols);
    socketRef.current = socket;

    socket.onopen = () => {
      if (socketRef.current !== socket) return;
      screenReconnectAttemptRef.current = 0;
      setNextScreenReconnectAtMs(null);
      setConnection("online");
      setError("");
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
          if (messageLooksSessionExpired(payload.message)) {
            onSessionExpired();
            return;
          }
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
      socketRef.current = null;
      if (webSocketCloseLooksSessionExpired(event, session)) {
        onSessionExpired();
        return;
      }
      if (event.code === 1008) {
        setError("无法查看电脑屏幕。请在桌面端设置中确认已开启手机屏幕查看；如果仍失败，请在手机和电脑端重新配对。");
        setConnection("offline");
        return;
      }
      setConnection((current) => (current === "paused" ? current : "offline"));
      scheduleScreenReconnect();
    };
  }, [clearScreenReconnectTimer, closeSocket, onSessionExpired, scheduleFrameRender, scheduleScreenReconnect, sendStreamConfig, session]);

  const connectInput = useCallback(async () => {
    closeInputSocket();
    if (transportBlocked) {
      setInputConnection("disabled");
      setInputError("当前网络连接不够安全，无法启用远程输入。请在电脑端开启安全连接后重新配对。");
      return;
    }
    if (!effectiveGrant || !isRemoteInputGrantUsable(effectiveGrant)) {
      setInputConnection("disabled");
      setInputError("");
      return;
    }
    const activeGrant = effectiveGrant;
    const connectionGeneration = inputConnectionGenerationRef.current;
    setInputConnection("connecting");
    setInputError("");
    try {
      const grantToken = await claimRemoteInputGrantToken(session, activeGrant.id);
      if (connectionGeneration !== inputConnectionGenerationRef.current || !isRemoteInputGrantUsable(activeGrant)) {
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
            setInputError("远程输入已发送到电脑端审批。");
            return;
          }
          if (payload.type === "error" || payload.type === "denied") {
            if (messageLooksSessionExpired(payload.message)) {
              onSessionExpired();
              return;
            }
            setInputError(readableInputFailureReason(payload.message, payload.type));
            if (remoteInputGrantFailureIsTerminal(payload.message)) {
              setInputConnection("disabled");
              if (effectiveGrant) onRemoteInputGrantRevoked({ ...effectiveGrant, status: "revoked", revoked_at: new Date().toISOString() });
              closeInputSocket();
            }
          }
        } catch {
          setInputError("电脑端返回了无法读取的远程输入结果。");
        }
      };
      socket.onerror = () => {
        if (inputSocketRef.current !== socket) return;
        setInputConnection("offline");
        setInputError("远程输入连接不可用。请在电脑端重新授权。");
      };
      socket.onclose = (event) => {
        if (inputSocketRef.current !== socket) return;
        inputSocketRef.current = null;
        if (webSocketCloseLooksSessionExpired(event, session)) {
          onSessionExpired();
          return;
        }
        if (webSocketCloseLooksRemoteInputGrantEnded(event, Boolean(effectiveGrant))) {
          setInputConnection("disabled");
          setInputError(readableInputFailureReason(event.reason || "expired"));
          if (effectiveGrant) onRemoteInputGrantRevoked({ ...effectiveGrant, status: "revoked", revoked_at: new Date().toISOString() });
          return;
        }
        setInputConnection(isRemoteInputGrantUsable(effectiveGrant) ? "offline" : "disabled");
      };
    } catch (currentError) {
      if (connectionGeneration !== inputConnectionGenerationRef.current) {
        return;
      }
      if (currentError instanceof AuthExpiredError) {
        onSessionExpired();
        return;
      }
      const terminalGrantFailure = remoteInputGrantFailureIsTerminal(currentError);
      if (terminalGrantFailure && effectiveGrant) {
        const revokedAt = new Date().toISOString();
        setLocallyRevokedGrantId(effectiveGrant.id);
        onRemoteInputGrantRevoked({ ...effectiveGrant, status: "revoked", revoked_at: revokedAt });
      }
      setInputConnection(terminalGrantFailure ? "disabled" : "offline");
      setInputError(inputErrorMessage(currentError));
    }
  }, [closeInputSocket, effectiveGrant, onRemoteInputGrantRevoked, onSessionExpired, session, transportBlocked]);

  useEffect(() => {
    resetInputConnection();
    setInputError("");
    closeInputSocket();
  }, [closeInputSocket, effectiveGrant, resetInputConnection]);

  useEffect(() => {
    if (transportBlocked) {
      clearScreenReconnectTimer();
      closeSocket();
      clearRemoteFrame();
      setConnection("offline");
      setError("");
      return () => {
        closeSocket();
      };
    }
    if (pausedByUserRef.current) {
      clearScreenReconnectTimer();
      setConnection("paused");
      return () => {
        closeSocket();
      };
    }
    connect();
    return () => {
      closeSocket();
    };
  }, [clearRemoteFrame, clearScreenReconnectTimer, closeSocket, connect, screenReconnectKey, transportBlocked]);

  useEffect(() => {
    if (transportBlocked) {
      closeInputSocket();
      setInputConnection("disabled");
      setInputError("");
      return () => {
        closeInputSocket();
      };
    }
    if (pausedByUserRef.current) {
      resetInputConnection();
      closeInputSocket();
      return () => {
        closeInputSocket();
      };
    }
    if (!grantUsable) {
      closeInputSocket();
      setInputConnection("disabled");
      setInputError(grantExpired ? "输入授权已过期。请在电脑端重新授权。" : "");
      return () => {
        closeInputSocket();
      };
    }
    void connectInput();
    return () => {
      closeInputSocket();
    };
  }, [closeInputSocket, connectInput, grantExpired, grantUsable, resetInputConnection, transportBlocked]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state: AppStateStatus) => {
      if (transportBlocked) {
        clearScreenReconnectTimer();
        closeSocket();
        closeInputSocket();
        clearRemoteFrame();
        setConnection("offline");
        setInputConnection("disabled");
        return;
      }
      if (state === "active") {
        if (!pausedByUserRef.current && !transportBlocked) {
          setConnection("connecting");
          connect();
          if (grantUsable) void connectInput();
        }
        return;
      }
      setConnection("paused");
      clearScreenReconnectTimer();
      resetInputConnection();
      closeSocket();
      closeInputSocket();
      clearRemoteFrame();
    });
    return () => subscription.remove();
  }, [clearRemoteFrame, clearScreenReconnectTimer, closeInputSocket, closeSocket, connect, connectInput, grantUsable, resetInputConnection, transportBlocked]);

  const handleToggleStream = () => {
    if (connection === "online" || connection === "connecting") {
      pausedByUserRef.current = true;
      clearScreenReconnectTimer();
      setConnection("paused");
      resetInputConnection();
      closeSocket();
      closeInputSocket();
      return;
    }
    if (transportBlocked) {
      clearScreenReconnectTimer();
      closeSocket();
      closeInputSocket();
      setConnection("offline");
      setInputConnection("disabled");
      clearRemoteFrame();
      setError("");
      setInputError("当前网络连接不够安全，无法启用远程输入。请在电脑端开启安全连接后重新配对。");
      return;
    }
    pausedByUserRef.current = false;
    clearScreenReconnectTimer();
    connect();
    if (grantUsable) void connectInput();
  };

  const handleViewerLayout = (event: LayoutChangeEvent) => {
    setViewerContainerSize({
      width: Math.max(0, event.nativeEvent.layout.width - REMOTE_VIEWER_PADDING_HORIZONTAL * 2),
      height: Math.max(0, event.nativeEvent.layout.height - REMOTE_VIEWER_PADDING_VERTICAL * 2),
    });
  };

  const handleViewerSurfaceLayout = (event: LayoutChangeEvent) => {
    setViewerSurfaceSize({
      width: event.nativeEvent.layout.width,
      height: event.nativeEvent.layout.height,
    });
  };

  const sendRemoteInputEvent = useCallback((payload: RemoteInputPayload, successMessage: string): boolean => {
    const socket = inputSocketRef.current;
    if (transportBlocked) {
      setInputError("当前网络连接不够安全，无法发送远程输入。请在电脑端开启安全连接后重新配对。");
      return false;
    }
    if (connection !== "online") {
      setInputError("屏幕恢复实时后才能发送远程输入。");
      return false;
    }
    if (!grantUsable) {
      setInputError("当前是只读观看。请先在电脑端授权远程输入。");
      return false;
    }
    if (!socket || socket.readyState !== 1) {
      if (grantUsable && inputConnection !== "online") {
        setInputError("正在连接远程输入，请稍后再点一次。");
        void connectInput();
      }
      return false;
    }
    socket.send(JSON.stringify(payload));
    setInputError(successMessage);
    return true;
  }, [connectInput, connection, grantUsable, inputConnection, transportBlocked]);

  const handleRemotePress = (event: GestureResponderEvent) => {
    if (!frame || !viewerSurfaceSize.width || !viewerSurfaceSize.height) {
      setInputError("等待屏幕画面后才能发送远程点击。");
      return;
    }
    const point = mapViewerPointToRemote(event.nativeEvent.locationX, event.nativeEvent.locationY, viewerSurfaceSize, frame);
    if (!point) {
      setInputError("点击位置不在屏幕画面内。");
      return;
    }
    sendRemoteInputEvent({ type: "click", x: point.x, y: point.y }, "点击已发送，等待电脑端审批。");
  };

  const handleSendText = () => {
    if (!textDraft.length) {
      setInputError("请输入要发送到电脑的文字。");
      return;
    }
    if (sendRemoteInputEvent({ type: "type", text: textDraft }, "文字已发送，等待电脑端审批。")) {
      setTextDraft("");
    }
  };

  const handleRemoteKey = (key: string) => {
    sendRemoteInputEvent({ type: "key", key }, "按键已发送，等待电脑端审批。");
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
      if (currentError instanceof AuthExpiredError) {
        onSessionExpired();
        return;
      }
      Alert.alert("结束接管失败", readableInputFailureReason(currentError));
    } finally {
      setIsRevokingInput(false);
    }
  };

  const online = connection === "online";
  const showLoading = connection === "connecting" && !frame;
  const aspectRatio = frame && frame.width > 0 && frame.height > 0 ? frame.width / frame.height : 16 / 9;
  const viewerZoomOption = REMOTE_VIEWER_ZOOM_OPTIONS.find((option) => option.value === viewerZoom) ?? REMOTE_VIEWER_ZOOM_OPTIONS[0];
  const baseViewerSurfaceDimensions = fitRemoteViewerSurface(viewerContainerSize, aspectRatio);
  const viewerSurfaceDimensions = zoomRemoteViewerSurface(baseViewerSurfaceDimensions, viewerZoomOption.factor);
  const viewerSurfaceStyle = viewerSurfaceDimensions
    ? { width: viewerSurfaceDimensions.width, height: viewerSurfaceDimensions.height }
    : { width: "100%" as const, aspectRatio };
  const viewerScrollContentStyle = {
    minWidth: viewerContainerSize.width,
    minHeight: viewerContainerSize.height,
  };
  const streamMetaText = streamStatusMetaText(streamMeta, connection);
  const reconnectStatusText = screenReconnectStatusText(connection, nextScreenReconnectAtMs, nowMs);
  const statusMetaText = reconnectStatusText || streamMetaText;
  const viewerFrameText = frame ? `${frame.originalWidth} x ${frame.originalHeight}` : "等待画面";
  const viewerStateText = viewerConnectionText(connection);
  const remoteClickDisabled = !grantUsable || !frame || transportBlocked || connection !== "online";
  const remoteInputControlsDisabled = remoteClickDisabled || inputConnection !== "online";
  const remoteTextSendDisabled = remoteInputControlsDisabled || !textDraft.length;
  const viewerInputText = transportNotice.tone === "danger" ? "连接已阻止" : viewerInputBadgeText(grantUsable, inputConnection);
  const inputConnectionText = transportBlocked ? "连接已阻止：无法启用远程输入" : inputStatusText(inputConnection);
  const viewerAccessibilityLabel = !frame
    ? "等待屏幕画面"
    : transportNotice.tone === "danger"
      ? "连接已阻止"
      : grantUsable
        ? "发送远程点击审批"
        : "只读屏幕查看";
  const viewerAccessibilityHint = viewerHintText({
    connection,
    frameAvailable: Boolean(frame),
    grantUsable,
    transportBlocked,
  });
  const canRetry = !transportBlocked && (connection === "offline" || !!error);
  const inputFeedbackWarning = inputFeedbackIsWarning(inputError);
  const footerText = transportBlocked
    ? "安全连接开启前，手机不会显示屏幕或发送远程输入。"
    : frame
      ? `最后更新于 ${shortDate(frame.timestamp)}。${grantUsable ? "远程输入仍需电脑端审批。" : "仅查看。"}`
      : "当前仅查看，不能从此页面控制电脑。";

  return (
    <KeyboardAvoidingView
      behavior={Platform.select({ ios: "padding", android: "height" })}
      style={styles.keyboardAvoiding}
    >
      <SafeAreaView style={styles.safeArea}>
      <StatusBar barStyle="light-content" backgroundColor="#17222b" />
      <View style={[styles.header, isCompactRemoteLayout && styles.headerCompact]}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="返回审批列表"
          accessibilityHint="停止查看远程屏幕并返回审批列表"
          hitSlop={8}
          onPress={onBack}
          style={({ pressed }) => [styles.iconButton, pressed && styles.pressed]}
        >
          <ArrowLeft size={20} color="#f7faf8" />
        </Pressable>
        <View style={styles.headerText}>
          <Text style={styles.kicker}>{remoteModeText}</Text>
          <Text style={styles.headerTitle}>远程屏幕</Text>
        </View>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={online || connection === "connecting" ? "暂停屏幕查看" : "继续屏幕查看"}
          accessibilityHint={online || connection === "connecting" ? "暂停屏幕画面并断开远程输入" : "重新连接电脑屏幕"}
          accessibilityState={{ busy: connection === "connecting", selected: online }}
          hitSlop={6}
          onPress={handleToggleStream}
          style={({ pressed }) => [styles.streamToggleButton, pressed && styles.pressed]}
        >
          {online || connection === "connecting" ? <Pause size={20} color="#f7faf8" /> : <Play size={20} color="#f7faf8" />}
          <Text style={styles.streamToggleText}>{online || connection === "connecting" ? "暂停" : "恢复"}</Text>
        </Pressable>
      </View>

      <ScrollView
        contentContainerStyle={[
          styles.remoteChromeContent,
          isCompactRemoteLayout && styles.remoteChromeContentCompact,
          remoteTextFocused && styles.remoteChromeContentTextFocused,
        ]}
        keyboardDismissMode={Platform.OS === "ios" ? "interactive" : "on-drag"}
        keyboardShouldPersistTaps="handled"
        nestedScrollEnabled
        style={styles.remoteChromeScroll}
      >
      <View accessibilityLiveRegion="polite" style={[styles.statusRow, isCompactRemoteLayout && styles.statusRowCompact]}>
        {online ? <Wifi size={16} color="#75d39a" /> : <WifiOff size={16} color="#ffcf72" />}
        <Text style={styles.statusText}>{statusText(connection)}</Text>
        {statusMetaText ? <Text style={[styles.statusMeta, reconnectStatusText && styles.statusMetaWarning]}>{statusMetaText}</Text> : null}
      </View>

      <View
        style={[
          styles.transportStatusRow,
          isCompactRemoteLayout && styles.transportStatusRowCompact,
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

      <View accessibilityLiveRegion="polite" style={[styles.inputStatusRow, isCompactRemoteLayout && styles.inputStatusRowCompact]}>
        <MousePointer2 size={15} color={inputConnection === "online" ? "#75d39a" : "#ffcf72"} />
        <Text style={styles.inputStatusText}>{inputConnectionText}</Text>
        {grantUsable && inputConnection !== "online" && !transportBlocked ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="连接远程输入"
            accessibilityHint="使用电脑端已授权的远程输入接管"
            hitSlop={6}
            onPress={() => void connectInput()}
            style={({ pressed }) => [styles.inputReconnect, pressed && styles.pressed]}
          >
            <Text style={styles.inputReconnectText}>连接</Text>
          </Pressable>
        ) : null}
      </View>

      <View
        accessibilityLabel={`${remoteModeText}。${grantStatusMeta}`}
        accessibilityLiveRegion="polite"
        style={[styles.grantStatusRow, isCompactRemoteLayout && styles.grantStatusRowCompact]}
      >
        <View style={styles.grantStatusTextWrap}>
          <Text style={styles.grantStatusLabel}>{remoteModeText}</Text>
          <Text style={styles.grantStatusMeta}>{grantStatusMeta}</Text>
        </View>
        {grantUsable ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="结束远程输入接管"
            accessibilityHint="撤销当前远程输入授权，保留只读屏幕查看"
            accessibilityState={{ busy: isRevokingInput }}
            disabled={isRevokingInput}
            hitSlop={6}
            onPress={() => void handleEndInputControl()}
            style={({ pressed }) => [styles.endGrantButton, (pressed || isRevokingInput) && styles.pressed]}
          >
            <XCircle size={15} color="#ffd1d6" />
            <Text style={styles.endGrantText}>{isRevokingInput ? "结束中" : "结束接管"}</Text>
          </Pressable>
        ) : null}
      </View>

      <View onLayout={handleViewerLayout} style={[styles.viewer, isCompactRemoteLayout && styles.viewerCompact, remoteTextFocused && styles.viewerTextFocused]}>
        <ScrollView
          bounces={false}
          contentContainerStyle={[styles.viewerScrollContent, viewerScrollContentStyle]}
          nestedScrollEnabled
          showsVerticalScrollIndicator={viewerZoom !== "fit"}
          style={styles.viewerScroll}
        >
          <ScrollView
            bounces={false}
            contentContainerStyle={[styles.viewerScrollContent, viewerScrollContentStyle]}
            horizontal
            nestedScrollEnabled
            showsHorizontalScrollIndicator={viewerZoom !== "fit"}
            style={styles.viewerScroll}
          >
            <Pressable
              android_disableSound
              accessibilityRole="button"
              accessibilityLabel={viewerAccessibilityLabel}
              accessibilityHint={viewerAccessibilityHint}
              accessibilityState={{ disabled: remoteClickDisabled }}
              accessibilityValue={{ text: `${viewerStateText}，${viewerInputText}` }}
              disabled={remoteClickDisabled}
              onLayout={handleViewerSurfaceLayout}
              onPress={handleRemotePress}
              style={({ pressed }) => [
                styles.viewerSurface,
                viewerSurfaceStyle,
                transportNotice.tone === "danger" && styles.viewerSurfaceBlocked,
                pressed && styles.viewerSurfacePressed,
              ]}
            >
              {frame ? (
                <Image
                  resizeMode="contain"
                  source={{ uri: frame.image }}
                  style={styles.screenImage}
                  onLoadEnd={() => acknowledgeFrame(frame.sequence)}
                />
              ) : (
                <View style={styles.emptyFrame}>
                  {showLoading ? <ActivityIndicator color="#75d39a" /> : <Monitor size={42} color="#93a2ad" />}
                  <Text style={styles.emptyTitle}>{showLoading ? "正在连接电脑" : "等待屏幕画面"}</Text>
                  <Text style={styles.emptyText}>屏幕共享可用时，你可以在这里查看电脑画面。</Text>
                </View>
              )}
              <View pointerEvents="none" style={styles.screenOverlayTop}>
                <Text style={styles.screenBadge}>{viewerFrameText}</Text>
                <Text style={[styles.screenBadge, online ? styles.screenBadgeSecure : styles.screenBadgeWarning]}>{viewerStateText}</Text>
              </View>
              <View pointerEvents="none" style={styles.screenOverlayBottom}>
                <Text style={[styles.inputModeBadge, grantUsable ? styles.inputModeBadgeActive : styles.inputModeBadgeReadonly]}>
                  {viewerInputText}
                </Text>
              </View>
            </Pressable>
          </ScrollView>
        </ScrollView>
      </View>

      <View style={[styles.controlDeck, isCompactRemoteLayout && styles.controlDeckCompact]}>
        <View accessibilityRole="tablist" style={styles.zoomRow}>
          {REMOTE_VIEWER_ZOOM_OPTIONS.map((option) => {
            const selected = option.value === viewerZoom;
            return (
              <Pressable
                key={option.value}
                accessibilityLabel={`屏幕缩放${option.label}`}
                accessibilityRole="tab"
                accessibilityState={{ selected }}
                hitSlop={4}
                onPress={() => setViewerZoom(option.value)}
                style={({ pressed }) => [styles.zoomButton, selected && styles.zoomButtonActive, pressed && styles.pressed]}
              >
                <Text style={[styles.zoomButtonText, selected && styles.zoomButtonTextActive]}>{option.label}</Text>
              </Pressable>
            );
          })}
        </View>

        <View style={styles.textInputRow}>
          <Keyboard size={16} color={remoteInputControlsDisabled ? "#7f8d96" : "#d6e2dd"} />
          <TextInput
            accessibilityLabel="发送文字到电脑"
            autoCapitalize="none"
            autoComplete="off"
            autoCorrect={false}
            disableFullscreenUI
            editable={!remoteInputControlsDisabled}
            importantForAutofill="no"
            maxLength={REMOTE_TEXT_INPUT_MAX_LENGTH}
            blurOnSubmit={false}
            onChangeText={setTextDraft}
            onBlur={() => setRemoteTextFocused(false)}
            onFocus={() => setRemoteTextFocused(true)}
            onSubmitEditing={handleSendText}
            placeholder={remoteInputControlsDisabled ? "远程输入可用后才能发送文字" : "输入要发送到电脑的文字"}
            placeholderTextColor="#7f8d96"
            returnKeyType="send"
            spellCheck={false}
            style={[styles.textInput, remoteInputControlsDisabled && styles.textInputDisabled]}
            textContentType="none"
            value={textDraft}
          />
          <Pressable
            accessibilityLabel="发送文字"
            accessibilityRole="button"
            accessibilityState={{ disabled: remoteTextSendDisabled }}
            disabled={remoteTextSendDisabled}
            hitSlop={6}
            onPress={handleSendText}
            style={({ pressed }) => [styles.sendTextButton, remoteTextSendDisabled && styles.controlButtonDisabled, pressed && styles.pressed]}
          >
            <Send size={16} color={remoteTextSendDisabled ? "#7f8d96" : "#17222b"} />
          </Pressable>
        </View>

        <View style={styles.keyControlRow}>
          {REMOTE_KEY_CONTROLS.map((control) => (
            <Pressable
              key={control.key}
              accessibilityLabel={`发送${control.label}按键`}
              accessibilityRole="button"
              accessibilityState={{ disabled: remoteInputControlsDisabled }}
              disabled={remoteInputControlsDisabled}
              hitSlop={4}
              onPress={() => handleRemoteKey(control.key)}
              style={({ pressed }) => [styles.keyControlButton, remoteInputControlsDisabled && styles.controlButtonDisabled, pressed && styles.pressed]}
            >
              <RemoteKeyIcon icon={control.icon} disabled={remoteInputControlsDisabled} />
              <Text style={[styles.keyControlText, remoteInputControlsDisabled && styles.keyControlTextDisabled]}>{control.label}</Text>
            </Pressable>
          ))}
        </View>
      </View>

      <View style={[styles.footer, isCompactRemoteLayout && styles.footerCompact, remoteTextFocused && styles.footerTextFocused]}>
        {transportWarning ? <Text style={styles.transportWarningText}>{transportWarning}</Text> : null}
        {error ? <Text accessibilityLiveRegion="polite" accessibilityRole="alert" style={styles.errorText}>{error}</Text> : null}
        {inputError ? (
          <Text accessibilityLiveRegion="polite" accessibilityRole="alert" style={[styles.inputHintText, inputFeedbackWarning && styles.inputWarningText]}>
            {inputError}
          </Text>
        ) : null}
        {canRetry ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="重试屏幕查看"
            accessibilityHint="重新连接电脑屏幕画面"
            hitSlop={6}
            onPress={connect}
            style={({ pressed }) => [styles.retryButton, pressed && styles.pressed]}
          >
            <RefreshCcw size={16} color="#17222b" />
            <Text style={styles.retryButtonText}>重试屏幕查看</Text>
          </Pressable>
        ) : null}
        {isCompactRemoteLayout ? null : <Text style={styles.footerText}>{footerText}</Text>}
      </View>
      </ScrollView>
      </SafeAreaView>
    </KeyboardAvoidingView>
  );
}

function RemoteKeyIcon({ icon, disabled }: { icon?: "enter" | "delete" | "pageup" | "pagedown"; disabled: boolean }) {
  const color = disabled ? "#7f8d96" : "#d6e2dd";
  if (icon === "enter") return <CornerDownLeft size={14} color={color} />;
  if (icon === "delete") return <Delete size={14} color={color} />;
  if (icon === "pageup") return <ChevronsUp size={14} color={color} />;
  if (icon === "pagedown") return <ChevronsDown size={14} color={color} />;
  return null;
}

function statusText(connection: ConnectionState): string {
  if (connection === "online") return "实时";
  if (connection === "connecting") return "连接中";
  if (connection === "paused") return "已暂停";
  return "离线";
}

function streamStatusMetaText(meta: { fps: number; quality: number }, connection: ConnectionState): string {
  if (connection !== "online" || !meta.fps) return "";
  if (meta.fps <= 1 || meta.quality <= 42) return `低带宽 ${meta.fps} FPS`;
  return `${meta.fps} FPS`;
}

function screenReconnectStatusText(connection: ConnectionState, nextReconnectAtMs: number | null, nowMs: number): string {
  if (connection !== "offline" || nextReconnectAtMs === null) return "";
  const remainingSeconds = Math.max(1, Math.ceil((nextReconnectAtMs - nowMs) / 1000));
  return `自动重连 ${remainingSeconds} 秒`;
}

function viewerConnectionText(connection: ConnectionState): string {
  if (connection === "online") return "实时画面";
  if (connection === "connecting") return "正在连接";
  if (connection === "paused") return "已暂停";
  return "离线";
}

function viewerInputBadgeText(grantUsable: boolean, connection: InputConnectionState): string {
  if (!grantUsable) return "只读屏幕查看";
  if (connection === "online") return "已授权输入";
  if (connection === "connecting") return "输入连接中";
  if (connection === "offline") return "输入连接失败";
  return "已授权，待连接";
}

function viewerHintText({
  connection,
  frameAvailable,
  grantUsable,
  transportBlocked,
}: {
  connection: ConnectionState;
  frameAvailable: boolean;
  grantUsable: boolean;
  transportBlocked: boolean;
}): string {
  if (transportBlocked) return "安全连接开启前，手机不会显示屏幕或发送远程输入";
  if (!frameAvailable) return "等待电脑端发送屏幕画面";
  if (connection !== "online") return "恢复实时屏幕后才能发送远程输入";
  if (!grantUsable) return "电脑端授权输入前只能查看屏幕";
  return "轻点屏幕发送点击，也可以发送文字或常用按键；电脑端批准后才会执行";
}

function fitRemoteViewerSurface(
  container: { width: number; height: number },
  aspectRatio: number,
): { width: number; height: number } | null {
  if (!Number.isFinite(container.width) || !Number.isFinite(container.height)) return null;
  if (container.width <= 0 || container.height <= 0 || !Number.isFinite(aspectRatio) || aspectRatio <= 0) return null;
  const containerRatio = container.width / container.height;
  if (containerRatio > aspectRatio) {
    const height = container.height;
    return { width: height * aspectRatio, height };
  }
  const width = container.width;
  return { width, height: width / aspectRatio };
}

function zoomRemoteViewerSurface(
  surface: { width: number; height: number } | null,
  zoomFactor: number,
): { width: number; height: number } | null {
  if (!surface) return null;
  const factor = Number.isFinite(zoomFactor) && zoomFactor > 0 ? zoomFactor : 1;
  return {
    width: surface.width * factor,
    height: surface.height * factor,
  };
}

function readableStreamConnectionError(error: unknown): string {
  const message = error instanceof Error ? error.message.trim().toLowerCase() : "";
  if (
    message.includes("非本机 http") ||
    message.includes("http") ||
    message.includes("明文") ||
    message.includes("insecure")
  ) {
    return "当前网络连接不够安全，手机不会继续查看屏幕。请在电脑端开启安全连接后重新配对。";
  }
  return "暂时无法显示屏幕。请确认 Lengrvis 已打开，然后点重试。";
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
  if (connection === "online") return "已授权输入：点击、文字和按键仍需电脑端审批";
  if (connection === "connecting") return "正在启用已授权输入";
  if (connection === "ready") return "电脑端已授权输入，点击连接后可使用";
  if (connection === "offline") return "已授权输入连接失败，请重试或在电脑端重新授权";
  return "只读观看：电脑端尚未授权输入";
}

function inputErrorMessage(error: unknown): string {
  return readableInputFailureReason(error);
}

function remoteInputModeText(grantUsable: boolean, connection: InputConnectionState): string {
  if (!grantUsable) return "只读观看";
  if (connection === "online") return "已授权输入";
  if (connection === "connecting") return "正在启用输入";
  return "已授权输入，待连接";
}

function remoteInputGrantStatusMeta({
  grantDisplayStatus,
  grantRemainingText,
  grantUsable,
}: {
  grantDisplayStatus: RemoteInputGrantDisplayStatus;
  grantRemainingText: string;
  grantUsable: boolean;
}): string {
  if (grantUsable) return `授权剩余 ${grantRemainingText}；点击、文字和按键仍需电脑端审批`;
  return grantDisplayStatus.detail;
}

function readableInputFailureReason(errorOrMessage: unknown, eventType?: string): string {
  if (eventType === "denied") return "电脑端已结束或拒绝这次远程输入。";
  const message = typeof errorOrMessage === "string"
    ? errorOrMessage
    : errorOrMessage instanceof Error
      ? errorOrMessage.message
      : "";
  const normalized = message.trim().toLowerCase();
  if (!normalized) return "远程输入暂时不可用。请在电脑端重新授权后重试。";
  if (normalized.includes("非本机 http") || normalized.includes("明文") || normalized.includes("insecure lan")) {
    return "当前网络连接不够安全，无法发送远程输入。请在电脑端开启安全连接后重新配对。";
  }
  if (normalized.includes("expired") || normalized.includes("410")) return "输入授权已过期。请在电脑端重新授权。";
  if (normalized.includes("revoked") || normalized.includes("denied")) return "电脑端已结束或拒绝这次远程输入。";
  if (normalized.includes("unauthorized") || normalized.includes("forbidden") || normalized.includes("token") || normalized.includes("scope")) {
    return "这台手机没有远程输入权限。请在电脑端重新授权。";
  }
  if (normalized.includes("fetch") || normalized.includes("network") || normalized.includes("failed") || normalized.includes("timeout")) {
    return "远程输入连接失败。请确认电脑端在线后重试。";
  }
  return "远程输入暂时不可用。请在电脑端重新授权后重试。";
}

function inputFeedbackIsWarning(message: string): boolean {
  return /失败|过期|拒绝|没有|不可用|不够安全|等待屏幕|不在屏幕/.test(message);
}

function webSocketCloseLooksSessionExpired(event: { code?: number; reason?: string }, session?: Pick<PairingSession, "token" | "expiresAt">): boolean {
  if (pairingSessionTokenIsMissingOrExpired(session)) return true;
  if (event.code !== 1008 && event.code !== 4001 && event.code !== 4401) return false;
  return messageLooksSessionExpired(event.reason);
}

function webSocketCloseLooksRemoteInputGrantEnded(event: { code?: number; reason?: string }, hasActiveGrant = false): boolean {
  if (event.code === 410) return true;
  if (event.code !== 1008 && event.code !== 410 && event.code !== 4403) return false;
  if (hasActiveGrant && event.code === 1008) return true;
  return remoteInputGrantFailureIsTerminal(event.reason);
}

function messageLooksSessionExpired(message: unknown): boolean {
  const normalized = normalizedMessage(message);
  if (!normalized) return false;
  const mentionsSession =
    normalized.includes("session") ||
    normalized.includes("mobile token") ||
    normalized.includes("pairing token") ||
    normalized.includes("mobile device");
  const mentionsExpiredAuth =
    normalized.includes("expired") ||
    normalized.includes("unauthorized") ||
    normalized.includes("auth") ||
    normalized.includes("missing") ||
    normalized.includes("revoked") ||
    normalized.includes("not paired") ||
    normalized.includes("inactive");
  return mentionsSession && mentionsExpiredAuth;
}

function remoteInputGrantFailureIsTerminal(message: unknown): boolean {
  const normalized = normalizedMessage(message);
  if (!normalized) return false;
  const mentionsGrant = normalized.includes("grant") || normalized.includes("remote input");
  return (
    mentionsGrant &&
    (
      normalized.includes("expired") ||
      normalized.includes("revoked") ||
      normalized.includes("not active") ||
      normalized.includes("inactive") ||
      normalized.includes("invalid") ||
      normalized.includes("missing") ||
      normalized.includes("required") ||
      normalized.includes("410") ||
      normalized.includes("gone")
    )
  );
}

function normalizedMessage(message: unknown): string {
  if (typeof message === "string") return message.trim().toLowerCase();
  if (message instanceof Error) return message.message.trim().toLowerCase();
  return "";
}

function pairingSessionTokenIsMissingOrExpired(session?: Pick<PairingSession, "token" | "expiresAt">): boolean {
  if (!session) return false;
  if (!session.token?.trim()) return true;
  if (!session.expiresAt) return false;
  const expiresAt = Date.parse(session.expiresAt);
  return !Number.isFinite(expiresAt) || expiresAt <= Date.now() + 1000;
}

function remoteTransportNotice(security: BaseUrlSecurity): TransportNotice {
  if (security.isInsecureLan || (!security.isLoopback && (!security.backendTlsEnabled || security.webSocketProtocol !== "wss:"))) {
    return {
      tone: "danger",
      title: "连接已阻止",
      detail: "当前网络连接不够安全，手机不会继续查看屏幕或发送远程输入。",
      warning: "请在电脑端开启安全连接后重新配对。",
    };
  }
  if (security.requiresTlsTrust) {
    return {
      tone: "warning",
      title: "需要确认这台电脑",
      detail: "首次安全连接需要你在电脑端确认。确认前请保持只读观看，不要处理不认识的请求。",
      warning: "如果这不是你正在使用的电脑，请返回并重新配对。",
    };
  }
  if (security.isHttps) {
    return {
      tone: "secure",
      title: "安全连接已开启",
      detail: "屏幕查看和远程输入会通过安全连接发送。",
    };
  }
  return {
    tone: "warning",
    title: "仅限本机调试连接",
    detail: "当前连接只适合本机测试；实际使用请在电脑端生成安全配对信息。",
  };
}

const styles = StyleSheet.create({
  keyboardAvoiding: {
    flex: 1,
    backgroundColor: "#17222b",
  },
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
  headerCompact: {
    paddingTop: 8,
    paddingBottom: 6,
  },
  remoteChromeScroll: {
    flex: 1,
  },
  remoteChromeContent: {
    flexGrow: 1,
  },
  remoteChromeContentCompact: {
    paddingBottom: Platform.select({ android: 8, default: 0 }),
  },
  remoteChromeContentTextFocused: {
    paddingBottom: Platform.select({ android: 168, default: 96 }),
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
  streamToggleButton: {
    minWidth: 78,
    height: 42,
    borderRadius: 8,
    backgroundColor: "#23313d",
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 10,
    borderWidth: 1,
    borderColor: "#3b4d5b",
  },
  streamToggleText: {
    color: "#f7faf8",
    fontSize: 12,
    fontWeight: "900",
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
  statusRowCompact: {
    minHeight: 30,
    marginHorizontal: 12,
    paddingHorizontal: 10,
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
  statusMetaWarning: {
    color: "#ffcf72",
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
  transportStatusRowCompact: {
    minHeight: 34,
    marginHorizontal: 12,
    marginTop: 5,
    paddingVertical: 4,
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
  inputStatusRowCompact: {
    minHeight: 32,
    marginHorizontal: 12,
    marginTop: 5,
  },
  inputStatusText: {
    flex: 1,
    color: "#d6e2dd",
    fontSize: 12,
    fontWeight: "700",
  },
  inputReconnect: {
    minHeight: 44,
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
  grantStatusRowCompact: {
    minHeight: 34,
    marginHorizontal: 12,
    marginTop: 5,
    paddingVertical: 4,
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
    minHeight: 44,
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
    minHeight: 180,
    paddingHorizontal: REMOTE_VIEWER_PADDING_HORIZONTAL,
    paddingVertical: REMOTE_VIEWER_PADDING_VERTICAL,
    alignItems: "center",
    justifyContent: "center",
  },
  viewerCompact: {
    minHeight: 220,
    paddingHorizontal: 8,
    paddingVertical: 8,
  },
  viewerTextFocused: {
    minHeight: Platform.select({ android: 132, default: 150 }),
    paddingVertical: 8,
  },
  viewerScroll: {
    alignSelf: "stretch",
    flex: 1,
  },
  viewerScrollContent: {
    alignItems: "center",
    justifyContent: "center",
  },
  viewerSurface: {
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#3b4d5b",
    backgroundColor: "#0c1217",
    overflow: "hidden",
    alignItems: "center",
    justifyContent: "center",
  },
  viewerSurfacePressed: {
    opacity: 0.86,
  },
  viewerSurfaceBlocked: {
    borderColor: "#70404a",
  },
  screenImage: {
    width: "100%",
    height: "100%",
    backgroundColor: "#0c1217",
  },
  emptyFrame: {
    width: "100%",
    height: "100%",
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
  screenOverlayTop: {
    position: "absolute",
    top: 8,
    left: 8,
    right: 8,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: 8,
  },
  screenBadge: {
    maxWidth: "48%",
    borderRadius: 8,
    overflow: "hidden",
    borderWidth: 1,
    borderColor: "#3b4d5b",
    backgroundColor: "rgba(12, 18, 23, 0.78)",
    color: "#f7faf8",
    fontSize: 11,
    fontWeight: "900",
    paddingHorizontal: 8,
    paddingVertical: 5,
  },
  screenBadgeSecure: {
    borderColor: "#366f5d",
    color: "#c7f5d7",
  },
  screenBadgeWarning: {
    borderColor: "#5f553d",
    color: "#ffe0a0",
  },
  screenOverlayBottom: {
    position: "absolute",
    left: 8,
    right: 8,
    bottom: 8,
    flexDirection: "row",
    alignItems: "center",
  },
  inputModeBadge: {
    maxWidth: "100%",
    borderRadius: 8,
    overflow: "hidden",
    borderWidth: 1,
    borderColor: "#3b4d5b",
    backgroundColor: "rgba(35, 49, 61, 0.88)",
    color: "#c8d2ce",
    fontSize: 11,
    fontWeight: "900",
    paddingHorizontal: 8,
    paddingVertical: 5,
  },
  inputModeBadgeActive: {
    borderColor: "#366f5d",
    backgroundColor: "rgba(28, 48, 44, 0.9)",
    color: "#c7f5d7",
  },
  inputModeBadgeReadonly: {
    borderColor: "#3b4d5b",
    backgroundColor: "rgba(35, 49, 61, 0.88)",
    color: "#c8d2ce",
  },
  controlDeck: {
    marginHorizontal: 20,
    marginBottom: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#344856",
    backgroundColor: "#1d2a35",
    padding: 10,
    gap: 9,
  },
  controlDeckCompact: {
    marginHorizontal: 12,
    marginBottom: 6,
    padding: 8,
    gap: 7,
  },
  zoomRow: {
    minHeight: 48,
    borderRadius: 8,
    backgroundColor: "#14202a",
    flexDirection: "row",
    padding: 3,
    gap: 4,
  },
  zoomButton: {
    flex: 1,
    minHeight: 42,
    borderRadius: 7,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 8,
  },
  zoomButtonActive: {
    backgroundColor: "#ffcf72",
  },
  zoomButtonText: {
    color: "#c8d2ce",
    fontSize: 12,
    fontWeight: "900",
  },
  zoomButtonTextActive: {
    color: "#17222b",
  },
  textInputRow: {
    minHeight: 48,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#344856",
    backgroundColor: "#14202a",
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 10,
  },
  textInput: {
    flex: 1,
    minWidth: 0,
    color: "#f7faf8",
    fontSize: 14,
    paddingVertical: 8,
  },
  textInputDisabled: {
    color: "#7f8d96",
  },
  sendTextButton: {
    width: 44,
    height: 44,
    borderRadius: 8,
    backgroundColor: "#ffcf72",
    alignItems: "center",
    justifyContent: "center",
  },
  keyControlRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
  },
  keyControlButton: {
    minHeight: 48,
    minWidth: 74,
    flexGrow: 1,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#3b4d5b",
    backgroundColor: "#23313d",
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 5,
    paddingHorizontal: 8,
  },
  controlButtonDisabled: {
    backgroundColor: "#202b34",
    borderColor: "#2f3c46",
    opacity: 0.72,
  },
  keyControlText: {
    color: "#d6e2dd",
    fontSize: 12,
    fontWeight: "900",
  },
  keyControlTextDisabled: {
    color: "#7f8d96",
  },
  footer: {
    paddingHorizontal: 20,
    paddingBottom: 20,
    gap: 8,
  },
  footerCompact: {
    paddingHorizontal: 12,
    paddingBottom: Platform.select({ android: 12, default: 8 }),
    gap: 5,
  },
  footerTextFocused: {
    paddingBottom: Platform.select({ android: 28, default: 16 }),
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
  inputWarningText: {
    color: "#ffcf72",
  },
  transportWarningText: {
    color: "#ffcf72",
    lineHeight: 20,
  },
  retryButton: {
    alignSelf: "flex-start",
    minHeight: 44,
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
