import { create } from "zustand";

import {
  sampleAgentConversations,
  sampleApprovalRequests,
  sampleAuditLogs,
  sampleChatMessages,
  sampleFileResults,
  samplePlan,
  sampleSafetyReview,
  sampleSettings,
  sampleSystemInfo,
  sampleTaskTimeline
} from "../data/mockData";
import type { BackendStatus } from "../../shared/types";
import type { AssistantMode, MavrisStore, ViewKey } from "./types";

const viewKeys = new Set<ViewKey>(["home", "chat", "files", "computer", "agents", "browser", "memories", "safety", "settings"]);

function initialView(): ViewKey {
  if (typeof window === "undefined") return "home";
  const view = new URLSearchParams(window.location.search).get("view");
  return viewKeys.has(view as ViewKey) ? view as ViewKey : "home";
}

export const disconnectedStatus: BackendStatus = {
  state: "not_configured",
  baseUrl: sampleSettings.apiBaseUrl,
  message: "等待后端连接",
  lastCheckedAt: new Date().toISOString(),
  health: {
    ok: false
  }
};

export const useMavrisStore = create<MavrisStore>((set) => ({
  backendStatus: disconnectedStatus,
  settings: sampleSettings,
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

  messages: sampleChatMessages,
  tasks: sampleTaskTimeline,
  plan: samplePlan,
  agentConversations: sampleAgentConversations,
  fileResults: sampleFileResults,
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

  safetyReview: sampleSafetyReview,
  approvalRequests: sampleApprovalRequests,
  auditEntries: sampleAuditLogs,
  systemInfo: sampleSystemInfo,
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
