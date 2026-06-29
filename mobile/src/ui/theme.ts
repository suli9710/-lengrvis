import type { TextStyle, ViewStyle } from "react-native";

export const colors = {
  canvas: "#f3f6f8",
  surface: "#ffffff",
  surfaceMuted: "#e8eef2",
  border: "#cbd6df",
  borderStrong: "#9aaaba",
  ink: "#17212b",
  inkMuted: "#556677",
  inkSubtle: "#748392",
  accent: "#1b5d7a",
  accentSoft: "#d9edf4",
  success: "#24724f",
  successSoft: "#dff2e8",
  warning: "#9a6700",
  warningSoft: "#fff4cf",
  danger: "#9a2f43",
  dangerSoft: "#fde3e8",
  remoteBg: "#111923",
  remotePanel: "#1a2632",
  remotePanelStrong: "#243545",
  remoteBorder: "#395163",
  remoteText: "#eef6f9",
  remoteMuted: "#9fb1bf",
  gold: "#f0c260",
};

export const radii = {
  sm: 6,
  md: 8,
};

export const spacing = {
  screenX: 20,
  bottomNav: 92,
};

export const shadows: { panel: ViewStyle } = {
  panel: {
    shadowColor: "#183247",
    shadowOpacity: 0.08,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 4 },
    elevation: 2,
  },
};

export const text = {
  kicker: {
    color: colors.inkSubtle,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0,
  } satisfies TextStyle,
  title: {
    color: colors.ink,
    fontSize: 28,
    fontWeight: "900",
    letterSpacing: 0,
  } satisfies TextStyle,
  body: {
    color: colors.inkMuted,
    fontSize: 14,
    lineHeight: 21,
  } satisfies TextStyle,
};
