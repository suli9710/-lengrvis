import { Pressable, Text, View } from "react-native";
import { MousePointer2, ShieldCheck, TriangleAlert, Wifi, WifiOff, XCircle } from "lucide-react-native";

import { statusText, type ConnectionState, type InputConnectionState, type TransportNotice } from "./remoteScreenPresentation";
import { styles } from "./remoteScreenStyles";

interface RemoteStatusPanelsProps {
  online: boolean;
  connection: ConnectionState;
  statusMetaText: string;
  reconnectStatusText: string;
  transportNotice: TransportNotice;
  inputConnection: InputConnectionState;
  inputConnectionText: string;
  grantUsable: boolean;
  transportBlocked: boolean;
  remoteModeText: string;
  grantStatusMeta: string;
  isCompactRemoteLayout: boolean;
  isRevokingInput: boolean;
  onConnectInput: () => void;
  onEndInputControl: () => void;
}

export function RemoteStatusPanels({
  online,
  connection,
  statusMetaText,
  reconnectStatusText,
  transportNotice,
  inputConnection,
  inputConnectionText,
  grantUsable,
  transportBlocked,
  remoteModeText,
  grantStatusMeta,
  isCompactRemoteLayout,
  isRevokingInput,
  onConnectInput,
  onEndInputControl,
}: RemoteStatusPanelsProps) {
  return (
    <>
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
            onPress={onConnectInput}
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
            onPress={onEndInputControl}
            style={({ pressed }) => [styles.endGrantButton, (pressed || isRevokingInput) && styles.pressed]}
          >
            <XCircle size={15} color="#ffd1d6" />
            <Text style={styles.endGrantText}>{isRevokingInput ? "结束中" : "结束接管"}</Text>
          </Pressable>
        ) : null}
      </View>
    </>
  );
}
