import { create } from "zustand";

import {
  defaultSettings,
  disconnectedStatus,
  emptyPlan,
  emptySafetyReview,
  emptySystemInfo
} from "./defaults";
import type { AssistantMode, LengrvisStore, ViewKey } from "./types";

export { disconnectedStatus };

const viewKeys = new Set<ViewKey>([
  "home",
  "chat",
  "apps",
  "documents",
  "documentOcr",
  "papers",
  "courseware",
  "reports",
  "gallery",
  "imageOcr",
  "people",
  "places",
  "timeline",
  "files",
  "computer",
  "agents",
  "browser",
  "memories",
  "safety",
  "skills",
  "settings"
]);

function initialView(): ViewKey {
  if (typeof window === "undefined") return "home";
  const view = new URLSearchParams(window.location.search).get("view");
  return viewKeys.has(view as ViewKey) ? view as ViewKey : "home";
}

export const useLengrvisStore = create<LengrvisStore>((set) => ({
  backendStatus: disconnectedStatus,
  settings: defaultSettings,
  localLlmHealth: null,
  llmHealth: null,
  llmCostSummary: null,
  contextUsage: null,
  mode: "efficiency" as AssistantMode,
  isLoading: false,
  setBackendStatus: (backendStatus) => set({ backendStatus }),
  setSettings: (settings) => set({ settings }),
  setLocalLlmHealth: (localLlmHealth) => set({ localLlmHealth }),
  setLlmHealth: (llmHealth) => set({ llmHealth }),
  setLlmCostSummary: (llmCostSummary) => set({ llmCostSummary }),
  setContextUsage: (contextUsage) => set({ contextUsage }),
  setMode: (mode) => set({ mode }),
  setIsLoading: (isLoading) => set({ isLoading }),

  messages: [],
  tasks: [],
  plan: emptyPlan,
  agentConversations: [],
  fileResults: [],
  intentSuggestions: [],
  isSearching: false,
  focusedTaskId: null,
  setMessages: (messages) => set((state) => ({
    messages: typeof messages === "function" ? messages(state.messages) : messages
  })),
  setTasks: (tasks) => set((state) => ({
    tasks: typeof tasks === "function" ? tasks(state.tasks) : tasks
  })),
  setPlan: (plan) => set({ plan }),
  setAgentConversations: (agentConversations) => set((state) => ({
    agentConversations:
      typeof agentConversations === "function" ? agentConversations(state.agentConversations) : agentConversations
  })),
  setFileResults: (fileResults) => set({ fileResults }),
  setIntentSuggestions: (intentSuggestions) => set({ intentSuggestions }),
  setIsSearching: (isSearching) => set({ isSearching }),
  setFocusedTaskId: (focusedTaskId) => set({ focusedTaskId }),

  browserSessions: [],
  browserEvents: [],
  browserHostSnapshot: null,
  activeBrowserSessionId: null,
  browserError: null,
  setBrowserSessions: (browserSessions) => set((state) => ({
    browserSessions:
      typeof browserSessions === "function" ? browserSessions(state.browserSessions) : browserSessions
  })),
  setBrowserEvents: (browserEvents) => set((state) => ({
    browserEvents:
      typeof browserEvents === "function" ? browserEvents(state.browserEvents) : browserEvents
  })),
  setBrowserHostSnapshot: (browserHostSnapshot) => set({ browserHostSnapshot }),
  setActiveBrowserSessionId: (activeBrowserSessionId) => set({ activeBrowserSessionId }),
  setBrowserError: (browserError) => set({ browserError }),

  activeView: initialView(),
  setActiveView: (activeView) => set({ activeView }),

  safetyReview: emptySafetyReview,
  approvalRequests: [],
  auditEntries: [],
  systemInfo: emptySystemInfo,
  isApprovalOpen: false,
  approvalError: null,
  setSafetyReview: (safetyReview) => set({ safetyReview }),
  setApprovalRequests: (approvalRequests) => set((state) => ({
    approvalRequests:
      typeof approvalRequests === "function" ? approvalRequests(state.approvalRequests) : approvalRequests
  })),
  setAuditEntries: (auditEntries) => set((state) => ({
    auditEntries: typeof auditEntries === "function" ? auditEntries(state.auditEntries) : auditEntries
  })),
  setSystemInfo: (systemInfo) => set({ systemInfo }),
  setIsApprovalOpen: (isApprovalOpen) => set({ isApprovalOpen }),
  setApprovalError: (approvalError) => set({ approvalError })
}));
