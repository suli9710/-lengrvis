import { LoaderCircle, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import type { ApiResponse } from "../../shared/desktopBridgeTypes";

type CollectionResponse<T> = Promise<ApiResponse<T[]>>;
type MutationResponse = Promise<ApiResponse<unknown>>;

const IDLE_LOAD = { isLoading: false, loadError: null as string | null };
const IDLE_MUTATION = { pendingAction: null as string | null, mutationError: null as string | null };

export function useCollectionPanelState<T>(loader: () => CollectionResponse<T>, loadFallback: string) {
  const [items, setItems] = useState<T[]>([]);
  const [loadState, setLoadState] = useState({ isLoading: true, loadError: null as string | null });
  const [mutationState, setMutationState] = useState(IDLE_MUTATION);
  const sequence = useRef(0);
  const mutationLocked = useRef(false);
  const mounted = useRef(true);

  const loadItems = useCallback(async (request: () => CollectionResponse<T>, fallback: string): Promise<boolean> => {
    if (!mounted.current) return false;
    const requestSequence = ++sequence.current;
    setLoadState({ isLoading: true, loadError: null });
    try {
      const response = await request();
      if (!mounted.current || requestSequence !== sequence.current) return false;
      if (!response.ok || !Array.isArray(response.data)) {
        setLoadState({ isLoading: false, loadError: response.error?.message ?? fallback });
        return false;
      }
      setItems(response.data);
      setLoadState(IDLE_LOAD);
      return true;
    } catch {
      if (mounted.current && requestSequence === sequence.current) {
        setLoadState({ isLoading: false, loadError: fallback });
      }
      return false;
    }
  }, []);

  const refresh = useCallback(() => loadItems(loader, loadFallback), [loadFallback, loadItems, loader]);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => {
      mounted.current = false;
      sequence.current += 1;
    };
  }, [refresh]);

  const mutate = useCallback(async (
    key: string,
    request: () => MutationResponse,
    fallback: string,
    onSuccess?: () => unknown
  ): Promise<boolean> => {
    if (mutationLocked.current || !mounted.current) return false;
    mutationLocked.current = true;
    setMutationState({ pendingAction: key, mutationError: null });
    try {
      const response = await request();
      if (!mounted.current) return false;
      if (!response.ok) {
        setMutationState({ pendingAction: null, mutationError: response.error?.message ?? fallback });
        return false;
      }
      await onSuccess?.();
      return true;
    } catch {
      if (mounted.current) setMutationState({ pendingAction: null, mutationError: fallback });
      return false;
    } finally {
      mutationLocked.current = false;
      if (mounted.current) {
        setMutationState((current) => current.pendingAction ? { ...current, pendingAction: null } : current);
      }
    }
  }, []);

  const setMutationError = useCallback((mutationError: string | null) => {
    if (mounted.current) setMutationState((current) => ({ ...current, mutationError }));
  }, []);

  return {
    items,
    ...loadState,
    ...mutationState,
    loadItems,
    mutate,
    refresh,
    setMutationError
  };
}

export function CollectionPanelStatus({
  isLoading,
  loadError,
  loadingLabel,
  onRetry
}: {
  isLoading: boolean;
  loadError: string | null;
  loadingLabel: string;
  onRetry: () => void;
}) {
  return (
    <>
      {isLoading ? (
        <div className="empty-state panel-state panel-state--loading" role="status" aria-live="polite">
          <LoaderCircle size={17} aria-hidden="true" className="spin-icon" />
          <span>{loadingLabel}</span>
        </div>
      ) : null}
      {loadError ? (
        <div className="empty-state empty-state--error panel-state panel-state--error" role="alert">
          <span>{loadError}</span>
          <button className="button button--secondary" type="button" disabled={isLoading} onClick={onRetry}>
            <RefreshCw size={14} aria-hidden="true" />
            重试
          </button>
        </div>
      ) : null}
    </>
  );
}
