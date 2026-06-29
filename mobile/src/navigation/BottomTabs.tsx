import { usePathname, useRouter } from "expo-router";
import { ClipboardCheck, Home, Monitor, Timer } from "lucide-react-native";
import { Pressable, StyleSheet, Text, View } from "react-native";

import type { CompanionTab } from "./types";
import { colors, radii } from "../ui/theme";

const TABS: Array<{
  id: CompanionTab;
  label: string;
  href: "/home" | "/approvals" | "/remote" | "/wakeups";
  testID: string;
  icon: typeof Home;
}> = [
  { id: "home", label: "首页", href: "/home", testID: "tab-home", icon: Home },
  { id: "approvals", label: "审批", href: "/approvals", testID: "tab-approvals", icon: ClipboardCheck },
  { id: "remote", label: "远控", href: "/remote", testID: "tab-remote", icon: Monitor },
  { id: "wakeups", label: "唤醒", href: "/wakeups", testID: "tab-wakeups", icon: Timer },
];

export function BottomTabs() {
  const pathname = usePathname();
  const router = useRouter();
  const activeTab = currentTab(pathname);
  return (
    <View style={styles.wrap}>
      <View style={styles.bar}>
        {TABS.map((tab) => {
          const selected = tab.id === activeTab;
          const Icon = tab.icon;
          return (
            <Pressable
              key={tab.id}
              accessibilityLabel={tab.label}
              accessibilityRole="tab"
              accessibilityState={{ selected }}
              hitSlop={6}
              onPress={() => router.replace(tab.href)}
              style={({ pressed }) => [styles.tab, selected && styles.tabSelected, pressed && styles.pressed]}
              testID={tab.testID}
            >
              <Icon size={20} color={selected ? colors.remoteText : colors.inkSubtle} />
              <Text style={[styles.label, selected && styles.labelSelected]}>{tab.label}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

function currentTab(pathname: string): CompanionTab {
  if (pathname.startsWith("/approvals")) return "approvals";
  if (pathname.startsWith("/remote")) return "remote";
  if (pathname.startsWith("/wakeups")) return "wakeups";
  return "home";
}

const styles = StyleSheet.create({
  wrap: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    paddingHorizontal: 14,
    paddingTop: 8,
    paddingBottom: 18,
    backgroundColor: "rgba(243,246,248,0.96)",
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  bar: {
    minHeight: 62,
    borderRadius: radii.md,
    backgroundColor: colors.remoteBg,
    flexDirection: "row",
    alignItems: "center",
    padding: 5,
    gap: 4,
  },
  tab: {
    flex: 1,
    minWidth: 0,
    minHeight: 52,
    borderRadius: radii.md,
    alignItems: "center",
    justifyContent: "center",
    gap: 3,
  },
  tabSelected: {
    backgroundColor: colors.remotePanelStrong,
  },
  label: {
    color: colors.inkSubtle,
    fontSize: 11,
    fontWeight: "900",
  },
  labelSelected: {
    color: colors.remoteText,
  },
  pressed: {
    opacity: 0.72,
  },
});
