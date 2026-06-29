// Android hardware/gesture back-button navigation.
//
// The app drives its own screen stack via React state (activeScreen +
// selectedApproval) instead of a navigation library, so the Android system back
// button is not wired to that state by default — pressing back would otherwise
// close the whole app from any inner screen. This pure resolver mirrors the
// in-app back buttons so App.tsx can map a hardware back press to the same
// transition. Kept dependency-free so it is unit-testable in the Node smokes.

export type ActiveScreen = "home" | "approvals" | "remote" | "wakeups";
export type CompanionTab = ActiveScreen;

export type RouterBackState =
  | { kind: "gate" }
  | { kind: "tab"; tab: CompanionTab }
  | { kind: "approvalDetail" };

export interface BackNavigationState {
  /** True once a pairing session is loaded (PairScreen / load screens excluded). */
  sessionActive: boolean;
  activeScreen?: ActiveScreen;
  hasSelectedApproval?: boolean;
  route?: RouterBackState;
}

export type BackNavigationAction =
  | "return_to_home"
  | "go_back"
  | "exit_app";

/**
 * Decide what the Android back button should do given the current navigation
 * state. The priority mirrors App.tsx's render order: the remote/wakeups
 * sub-screens take precedence over an open approval detail, which takes
 * precedence over the approvals list (where back falls through to the OS).
 */
export function resolveAndroidBack(state: BackNavigationState): BackNavigationAction {
  if (!state.sessionActive) {
    // Pairing / session-recovery screens use default OS back (exit).
    return "exit_app";
  }

  if (state.route) {
    if (state.route.kind === "approvalDetail") return "go_back";
    if (state.route.kind === "tab" && state.route.tab !== "home") return "return_to_home";
    return "exit_app";
  }

  if (state.hasSelectedApproval) {
    return "go_back";
  }
  if (state.activeScreen === "remote" || state.activeScreen === "wakeups" || state.activeScreen === "approvals") {
    return state.activeScreen === "approvals" ? "exit_app" : "return_to_home";
  }
  return "exit_app";
}

/** Whether the resolved action is handled in-app (vs. letting the OS exit). */
export function androidBackIsHandled(action: BackNavigationAction): boolean {
  return action !== "exit_app";
}
