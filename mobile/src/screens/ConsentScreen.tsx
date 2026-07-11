/**
 * Mobile first-launch consent screen — Touchpoint 4.
 *
 * Full-screen page, not skippable. It distinguishes the two draft documents,
 * requires separate acknowledgement, and stores their versions and times.
 */

import { useCallback, useRef, useState } from "react";
import {
  Linking,
  Platform,
  Pressable,
  SafeAreaView,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  View,
} from "react-native";

import {
  MOBILE_LEGAL_DOCUMENT_URLS,
  mobileLegalDocumentLabel,
  type MobileLegalDocument,
} from "../legalDocuments";
import { acceptConsent, MOBILE_LEGAL_VERSIONS } from "../store/consent";

interface ConsentScreenProps {
  onConsented: () => void;
}

const EULA_SUMMARY: Array<{ title: string; detail: string }> = [
  { title: "许可证边界", detail: "源码采用 BUSL-1.1，而不是开源许可证。当前生产使用仅限个人、非商业、教育或非营利研究；商业使用需要另行获得许可。" },
  { title: "自动化风险", detail: "软件按“现状”提供。自动化操作和 AI 生成内容可能出错，不应作为唯一决策依据。" },
  { title: "责任与适用法", detail: "草案规定的责任限制为人民币 100 元或实际支付金额中的较小者；法律不得排除的责任不受影响，并以中华人民共和国法律为准。" },
  { title: "草案状态", detail: "本 EULA 仍是发布候选草案，尚未完成律师定稿；本页不构成商业发布或合规承诺。" },
];

const PRIVACY_SUMMARY: Array<{ title: string; detail: string }> = [
  { title: "本地与云端", detail: "桌面上的对话、任务和审计数据通常保存在本机。只有你主动选择云端模型时，必要的提示词、上下文和附件才可能发送给所选服务商。" },
  { title: "后台审批提醒", detail: "启用通知后，手机会把 Expo 推送令牌登记到已配对电脑。电脑只向 Expo 发送通用提醒和审批路由标识以唤醒手机，不发送审批正文、路径或会话令牌。" },
  { title: "遥测与诊断", detail: "当前版本不默认向外发送遥测或崩溃报告。诊断包只在你手动导出并自行分享时才会离开设备。" },
  { title: "删除边界", detail: "断开配对会清除手机的配对会话和本地证书信任。桌面数据删除需要在桌面端单独确认，审计链和日志可能仍需按说明处理。" },
  { title: "草案状态", detail: "本隐私政策仍是发布候选草案，尚未完成律师定稿；它不表示已经满足任何特定隐私法规。" },
];

export function ConsentScreen({ onConsented }: ConsentScreenProps) {
  const [scrolledToBottom, setScrolledToBottom] = useState(false);
  const [eulaAccepted, setEulaAccepted] = useState(false);
  const [privacyAccepted, setPrivacyAccepted] = useState(false);
  const [agreeing, setAgreeing] = useState(false);
  const [legalOpenError, setLegalOpenError] = useState("");
  const [saveError, setSaveError] = useState("");
  const scrollViewRef = useRef<ScrollView>(null);

  const handleScroll = useCallback((event: { nativeEvent: { layoutMeasurement: { height: number }; contentOffset: { y: number }; contentSize: { height: number } } }) => {
    const { layoutMeasurement, contentOffset, contentSize } = event.nativeEvent;
    const padding = 32;
    if (layoutMeasurement.height + contentOffset.y >= contentSize.height - padding) {
      setScrolledToBottom(true);
    }
  }, []);

  const handleAgree = useCallback(async () => {
    if (agreeing || !scrolledToBottom || !eulaAccepted || !privacyAccepted) return;
    setAgreeing(true);
    setSaveError("");
    try {
      await acceptConsent({ eula: true, privacy: true });
      onConsented();
    } catch {
      setAgreeing(false);
      setSaveError("无法安全保存同意记录。为保护你的选择，应用将停留在此页面，请重试。");
    }
  }, [agreeing, eulaAccepted, onConsented, privacyAccepted, scrolledToBottom]);

  const handleOpenLegalDocument = useCallback((document: MobileLegalDocument) => {
    setLegalOpenError("");
    void Linking.openURL(MOBILE_LEGAL_DOCUMENT_URLS[document]).catch(() => {
      setLegalOpenError("无法打开完整候选文本。请检查网络连接后重试；未读完整文本前请勿继续确认。");
    });
  }, []);

  const canAgree = scrolledToBottom && eulaAccepted && privacyAccepted && !agreeing;

  return (
    <SafeAreaView style={styles.safeArea} testID="consent-screen">
      <StatusBar barStyle="dark-content" backgroundColor="#f7f9fb" />
      <View style={styles.header}>
        <Text style={styles.headerTitle} accessibilityRole="header">
          使用条款与隐私政策
        </Text>
        <Text style={styles.headerSubtitle}>
          请分别阅读并确认两份候选草案；它们尚未完成法务定稿。
        </Text>
      </View>
      <ScrollView
        ref={scrollViewRef}
        style={styles.scrollBody}
        onScroll={handleScroll}
        scrollEventThrottle={16}
        testID="consent-scroll-view"
      >
        <Text style={styles.draftNotice} accessibilityRole="alert">
          当前显示 EULA {MOBILE_LEGAL_VERSIONS.eula} 与隐私政策 {MOBILE_LEGAL_VERSIONS.privacy} 的要点。正式发布前仍需法务审阅；下方链接可打开与本版本绑定的完整候选文本。
        </Text>
        {(["eula", "privacy"] as const).map((document) => (
          <Pressable
            accessibilityHint="在系统浏览器中打开版本绑定的完整候选文本"
            accessibilityLabel={mobileLegalDocumentLabel(document)}
            accessibilityRole="link"
            key={document}
            onPress={() => handleOpenLegalDocument(document)}
            style={({ pressed }) => [styles.legalLink, pressed && styles.pressed]}
            testID={`consent-${document}-full-text-link`}
          >
            <Text style={styles.legalLinkText}>{mobileLegalDocumentLabel(document)}</Text>
          </Pressable>
        ))}
        {legalOpenError ? (
          <Text accessibilityLiveRegion="assertive" style={styles.saveError}>{legalOpenError}</Text>
        ) : null}
        <Text style={styles.sectionTitle}>最终用户许可协议（EULA）摘要</Text>
        {EULA_SUMMARY.map((item, i) => (
          <View key={`eula-${i}`} style={styles.summaryItem}>
            <Text style={styles.summaryItemTitle}>• {item.title}</Text>
            <Text style={styles.summaryItemDetail}>{item.detail}</Text>
          </View>
        ))}
        <View style={styles.divider} />
        <Text style={styles.sectionTitle}>隐私政策摘要</Text>
        {PRIVACY_SUMMARY.map((item, i) => (
          <View key={`privacy-${i}`} style={styles.summaryItem}>
            <Text style={styles.summaryItemTitle}>• {item.title}</Text>
            <Text style={styles.summaryItemDetail}>{item.detail}</Text>
          </View>
        ))}
        <Text style={styles.scrollHint} accessibilityLiveRegion="polite">
          {scrolledToBottom ? "请分别勾选两份文件后继续。" : "请滑动到此页底部后再确认。"}
        </Text>
      </ScrollView>
      <View style={styles.footer}>
        <Pressable
          accessibilityHint="确认已阅读 EULA 候选草案"
          accessibilityLabel={`我已阅读并同意最终用户许可协议候选草案 ${MOBILE_LEGAL_VERSIONS.eula}`}
          accessibilityRole="checkbox"
          accessibilityState={{ checked: eulaAccepted }}
          hitSlop={8}
          onPress={() => setEulaAccepted((accepted) => !accepted)}
          style={({ pressed }) => [styles.consentChoice, pressed && styles.pressed]}
          testID="consent-eula-checkbox"
        >
          <View style={[styles.consentBox, eulaAccepted && styles.consentBoxChecked]}>
            <Text style={styles.consentBoxMark}>{eulaAccepted ? "✓" : ""}</Text>
          </View>
          <Text style={styles.consentChoiceText}>
            我已阅读并同意《最终用户许可协议》候选草案（{MOBILE_LEGAL_VERSIONS.eula}）
          </Text>
        </Pressable>
        <Pressable
          accessibilityHint="确认已阅读隐私政策候选草案"
          accessibilityLabel={`我已阅读并同意隐私政策候选草案 ${MOBILE_LEGAL_VERSIONS.privacy}`}
          accessibilityRole="checkbox"
          accessibilityState={{ checked: privacyAccepted }}
          hitSlop={8}
          onPress={() => setPrivacyAccepted((accepted) => !accepted)}
          style={({ pressed }) => [styles.consentChoice, pressed && styles.pressed]}
          testID="consent-privacy-checkbox"
        >
          <View style={[styles.consentBox, privacyAccepted && styles.consentBoxChecked]}>
            <Text style={styles.consentBoxMark}>{privacyAccepted ? "✓" : ""}</Text>
          </View>
          <Text style={styles.consentChoiceText}>
            我已阅读并同意《隐私政策》候选草案（{MOBILE_LEGAL_VERSIONS.privacy}）
          </Text>
        </Pressable>
        {saveError ? <Text accessibilityLiveRegion="assertive" style={styles.saveError}>{saveError}</Text> : null}
        <Pressable
          accessibilityLabel="\u540c\u610f\u5e76\u7ee7\u7eed"
          accessibilityRole="button"
          disabled={!canAgree}
          hitSlop={8}
          onPress={handleAgree}
          style={({ pressed }) => [
            styles.agreeButton,
            !canAgree && styles.agreeButtonDisabled,
            pressed && styles.pressed,
          ]}
          testID="consent-agree-button"
        >
          <Text style={[styles.agreeButtonText, !canAgree && styles.agreeButtonTextDisabled]}>
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
  draftNotice: {
    color: "#6b4f1d",
    backgroundColor: "#fff4d6",
    borderColor: "#e2bf70",
    borderWidth: 1,
    borderRadius: 8,
    fontSize: 13,
    lineHeight: 19,
    marginTop: 16,
    padding: 12,
  },
  legalLink: {
    alignSelf: "flex-start",
    marginTop: 12,
    minHeight: 32,
    justifyContent: "center",
  },
  legalLinkText: {
    color: "#0e5f76",
    fontSize: 14,
    fontWeight: "700",
    textDecorationLine: "underline",
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
  consentChoice: {
    alignItems: "flex-start",
    flexDirection: "row",
    gap: 10,
    marginBottom: 12,
  },
  consentBox: {
    alignItems: "center",
    borderColor: "#70828a",
    borderRadius: 4,
    borderWidth: 1,
    height: 22,
    justifyContent: "center",
    marginTop: 1,
    width: 22,
  },
  consentBoxChecked: {
    backgroundColor: "#0e5f76",
    borderColor: "#0e5f76",
  },
  consentBoxMark: {
    color: "#ffffff",
    fontSize: 15,
    fontWeight: "900",
    lineHeight: 18,
  },
  consentChoiceText: {
    color: "#17323a",
    flex: 1,
    fontSize: 14,
    lineHeight: 20,
  },
  saveError: {
    color: "#8c2f39",
    fontSize: 13,
    lineHeight: 19,
    marginBottom: 12,
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
