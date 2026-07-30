import type { ApiResponse } from "../../shared/desktopBridgeTypes";
import type { LocalLibraryItem, LocalLibraryResponse } from "../../shared/fileLibraryTypes";

export interface LocalLibraryLoadState {
  contextKey: string;
  library: LocalLibraryResponse | null;
  selectedItem: LocalLibraryItem | null;
  isLoading: boolean;
  error: string | null;
}

export type LocalLibraryLoadAction =
  | { type: "start"; contextKey: string }
  | { type: "select"; contextKey: string; selectedItem: LocalLibraryItem }
  | {
      type: "success";
      contextKey: string;
      library: LocalLibraryResponse;
      selectedItem: LocalLibraryItem | null;
    }
  | { type: "failure"; contextKey: string; error: string };

interface LocalLibraryLoadRequest {
  contextKey: string;
  preferredItemId?: string | null;
  request: () => Promise<ApiResponse<LocalLibraryResponse>>;
  dispatch: (action: LocalLibraryLoadAction) => void;
}

export const initialLocalLibraryLoadState: LocalLibraryLoadState = {
  contextKey: "",
  library: null,
  selectedItem: null,
  isLoading: false,
  error: null
};

export function localLibraryContextKey(section: string, query: string): string {
  return JSON.stringify([section, query.trim()]);
}

export function localLibraryLoadReducer(
  state: LocalLibraryLoadState,
  action: LocalLibraryLoadAction
): LocalLibraryLoadState {
  if (action.type === "start") {
    return {
      contextKey: action.contextKey,
      library: null,
      selectedItem: null,
      isLoading: true,
      error: null
    };
  }
  if (action.type === "success") {
    return {
      contextKey: action.contextKey,
      library: action.library,
      selectedItem: action.selectedItem,
      isLoading: false,
      error: null
    };
  }
  if (action.type === "select") {
    return state.contextKey === action.contextKey
      ? { ...state, selectedItem: action.selectedItem }
      : state;
  }
  return {
    contextKey: action.contextKey,
    library: null,
    selectedItem: null,
    isLoading: false,
    error: action.error
  };
}

export function localLibraryStateForContext(
  state: LocalLibraryLoadState,
  contextKey: string
): LocalLibraryLoadState {
  if (state.contextKey === contextKey) return state;
  return { ...initialLocalLibraryLoadState, contextKey };
}

export class LocalLibraryLoadCoordinator {
  private generation = 0;
  private mounted = true;

  activate(): void {
    if (this.mounted) return;
    this.mounted = true;
    this.generation += 1;
  }

  dispose(): void {
    this.mounted = false;
    this.generation += 1;
  }

  async load({ contextKey, preferredItemId, request, dispatch }: LocalLibraryLoadRequest): Promise<void> {
    if (!this.mounted) return;
    const generation = ++this.generation;
    dispatch({ type: "start", contextKey });

    let response: ApiResponse<LocalLibraryResponse>;
    try {
      response = await request();
    } catch {
      if (this.isCurrent(generation)) {
        dispatch({ type: "failure", contextKey, error: "读取本地内容失败" });
      }
      return;
    }

    if (!this.isCurrent(generation)) return;
    if (response.ok && response.data) {
      const selectedItem = preferredItemId
        ? response.data.items.find((item) => item.id === preferredItemId) ?? response.data.items[0] ?? null
        : response.data.items[0] ?? null;
      dispatch({ type: "success", contextKey, library: response.data, selectedItem });
      return;
    }
    dispatch({
      type: "failure",
      contextKey,
      error: response.error?.message ?? "读取本地内容失败"
    });
  }

  private isCurrent(generation: number): boolean {
    return this.mounted && generation === this.generation;
  }
}
