import { useCallback, useSyncExternalStore, type SetStateAction } from "react";

export const SESSION_DRAFT_KEYS = {
  home: "lengrvis.draft.home",
  chat: "lengrvis.draft.chat"
} as const;

const drafts = new Map<string, string>();
const listeners = new Map<string, Set<() => void>>();

/** Called only after the user's confirmed local-data erase has succeeded. */
export function clearSessionDrafts(): void {
  let storageFailed = false;
  for (const key of Object.values(SESSION_DRAFT_KEYS)) {
    drafts.set(key, "");
    try {
      window.sessionStorage.removeItem(key);
    } catch { // A failed cleanup must not be reported as a complete privacy erase.
      storageFailed = true;
    }
    listeners.get(key)?.forEach((listener) => listener());
  }
  if (storageFailed) throw new Error("本机记录已删除，但窗口草稿存储无法清除。请关闭此窗口以结束草稿会话。");
}

function readDraft(key: string, initialValue: string): string {
  if (!drafts.has(key)) {
    let value = initialValue;
    try {
      value = window.sessionStorage.getItem(key) ?? initialValue;
    } catch { // Storage may be unavailable; keep the draft in this window's memory.
      // No persistent storage or backend fallback for unsent text.
    }
    drafts.set(key, value);
  }
  return drafts.get(key) ?? initialValue;
}

/** Keep unsent text through navigation/refresh, scoped to the current window session. */
export function useSessionDraft(key: string, initialValue = "") {
  const subscribe = useCallback((listener: () => void) => {
    const subscribers = listeners.get(key) ?? new Set<() => void>();
    listeners.set(key, subscribers);
    subscribers.add(listener);
    return () => { subscribers.delete(listener); };
  }, [key]);
  const getSnapshot = useCallback(() => readDraft(key, initialValue), [key, initialValue]);
  const getServerSnapshot = useCallback(() => initialValue, [initialValue]);
  const draft = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const setDraft = useCallback((update: SetStateAction<string>) => {
    const current = readDraft(key, initialValue);
    const next = typeof update === "function" ? update(current) : update;
    drafts.set(key, next);
    try {
      if (next) window.sessionStorage.setItem(key, next);
      else window.sessionStorage.removeItem(key);
    } catch { // Retain the in-memory draft when storage is blocked or full.
      // Sending still works without browser storage.
    }
    listeners.get(key)?.forEach((listener) => listener());
  }, [key, initialValue]);
  return [draft, setDraft] as const;
}
