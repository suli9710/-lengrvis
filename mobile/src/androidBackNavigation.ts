// Android hardware/gesture back-button navigation.
//
// The app drives its own screen stack via React state (activeScreen +
// selectedApproval) instead of a navigation library, so the Android system back
// button is not wired to that state by default — pressing back would otherwise
// close the whole app from any inner screen. This pure resolver mirrors the
// in-app back buttons so App.tsx can map a hardware back press to the same
// transition. Kept dependency-free so it is unit-testable in the Node smokes.

export type ActiveScreen = "approvals" | "remote" | "wakeups";

export interface BackNavigationState {
  /** True once a pairing session is loaded (PairScreen / load screens excluded). */
  sessionActive: boolean;
  activeScreen: ActiveScreen;
  hasSelectedApproval: boolean;
}

export type BackNavigationAction =
  | "return_to_approvals"
  | "close_approval_detail"
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
  if (state.activeScreen === "remote" || state.activeScreen === "wakeups") {
    return "return_to_approvals";
  }
  if (state.hasSelectedApproval) {
    return "close_approval_detail";
  }
  return "exit_app";
}

/** Whether the resolved action is handled in-app (vs. letting the OS exit). */
export function androidBackIsHandled(action: BackNavigationAction): boolean {
  return action !== "exit_app";
}
