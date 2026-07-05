import {
  ActivityIndicator,
  Image,
  Pressable,
  ScrollView,
  Text,
  View,
  type GestureResponderEvent,
  type LayoutChangeEvent,
  type StyleProp,
  type ViewStyle,
} from "react-native";
import { Monitor } from "lucide-react-native";

import type { RemoteViewerZoom } from "./remoteScreenPresentation";
import { styles } from "./remoteScreenStyles";
import type { ScreenFrame } from "./remoteScreenTypes";

interface RemoteViewerSurfaceProps {
  frame: ScreenFrame | null;
  showLoading: boolean;
  online: boolean;
  isCompactRemoteLayout: boolean;
  remoteTextFocused: boolean;
  viewerZoom: RemoteViewerZoom;
  viewerScrollContentStyle: StyleProp<ViewStyle>;
  viewerSurfaceStyle: StyleProp<ViewStyle>;
  viewerAccessibilityLabel: string;
  viewerAccessibilityHint: string;
  viewerStateText: string;
  viewerInputText: string;
  remoteClickDisabled: boolean;
  grantUsable: boolean;
  transportDanger: boolean;
  viewerFrameText: string;
  onViewerLayout: (event: LayoutChangeEvent) => void;
  onViewerSurfaceLayout: (event: LayoutChangeEvent) => void;
  onRemotePress: (event: GestureResponderEvent) => void;
  onAcknowledgeFrame: (sequence: number) => void;
}

export function RemoteViewerSurface({
  frame,
  showLoading,
  online,
  isCompactRemoteLayout,
  remoteTextFocused,
  viewerZoom,
  viewerScrollContentStyle,
  viewerSurfaceStyle,
  viewerAccessibilityLabel,
  viewerAccessibilityHint,
  viewerStateText,
  viewerInputText,
  remoteClickDisabled,
  grantUsable,
  transportDanger,
  viewerFrameText,
  onViewerLayout,
  onViewerSurfaceLayout,
  onRemotePress,
  onAcknowledgeFrame,
}: RemoteViewerSurfaceProps) {
  return (
    <View onLayout={onViewerLayout} style={[styles.viewer, isCompactRemoteLayout && styles.viewerCompact, remoteTextFocused && styles.viewerTextFocused]}>
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
            onLayout={onViewerSurfaceLayout}
            onPress={onRemotePress}
            style={({ pressed }) => [
              styles.viewerSurface,
              viewerSurfaceStyle,
              transportDanger && styles.viewerSurfaceBlocked,
              pressed && styles.viewerSurfacePressed,
            ]}
          >
            {frame ? (
              <Image
                resizeMode="contain"
                source={{ uri: frame.image }}
                style={styles.screenImage}
                onLoadEnd={() => onAcknowledgeFrame(frame.sequence)}
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
  );
}
