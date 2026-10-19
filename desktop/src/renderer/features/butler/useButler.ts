import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiResponse, DesktopRunMode } from "../../../shared/desktopBridgeTypes";
import type {
  AutonomyScope, AutonomyScopeInput, ButlerControlAction, ButlerMatter, ButlerMatterControlAction,
  ButlerMessageResult, ButlerOverview, ButlerResponseInput, ButlerArrangementInput
} from "../../../shared/butlerTypes";
import type { LengrvisApiClient } from "../../lib/apiClient";
import { readableError } from "../../appViewModel";

export function useButler(api: LengrvisApiClient, mode: DesktopRunMode, connected: boolean) {
  // Older desktop bridges and test doubles retain the original chat path.
  const available = typeof api.supportsButler === "function" && api.supportsButler();
  const [overview, setOverview] = useState<ButlerOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedMatterId, setSelectedMatterId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const mutationInFlight = useRef(false);
  const readSequence = useRef(0);
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    if (!available) return;
    const sequence = ++readSequence.current;
    try {
      const result = await api.getButlerOverview();
      if (!mounted.current || sequence !== readSequence.current) return;
      if (result.ok && result.data) {
        setOverview(result.data);
        setError(null);
      } else {
        setError(result.error?.message ?? "无法读取管家进度。");
      }
    } catch (reason) {
      if (mounted.current && sequence === readSequence.current) setError(readableError(reason, "无法读取管家进度。"));
    }
  }, [api, available]);

  useEffect(() => {
    mounted.current = true;
    if (!connected || !available) return;
    void refresh();
    const interval = globalThis.setInterval(() => {
      if (typeof document === "undefined" || document.visibilityState !== "hidden") void refresh();
    }, 5_000);
    const onVisible = () => { if (document.visibilityState !== "hidden") void refresh(); };
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", onVisible);
    return () => {
      mounted.current = false;
      ++readSequence.current;
      globalThis.clearInterval(interval);
      if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onVisible);
    };
  }, [available, connected, refresh]);

  const acceptMatter = useCallback((matter?: ButlerMatter) => {
    if (!matter) return;
    setOverview((current) => {
      if (!current || current.matters.some((item) => item.id === matter.id && item.version > matter.version)) return current;
      return { ...current, matters: [matter, ...current.matters.filter((item) => item.id !== matter.id)] };
    });
    setSelectedMatterId(matter.id);
  }, []);

  const perform = useCallback(async <T,>(operation: () => Promise<ApiResponse<T>>): Promise<ApiResponse<T>> => {
    if (mutationInFlight.current) return {
      ok: false, status: 409, error: { code: "BUTLER_BUSY", message: "上一项操作正在处理，请稍候。" }, receivedAt: new Date().toISOString()
    };
    mutationInFlight.current = true;
    setBusy(true);
    setError(null);
    ++readSequence.current;
    try {
      const result = await operation();
      if (!result.ok) setError(result.error?.message ?? "操作未完成，请刷新查看当前状态。");
      await refresh();
      // A refresh may succeed after a rejected action; retain the actionable error.
      if (!result.ok) setError(result.error?.message ?? "操作未完成，请刷新查看当前状态。");
      return result;
    } catch (reason) {
      const message = readableError(reason, "操作未完成，请刷新查看当前状态。");
      setError(message);
      return { ok: false, status: 0, error: { message }, receivedAt: new Date().toISOString() };
    } finally {
      mutationInFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  }, [refresh]);

  const selectedMatter = overview?.matters.find((matter) => matter.id === selectedMatterId) ?? null;

  const submit = useCallback(async (message: string): Promise<ApiResponse<ButlerMessageResult> | null> => {
    if (!available) return null;
    if (selectedMatterId && !selectedMatter) return {
      ok: false, status: 409, error: { message: "所选事项已不可用，请先选择其他事项或新建事项。" }, receivedAt: new Date().toISOString()
    };
    const interaction = selectedMatter?.interaction;
    const newRequest = /^(?:另外|另一个任务|新任务|还有一件|顺便|先帮我|先处理|another task|new task)\s*[:：]?/i.test(message.trim());
    const controlMessage = new Set(["暂停", "继续", "恢复", "取消", "停止", "暂停管家", "停止主动管理", "恢复管家", "继续主动管理", "关闭自动记忆", "停止自动记忆", "开启自动记忆", "恢复自动记忆"]).has(message.trim());
    if (interaction?.kind === "approval" && !controlMessage && !newRequest) return {
      ok: false, status: 409, error: { message: "这件事项正在等待具体操作确认，请打开审批卡处理。" }, receivedAt: new Date().toISOString()
    };
    if ((interaction?.kind === "choose_matter" || interaction?.kind === "choose_dependency") && !controlMessage && !newRequest) return {
      ok: false, status: 409, error: { message: "请先在事项卡中选择这句话对应的事项。" }, receivedAt: new Date().toISOString()
    };
    const result = await perform(() => selectedMatter && interaction && !controlMessage && !newRequest
      ? api.respondButlerMatter(selectedMatter.id, { version: selectedMatter.version, interaction_id: interaction.id, message })
      : api.sendButlerMessage({ message, mode, ...(selectedMatterId ? { matter_id: selectedMatterId } : {}) }));
    if (result.ok) acceptMatter(result.data?.matter);
    return result;
  }, [acceptMatter, api, available, mode, perform, selectedMatter, selectedMatterId]);

  const respond = useCallback(async (matter: ButlerMatter, input: Omit<ButlerResponseInput, "version" | "interaction_id">) => {
    if (!matter.interaction) return false;
    const result = await perform(() => api.respondButlerMatter(matter.id, {
      ...input, version: matter.version, interaction_id: matter.interaction!.id
    }));
    if (result.ok) acceptMatter(result.data?.matter);
    return result.ok;
  }, [acceptMatter, api, perform]);

  return {
    available, overview, error, busy, refresh, selectedMatter, selectedMatterId, selectMatter: setSelectedMatterId, submit, respond,
    openArtifact: (matter: ButlerMatter, artifactId: string) => perform(() => api.openButlerArtifact(matter.id, artifactId, matter.version)),
    control: (action: ButlerControlAction) => perform(() => api.controlButler(action)),
    arrange: (matter: ButlerMatter, input: Omit<ButlerArrangementInput, "matter_id" | "version">) => perform(() => api.arrangeButlerMatter({ ...input, matter_id: matter.id, version: matter.version })),
    controlMatter: (matter: ButlerMatter, action: ButlerMatterControlAction) => perform(() => api.controlButlerMatter(matter.id, action)),
    createScope: (input: AutonomyScopeInput) => perform(() => api.createButlerScope(input)),
    revokeScope: (scope: AutonomyScope) => perform(() => api.revokeButlerScope(scope.id, scope.version)),
    undo: (matter: ButlerMatter) => perform(() => api.undoButlerMatter(matter.id, matter.version))
  };
}

export type ButlerController = ReturnType<typeof useButler>;
