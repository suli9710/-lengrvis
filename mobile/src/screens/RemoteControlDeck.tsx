import { Pressable, Text, TextInput, View } from "react-native";
import { ChevronsDown, ChevronsUp, CornerDownLeft, Delete, Keyboard, Send } from "lucide-react-native";

import {
  REMOTE_KEY_CONTROLS,
  REMOTE_TEXT_INPUT_MAX_LENGTH,
  REMOTE_VIEWER_ZOOM_OPTIONS,
  type RemoteViewerZoom,
} from "./remoteScreenPresentation";
import { styles } from "./remoteScreenStyles";

interface RemoteControlDeckProps {
  handleRemoteKey: (key: string) => void;
  handleSendText: () => void;
  isCompactRemoteLayout: boolean;
  remoteInputControlsDisabled: boolean;
  remoteTextSendDisabled: boolean;
  setRemoteTextFocused: (focused: boolean) => void;
  setTextDraft: (value: string) => void;
  setViewerZoom: (zoom: RemoteViewerZoom) => void;
  textDraft: string;
  viewerZoom: RemoteViewerZoom;
}

export function RemoteControlDeck({
  handleRemoteKey,
  handleSendText,
  isCompactRemoteLayout,
  remoteInputControlsDisabled,
  remoteTextSendDisabled,
  setRemoteTextFocused,
  setTextDraft,
  setViewerZoom,
  textDraft,
  viewerZoom,
}: RemoteControlDeckProps) {
  return (
    <View style={[styles.controlDeck, isCompactRemoteLayout && styles.controlDeckCompact]} testID="remote-control-deck">
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
