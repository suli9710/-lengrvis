export type CompanionTab = "home" | "approvals" | "remote" | "wakeups";

export type MobileRoute =
  | { kind: "gate" }
  | { kind: "tab"; tab: CompanionTab }
  | { kind: "approvalDetail"; approvalId: string };

export type UiTone = "neutral" | "success" | "warning" | "danger" | "accent" | "remote";

export interface HomeSnapshot {
  connectionLabel: string;
  pendingApprovals: number;
  activeTasks: number;
  remoteInputLabel: string;
  nextStep: string;
}
