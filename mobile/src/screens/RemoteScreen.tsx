import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Pressable,
  SafeAreaView,
  StatusBar,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { ArrowLeft, Monitor, Pause, Play, Wifi, WifiOff } from "lucide-react-native";

import { remoteScreenWebSocketUrl, type PairingSession, type RemoteScreenEvent } from "../api/client";
import { shortDate } from "../format";

type ConnectionState = "offline" | "connecting" | "online" | "paused";

interface ScreenFrame {
  image: string;
  timestamp: string;
  width: number;
  height: number;
  originalWidth: number;
  originalHeight: number;
}

export function RemoteScreen({
  session,
  onBack,
  onSessionExpired,
}: {
  session: PairingSession;
  onBack: () => void;
  onSessionExpired: () => void;
}) {
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [frame, setFrame] = useState<ScreenFrame | null>(null);
  const [streamMeta, setStreamMeta] = useState({ fps: 0, quality: 0 });
  const [error, setError] = useState("");
  const socketRef = useRef<WebSocket | null>(null);

  const closeSocket = useCallback(() => {
    socketRef.current?.close();
    socketRef.current = null;
  }, []);

  const connect = useCallback(() => {
    closeSocket();
    setConnection("connecting");
    setError("");

    const socket = new WebSocket(remoteScreenWebSocketUrl(session));
    socketRef.current = socket;

    socket.onopen = () => {
      setConnection("online");
      socket.send(JSON.stringify({ fps: 2, quality: 55 }));
    };

    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(String(event.data)) as RemoteScreenEvent;
        if (payload.type === "connected") {
          setStreamMeta({ fps: payload.fps, quality: payload.quality });
          return;
        }
        if (payload.type === "frame") {
          setFrame({
            image: payload.image,
            timestamp: payload.timestamp,
            width: payload.width,
            height: payload.height,
            originalWidth: payload.original_width,
            originalHeight: payload.original_height,
          });
          return;
        }
        if (payload.type === "error") {
          setError(payload.message);
        }
      } catch {
        setError("Received an unreadable screen stream event.");
      }
    };

    socket.onerror = () => {
      setError("Remote desktop stream failed. Check LAN access and remote desktop settings.");
    };

    socket.onclose = (event) => {
      if (event.code === 1008) {
        onSessionExpired();
        return;
      }
      setConnection((current) => (current === "paused" ? current : "offline"));
    };
  }, [closeSocket, onSessionExpired, session]);

  useEffect(() => {
    connect();
    return closeSocket;
  }, [closeSocket, connect]);

  const handleToggleStream = () => {
    if (connection === "online" || connection === "connecting") {
      setConnection("paused");
      closeSocket();
      return;
    }
    connect();
  };

  const online = connection === "online";
  const showLoading = connection === "connecting" && !frame;
  const aspectRatio = frame && frame.width > 0 && frame.height > 0 ? frame.width / frame.height : 16 / 9;

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar barStyle="light-content" backgroundColor="#17222b" />
      <View style={styles.header}>
        <Pressable onPress={onBack} style={({ pressed }) => [styles.iconButton, pressed && styles.pressed]}>
          <ArrowLeft size={20} color="#f7faf8" />
        </Pressable>
        <View style={styles.headerText}>
          <Text style={styles.kicker}>Remote desktop</Text>
          <Text style={styles.headerTitle}>Live screen</Text>
        </View>
        <Pressable onPress={handleToggleStream} style={({ pressed }) => [styles.iconButton, pressed && styles.pressed]}>
          {online || connection === "connecting" ? <Pause size={20} color="#f7faf8" /> : <Play size={20} color="#f7faf8" />}
        </Pressable>
      </View>

      <View style={styles.statusRow}>
        {online ? <Wifi size={16} color="#75d39a" /> : <WifiOff size={16} color="#ffcf72" />}
        <Text style={styles.statusText}>{statusText(connection)}</Text>
        {streamMeta.fps ? <Text style={styles.statusMeta}>{streamMeta.fps} fps</Text> : null}
      </View>

      <View style={styles.viewer}>
        {frame ? (
          <Image resizeMode="contain" source={{ uri: frame.image }} style={[styles.screenImage, { aspectRatio }]} />
        ) : (
          <View style={styles.emptyFrame}>
            {showLoading ? <ActivityIndicator color="#75d39a" /> : <Monitor size={42} color="#93a2ad" />}
            <Text style={styles.emptyTitle}>{showLoading ? "Connecting to desktop" : "No screen frame yet"}</Text>
            <Text style={styles.emptyText}>Frames appear here as soon as the paired backend starts streaming.</Text>
          </View>
        )}
      </View>

      <View style={styles.footer}>
        {error ? <Text style={styles.errorText}>{error}</Text> : null}
        <Text style={styles.footerText}>
          {frame
            ? `${frame.originalWidth}x${frame.originalHeight} desktop, last frame ${shortDate(frame.timestamp)}`
            : "View-only session. Remote input still requires approval."}
        </Text>
      </View>
    </SafeAreaView>
  );
}

function statusText(connection: ConnectionState): string {
  if (connection === "online") return "Streaming over paired JWT";
  if (connection === "connecting") return "Connecting";
  if (connection === "paused") return "Paused";
  return "Disconnected";
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
  pressed: {
    opacity: 0.72,
  },
});
