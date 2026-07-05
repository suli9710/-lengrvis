import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AppState,
  type AppStateStatus,
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  SafeAreaView,
  ScrollView,
  StatusBar,
  Text,
  View,
  type GestureResponderEvent,
  type LayoutChangeEvent,
  useWindowDimensions,
} from "react-native";
import { Pause, Play } from "lucide-react-native";

import {
  AuthExpiredError,
  claimRemoteInputGrantToken,
  describeSessionBaseUrlSecurity,
  remoteInputWebSocketConnectionInfo,
  remoteScreenWebSocketConnectionInfo,
  revokeRemoteInputGrant,
  type PairingSession,
  type RemoteInputGrant,
  type RemoteScreenEvent,
} from "../api/client";
import { shortDate } from "../format";
import {
  isRemoteInputGrantUsable,
  remoteInputGrantDisplayStatus,
  mapViewerPointToRemote,
  remoteInputGrantExpiryDelayMs,
  remoteInputGrantRemainingText,
} from "../remoteInputGrant";
import {
  FRAME_ACK_FALLBACK_MS,
  INITIAL_SCREEN_RECONNECT_DELAY_MS,
  MAX_SCREEN_RECONNECT_DELAY_MS,
  REMOTE_VIEWER_PADDING_HORIZONTAL,
  REMOTE_VIEWER_PADDING_VERTICAL,
  REMOTE_VIEWER_ZOOM_OPTIONS,
  finiteIntegerOrZero,
  fitRemoteViewerSurface,
  inputErrorMessage,
  inputFeedbackIsWarning,
  inputStatusText,
  messageLooksSessionExpired,
  readableInputFailureReason,
  readableStreamConnectionError,
  readableStreamError,
  remoteInputGrantFailureIsTerminal,
  remoteInputGrantStatusMeta,
  remoteInputModeText,
  remoteTransportNotice,
  screenReconnectStatusText,
  streamStatusMetaText,
  viewerConnectionText,
  viewerHintText,
  viewerInputBadgeText,
  webSocketCloseLooksRemoteInputGrantEnded,
  webSocketCloseLooksSessionExpired,
  zoomRemoteViewerSurface,
  type ConnectionState,
  type InputConnectionState,
  type RemoteViewerZoom,
} from "./remoteScreenPresentation";
import { RemoteFeedbackFooter } from "./RemoteFeedbackFooter";
import { RemoteControlDeck } from "./RemoteControlDeck";
import { RemoteStatusPanels } from "./RemoteStatusPanels";
import { RemoteViewerSurface } from "./RemoteViewerSurface";
import { styles } from "./remoteScreenStyles";
import type { RemoteInputPayload, ScreenFrame } from "./remoteScreenTypes";

export function RemoteScreen({
  grant,
  session,
  onBack: _onBack,
  onRemoteInputGrantRevoked,
  onSessionExpired,
}: {
  grant: RemoteInputGrant | null;
  session: PairingSession;
  onBack: () => void;
  onRemoteInputGrantRevoked: (grant: RemoteInputGrant) => void;
  onSessionExpired: () => void;
}) {
  void _onBack;
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
            screenOriginX: finiteIntegerOrZero(payload.screen_origin_x),
            screenOriginY: finiteIntegerOrZero(payload.screen_origin_y),
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
      <RemoteStatusPanels
        connection={connection}
        grantStatusMeta={grantStatusMeta}
        grantUsable={grantUsable}
        inputConnection={inputConnection}
        inputConnectionText={inputConnectionText}
        isCompactRemoteLayout={isCompactRemoteLayout}
        isRevokingInput={isRevokingInput}
        online={online}
        reconnectStatusText={reconnectStatusText}
        remoteModeText={remoteModeText}
        statusMetaText={statusMetaText}
        transportBlocked={transportBlocked}
        transportNotice={transportNotice}
        onConnectInput={() => void connectInput()}
        onEndInputControl={() => void handleEndInputControl()}
      />

      <RemoteViewerSurface
        frame={frame}
        grantUsable={grantUsable}
        isCompactRemoteLayout={isCompactRemoteLayout}
        online={online}
        remoteClickDisabled={remoteClickDisabled}
        remoteTextFocused={remoteTextFocused}
        showLoading={showLoading}
        transportDanger={transportNotice.tone === "danger"}
        viewerAccessibilityHint={viewerAccessibilityHint}
        viewerAccessibilityLabel={viewerAccessibilityLabel}
        viewerFrameText={viewerFrameText}
        viewerInputText={viewerInputText}
        viewerScrollContentStyle={viewerScrollContentStyle}
        viewerStateText={viewerStateText}
        viewerSurfaceStyle={viewerSurfaceStyle}
        viewerZoom={viewerZoom}
        onAcknowledgeFrame={acknowledgeFrame}
        onRemotePress={handleRemotePress}
        onViewerLayout={handleViewerLayout}
        onViewerSurfaceLayout={handleViewerSurfaceLayout}
      />

      <RemoteControlDeck
        handleRemoteKey={handleRemoteKey}
        handleSendText={handleSendText}
        isCompactRemoteLayout={isCompactRemoteLayout}
        remoteInputControlsDisabled={remoteInputControlsDisabled}
        remoteTextSendDisabled={remoteTextSendDisabled}
        setRemoteTextFocused={setRemoteTextFocused}
        setTextDraft={setTextDraft}
        setViewerZoom={setViewerZoom}
        textDraft={textDraft}
        viewerZoom={viewerZoom}
      />

      <RemoteFeedbackFooter
        canRetry={canRetry}
        error={error}
        footerText={footerText}
        inputError={inputError}
        inputFeedbackWarning={inputFeedbackWarning}
        isCompactRemoteLayout={isCompactRemoteLayout}
        remoteTextFocused={remoteTextFocused}
        transportWarning={transportWarning}
        onRetry={connect}
      />
      </ScrollView>
      </SafeAreaView>
    </KeyboardAvoidingView>
  );
}
