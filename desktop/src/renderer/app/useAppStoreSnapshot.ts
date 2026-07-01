import { useLengrvisStore } from "../store";

export function useAppStoreSnapshot() {
  const messages = useLengrvisStore((state) => state.messages);
  const setMessages = useLengrvisStore((state) => state.setMessages);
  const tasks = useLengrvisStore((state) => state.tasks);
  const setTasks = useLengrvisStore((state) => state.setTasks);
  const plan = useLengrvisStore((state) => state.plan);
  const setPlan = useLengrvisStore((state) => state.setPlan);
  const agentConversations = useLengrvisStore((state) => state.agentConversations);
  const setAgentConversations = useLengrvisStore((state) => state.setAgentConversations);
  const safetyReview = useLengrvisStore((state) => state.safetyReview);
  const setSafetyReview = useLengrvisStore((state) => state.setSafetyReview);
  const approvalRequests = useLengrvisStore((state) => state.approvalRequests);
  const setApprovalRequests = useLengrvisStore((state) => state.setApprovalRequests);
  const fileResults = useLengrvisStore((state) => state.fileResults);
  const setFileResults = useLengrvisStore((state) => state.setFileResults);
  const settings = useLengrvisStore((state) => state.settings);
  const setSettings = useLengrvisStore((state) => state.setSettings);
  const auditEntries = useLengrvisStore((state) => state.auditEntries);
  const setAuditEntries = useLengrvisStore((state) => state.setAuditEntries);
  const systemInfo = useLengrvisStore((state) => state.systemInfo);
  const setSystemInfo = useLengrvisStore((state) => state.setSystemInfo);
  const intentSuggestions = useLengrvisStore((state) => state.intentSuggestions);
  const setIntentSuggestions = useLengrvisStore((state) => state.setIntentSuggestions);
  const backendStatus = useLengrvisStore((state) => state.backendStatus);
  const setBackendStatus = useLengrvisStore((state) => state.setBackendStatus);
  const localLlmHealth = useLengrvisStore((state) => state.localLlmHealth);
  const setLocalLlmHealth = useLengrvisStore((state) => state.setLocalLlmHealth);
  const llmHealth = useLengrvisStore((state) => state.llmHealth);
  const setLlmHealth = useLengrvisStore((state) => state.setLlmHealth);
  const llmCostSummary = useLengrvisStore((state) => state.llmCostSummary);
  const setLlmCostSummary = useLengrvisStore((state) => state.setLlmCostSummary);
  const setContextUsage = useLengrvisStore((state) => state.setContextUsage);
  const isLoading = useLengrvisStore((state) => state.isLoading);
  const setIsLoading = useLengrvisStore((state) => state.setIsLoading);
  const isSearching = useLengrvisStore((state) => state.isSearching);
  const setIsSearching = useLengrvisStore((state) => state.setIsSearching);
  const isApprovalOpen = useLengrvisStore((state) => state.isApprovalOpen);
  const setIsApprovalOpen = useLengrvisStore((state) => state.setIsApprovalOpen);
  const approvalError = useLengrvisStore((state) => state.approvalError);
  const setApprovalError = useLengrvisStore((state) => state.setApprovalError);
  const mode = useLengrvisStore((state) => state.mode);
  const setMode = useLengrvisStore((state) => state.setMode);
  const activeView = useLengrvisStore((state) => state.activeView);
  const setActiveView = useLengrvisStore((state) => state.setActiveView);
  const focusedTaskId = useLengrvisStore((state) => state.focusedTaskId);
  const setFocusedTaskId = useLengrvisStore((state) => state.setFocusedTaskId);
  const browserSessions = useLengrvisStore((state) => state.browserSessions);
  const setBrowserSessions = useLengrvisStore((state) => state.setBrowserSessions);
  const browserEvents = useLengrvisStore((state) => state.browserEvents);
  const setBrowserEvents = useLengrvisStore((state) => state.setBrowserEvents);
  const browserHostSnapshot = useLengrvisStore((state) => state.browserHostSnapshot);
  const setBrowserHostSnapshot = useLengrvisStore((state) => state.setBrowserHostSnapshot);
  const activeBrowserSessionId = useLengrvisStore((state) => state.activeBrowserSessionId);
  const setActiveBrowserSessionId = useLengrvisStore((state) => state.setActiveBrowserSessionId);
  const browserError = useLengrvisStore((state) => state.browserError);
  const setBrowserError = useLengrvisStore((state) => state.setBrowserError);

  return {
    messages,
    setMessages,
    tasks,
    setTasks,
    plan,
    setPlan,
    agentConversations,
    setAgentConversations,
    safetyReview,
    setSafetyReview,
    approvalRequests,
    setApprovalRequests,
    fileResults,
    setFileResults,
    settings,
    setSettings,
    auditEntries,
    setAuditEntries,
    systemInfo,
    setSystemInfo,
    intentSuggestions,
    setIntentSuggestions,
    backendStatus,
    setBackendStatus,
    localLlmHealth,
    setLocalLlmHealth,
    llmHealth,
    setLlmHealth,
    llmCostSummary,
    setLlmCostSummary,
    setContextUsage,
    isLoading,
    setIsLoading,
    isSearching,
    setIsSearching,
    isApprovalOpen,
    setIsApprovalOpen,
    approvalError,
    setApprovalError,
    mode,
    setMode,
    activeView,
    setActiveView,
    focusedTaskId,
    setFocusedTaskId,
    browserSessions,
    setBrowserSessions,
    browserEvents,
    setBrowserEvents,
    browserHostSnapshot,
    setBrowserHostSnapshot,
    activeBrowserSessionId,
    setActiveBrowserSessionId,
    browserError,
    setBrowserError
  };
}
