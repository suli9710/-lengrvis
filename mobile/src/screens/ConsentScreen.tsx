/**
 * Mobile first-launch consent screen — Touchpoint 4.
 *
 * Full-screen page, not skippable. Shows EULA and privacy policy summaries.
 * The "Agree and continue" button is disabled until the user scrolls to bottom.
 * On agree, writes consent to SecureStore and calls onConsented().
 */

import { useCallback, useRef, useState } from "react";
import {
  Platform,
  Pressable,
  SafeAreaView,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { acceptConsent } from "../store/consent";

interface ConsentScreenProps {
  onConsented: () => void;
}

const EULA_SUMMARY: Array<{ title: string; detail: string }> = [
  { title: "\u8bb8\u53ef\u8303\u56f4", detail: "\u672c\u8f6f\u4ef6\u57fa\u4e8e BSL \u5f00\u6e90\u8bb8\u53ef\u53d1\u5e03\uff0c\u4ec5\u6388\u4e88\u4e2a\u4eba\u4f7f\u7528\u6743\u5229\u3002" },
  { title: "\u5546\u4e1a\u4f7f\u7528\u9650\u5236", detail: "\u672a\u7ecf\u5546\u4e1a\u6388\u6743\uff0c\u4e0d\u5f97\u7528\u4e8e\u5546\u4e1a\u76ee\u7684\u3002" },
  { title: "\u514d\u8d23\u58f0\u660e", detail: "\u672c\u8f6f\u4ef6\u4ee5\u201c\u73b0\u72b6\u201d\u63d0\u4f9b\uff0c\u4e0d\u627f\u62c5\u4efb\u4f55\u660e\u793a\u6216\u9690\u542b\u4fdd\u8bc1\u3002" },
  { title: "\u8d23\u4efb\u4e0a\u9650", detail: "\u56e0\u4f7f\u7528\u672c\u8f6f\u4ef6\u9020\u6210\u7684\u635f\u5931\uff0c\u8d23\u4efb\u4e0a\u9650\u4e3a\u00a5100\u3002" },
  { title: "\u7ba1\u8f96\u6743", detail: "\u4e89\u8bae\u9002\u7528\u4e2d\u56fd\u6cd5\u5f8b\uff0c\u7531\u5f00\u53d1\u8005\u6240\u5728\u5730\u7ba1\u8f96\u3002" },
];

const PRIVACY_SUMMARY: Array<{ title: string; detail: string }> = [
  { title: "\u672c\u5730\u5b58\u50a8", detail: "\u5bf9\u8bdd\u3001\u4efb\u52a1\u3001\u5ba1\u8ba1\u65e5\u5fd7\u5747\u5b58\u50a8\u5728\u60a8\u7684\u8bbe\u5907\u4e0a\uff0c\u4e0d\u4e0a\u4f20\u3002" },
  { title: "\u533f\u540d\u9065\u6d4b", detail: "\u9ed8\u8ba4\u5173\u95ed\uff0c\u4ec5\u5728\u60a8\u4e3b\u52a8\u5f00\u542f\u65f6\u53d1\u9001\uff0c\u4e14\u5b8c\u5168\u533f\u540d\u3002" },
  { title: "\u4e91\u7aef\u53ef\u9009", detail: "\u4ec5\u9009\u62e9\u4e91\u7aef LLM \u65f6\u5bf9\u8bdd\u5185\u5bb9\u624d\u53d1\u9001\u81f3\u670d\u52a1\u5546\u3002" },
  { title: "\u8fdc\u7a0b\u5ba1\u6279", detail: "\u79fb\u52a8\u7aef\u8fdc\u7a0b\u64cd\u4f5c\u987b\u7ecf\u684c\u9762\u7aef\u660e\u786e\u6279\u51c6\u3002" },
  { title: "\u968f\u65f6\u5220\u9664", detail: "\u53ef\u968f\u65f6\u5728\u8bbe\u7f6e\u4e2d\u4e00\u952e\u6e05\u9664\u6240\u6709\u672c\u5730\u6570\u636e\u3002" },
];

export function ConsentScreen({ onConsented }: ConsentScreenProps) {
  const [scrolledToBottom, setScrolledToBottom] = useState(false);
  const [agreeing, setAgreeing] = useState(false);
  const scrollViewRef = useRef<ScrollView>(null);

  const handleScroll = useCallback((event: { nativeEvent: { layoutMeasurement: { height: number }; contentOffset: { y: number }; contentSize: { height: number } } }) => {
    const { layoutMeasurement, contentOffset, contentSize } = event.nativeEvent;
    const padding = 32;
    if (layoutMeasurement.height + contentOffset.y >= contentSize.height - padding) {
      setScrolledToBottom(true);
    }
  }, []);

  const handleAgree = useCallback(async () => {
    if (agreeing) return;
    setAgreeing(true);
    try {
      await acceptConsent({ eula: true, privacy: true });
      onConsented();
    } catch {
      setAgreeing(false);
    }
  }, [agreeing, onConsented]);

  return (
    <SafeAreaView style={styles.safeArea} testID="consent-screen">
      <StatusBar barStyle="dark-content" backgroundColor="#f7f9fb" />
      <View style={styles.header}>
        <Text style={styles.headerTitle} accessibilityRole="header">
          {"\u4f7f\u7528\u6761\u6b3e\u4e0e\u9690\u79c1\u653f\u7b56"}
        </Text>
        <Text style={styles.headerSubtitle}>
          {"\u8bf7\u9605\u8bfb\u4ee5\u4e0b\u6761\u6b3e\u540e\u540c\u610f\u624d\u80fd\u7ee7\u7eed\u3002"}
        </Text>
      </View>
      <ScrollView
        ref={scrollViewRef}
        style={styles.scrollBody}
        onScroll={handleScroll}
        scrollEventThrottle={16}
        testID="consent-scroll-view"
      >
        <Text style={styles.sectionTitle}>{"\u6700\u7ec8\u7528\u6237\u8bb8\u53ef\u534f\u8bae\uff08EULA\uff09\u6458\u8981"}</Text>
        {EULA_SUMMARY.map((item, i) => (
          <View key={`eula-${i}`} style={styles.summaryItem}>
            <Text style={styles.summaryItemTitle}>{"\u2022 "}{item.title}</Text>
            <Text style={styles.summaryItemDetail}>{item.detail}</Text>
          </View>
        ))}
        <View style={styles.divider} />
        <Text style={styles.sectionTitle}>{"\u9690\u79c1\u653f\u7b56\u6458\u8981"}</Text>
        {PRIVACY_SUMMARY.map((item, i) => (
          <View key={`privacy-${i}`} style={styles.summaryItem}>
            <Text style={styles.summaryItemTitle}>{"\u2022 "}{item.title}</Text>
            <Text style={styles.summaryItemDetail}>{item.detail}</Text>
          </View>
        ))}
        <Text style={styles.scrollHint} accessibilityLiveRegion="polite">
          {scrolledToBottom ? "" : "\u8bf7\u6ed1\u52a8\u5230\u5e95\u90e8\u624d\u80fd\u540c\u610f\u3002"}
        </Text>
      </ScrollView>
      <View style={styles.footer}>
        <Pressable
          accessibilityLabel="\u540c\u610f\u5e76\u7ee7\u7eed"
          accessibilityRole="button"
          disabled={!scrolledToBottom || agreeing}
          hitSlop={8}
          onPress={handleAgree}
          style={({ pressed }) => [
            styles.agreeButton,
            (!scrolledToBottom || agreeing) && styles.agreeButtonDisabled,
            pressed && styles.pressed,
          ]}
          testID="consent-agree-button"
        >
          <Text style={[styles.agreeButtonText, (!scrolledToBottom || agreeing) && styles.agreeButtonTextDisabled]}>
            {agreeing ? "\u6b63\u5728\u5904\u7406..." : "\u540c\u610f\u5e76\u7ee7\u7eed"}
          </Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: "#f7f9fb",
  },
  header: {
    paddingTop: Platform.OS === "android" ? 8 : 4,
    paddingBottom: 12,
    paddingHorizontal: 24,
  },
  headerTitle: {
    color: "#17323a",
    fontSize: 24,
    fontWeight: "800",
  },
  headerSubtitle: {
    color: "#52616d",
    fontSize: 15,
    marginTop: 4,
  },
  scrollBody: {
    flex: 1,
    paddingHorizontal: 24,
  },
  sectionTitle: {
    color: "#17323a",
    fontSize: 18,
    fontWeight: "700",
    marginTop: 20,
    marginBottom: 8,
  },
  summaryItem: {
    marginBottom: 10,
  },
  summaryItemTitle: {
    color: "#17323a",
    fontSize: 15,
    fontWeight: "600",
  },
  summaryItemDetail: {
    color: "#52616d",
    fontSize: 14,
    lineHeight: 20,
    marginTop: 2,
    marginLeft: 12,
  },
  divider: {
    height: 1,
    backgroundColor: "#e0e6ea",
    marginVertical: 16,
  },
  scrollHint: {
    color: "#8c2f39",
    fontSize: 13,
    textAlign: "center",
    paddingVertical: 16,
  },
  footer: {
    padding: 16,
    paddingBottom: Platform.OS === "android" ? 16 : 32,
  },
  agreeButton: {
    minHeight: 52,
    borderRadius: 10,
    backgroundColor: "#0e5f76",
    alignItems: "center",
    justifyContent: "center",
  },
  agreeButtonDisabled: {
    backgroundColor: "#c5d6da",
  },
  agreeButtonText: {
    color: "#ffffff",
    fontSize: 16,
    fontWeight: "800",
  },
  agreeButtonTextDisabled: {
    color: "#8c9fa6",
  },
  pressed: {
    opacity: 0.72,
  },
});
