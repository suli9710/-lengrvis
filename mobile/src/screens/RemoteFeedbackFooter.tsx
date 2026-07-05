import { Pressable, Text, View } from "react-native";
import { RefreshCcw } from "lucide-react-native";

import { styles } from "./remoteScreenStyles";

interface RemoteFeedbackFooterProps {
  transportWarning: string;
  error: string;
  inputError: string;
  inputFeedbackWarning: boolean;
  canRetry: boolean;
  footerText: string;
  isCompactRemoteLayout: boolean;
  remoteTextFocused: boolean;
  onRetry: () => void;
}

export function RemoteFeedbackFooter({
  transportWarning,
  error,
  inputError,
  inputFeedbackWarning,
  canRetry,
  footerText,
  isCompactRemoteLayout,
  remoteTextFocused,
  onRetry,
}: RemoteFeedbackFooterProps) {
  return (
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
          onPress={onRetry}
          style={({ pressed }) => [styles.retryButton, pressed && styles.pressed]}
        >
          <RefreshCcw size={16} color="#17222b" />
          <Text style={styles.retryButtonText}>重试屏幕查看</Text>
        </Pressable>
      ) : null}
      {isCompactRemoteLayout ? null : <Text style={styles.footerText}>{footerText}</Text>}
    </View>
  );
}
