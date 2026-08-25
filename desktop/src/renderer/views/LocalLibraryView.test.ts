import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiResponse } from "../../shared/desktopBridgeTypes";
import type { LocalLibraryItem, LocalLibraryResponse } from "../../shared/fileLibraryTypes";
import type { LengrvisApiClient } from "../lib/apiClient";
import { LocalLibraryView } from "./LocalLibraryView";
import {
  initialLocalLibraryLoadState,
  localLibraryContextKey,
  localLibraryLoadReducer,
  LocalLibraryLoadCoordinator,
  type LocalLibraryLoadAction,
  type LocalLibraryLoadState
} from "./localLibraryLoadCoordinator";

const rendererWindow = window as unknown as {
  lengrvis?: {
    backendBaseUrl?: string;
    shell?: { getFileIcon?: (path: string) => Promise<string | null> };
  };
};
const originalLengrvis = rendererWindow.lengrvis;

afterEach(() => {
  rendererWindow.lengrvis = originalLengrvis;
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

function library(section: string, ids: string[]): LocalLibraryResponse {
  return {
    section,
    roots: ["C:\\Users\\Smoke\\Documents"],
    items: ids.map((id) => libraryItem(id)),
    count: ids.length,
    total: ids.length,
    scanned: ids.length,
    truncated: false,
    stats: { size: ids.length * 10, byExtension: { ".txt": ids.length } }
  };
}

function libraryItem(id: string): LocalLibraryItem {
  return {
    id,
    path: `C:\\Users\\Smoke\\Documents\\${id}.txt`,
    name: `${id}.txt`,
    parent: "C:\\Users\\Smoke\\Documents",
    kind: "document",
    extension: ".txt",
    mimeType: "text/plain",
    size: 10,
    createdAt: 1,
    modifiedAt: 1,
    previewUrl: "",
    groupLabel: "文本"
  };
}

function success(data: LocalLibraryResponse): ApiResponse<LocalLibraryResponse> {
  return { ok: true, status: 200, data, receivedAt: "2026-07-12T00:00:00Z" };
}

function failure(message: string): ApiResponse<LocalLibraryResponse> {
  return {
    ok: false,
    status: 500,
    error: { message },
    receivedAt: "2026-07-12T00:00:00Z"
  };
}

function stateHarness(initial: LocalLibraryLoadState = initialLocalLibraryLoadState) {
  let state = initial;
  const actions: LocalLibraryLoadAction[] = [];
  return {
    actions,
    dispatch(action: LocalLibraryLoadAction) {
      actions.push(action);
      state = localLibraryLoadReducer(state, action);
    },
    state() {
      return state;
    }
  };
}

describe("LocalLibraryLoadCoordinator", () => {
  it("keeps the newest section response when requests resolve out of order", async () => {
    const coordinator = new LocalLibraryLoadCoordinator();
    const harness = stateHarness();
    const documents = deferred<ApiResponse<LocalLibraryResponse>>();
    const gallery = deferred<ApiResponse<LocalLibraryResponse>>();
    const documentsKey = localLibraryContextKey("documents", "");
    const galleryKey = localLibraryContextKey("gallery", "");

    const oldRequest = coordinator.load({
      contextKey: documentsKey,
      request: () => documents.promise,
      dispatch: harness.dispatch
    });
    const latestRequest = coordinator.load({
      contextKey: galleryKey,
      request: () => gallery.promise,
      dispatch: harness.dispatch
    });

    gallery.resolve(success(library("gallery", ["new-image"])));
    await latestRequest;
    documents.resolve(success(library("documents", ["stale-document"])));
    await oldRequest;

    expect(harness.state().contextKey).toBe(galleryKey);
    expect(harness.state().library?.section).toBe("gallery");
    expect(harness.state().selectedItem?.id).toBe("new-image");
  });

  it("isolates a newly submitted query and ignores the old response", async () => {
    const coordinator = new LocalLibraryLoadCoordinator();
    const oldKey = localLibraryContextKey("documents", "alpha");
    const nextKey = localLibraryContextKey("documents", "beta");
    const oldLibrary = library("documents", ["alpha-result"]);
    const harness = stateHarness(
      localLibraryLoadReducer(initialLocalLibraryLoadState, {
        type: "success",
        contextKey: oldKey,
        library: oldLibrary,
        selectedItem: oldLibrary.items[0]
      })
    );
    const oldResponse = deferred<ApiResponse<LocalLibraryResponse>>();
    const nextResponse = deferred<ApiResponse<LocalLibraryResponse>>();

    const oldRequest = coordinator.load({
      contextKey: oldKey,
      preferredItemId: "alpha-result",
      request: () => oldResponse.promise,
      dispatch: harness.dispatch
    });
    const nextRequest = coordinator.load({
      contextKey: nextKey,
      request: () => nextResponse.promise,
      dispatch: harness.dispatch
    });

    expect(harness.state().contextKey).toBe(nextKey);
    expect(harness.state().library).toBeNull();
    expect(harness.state().selectedItem).toBeNull();

    nextResponse.resolve(success(library("documents", ["beta-result"])));
    await nextRequest;
    oldResponse.resolve(success(library("documents", ["late-alpha-result"])));
    await oldRequest;

    expect(harness.state().contextKey).toBe(nextKey);
    expect(harness.state().selectedItem?.id).toBe("beta-result");
  });

  it("clears the selected item while refreshing and restores it only when still present", async () => {
    const coordinator = new LocalLibraryLoadCoordinator();
    const contextKey = localLibraryContextKey("documents", "");
    const currentLibrary = library("documents", ["first", "selected"]);
    const harness = stateHarness(
      localLibraryLoadReducer(initialLocalLibraryLoadState, {
        type: "success",
        contextKey,
        library: currentLibrary,
        selectedItem: currentLibrary.items[1]
      })
    );
    const response = deferred<ApiResponse<LocalLibraryResponse>>();

    const request = coordinator.load({
      contextKey,
      preferredItemId: "selected",
      request: () => response.promise,
      dispatch: harness.dispatch
    });
    expect(harness.state().library).toBeNull();
    expect(harness.state().selectedItem).toBeNull();
    expect(harness.state().isLoading).toBe(true);

    response.resolve(success(library("documents", ["first", "selected"])));
    await request;

    expect(harness.state().selectedItem?.id).toBe("selected");
    expect(harness.state().isLoading).toBe(false);
  });

  it("commits only the latest failure and leaves no stale library actions available", async () => {
    const coordinator = new LocalLibraryLoadCoordinator();
    const contextKey = localLibraryContextKey("reports", "quarterly");
    const staleLibrary = library("documents", ["stale"]);
    const harness = stateHarness(
      localLibraryLoadReducer(initialLocalLibraryLoadState, {
        type: "success",
        contextKey: localLibraryContextKey("documents", ""),
        library: staleLibrary,
        selectedItem: staleLibrary.items[0]
      })
    );

    await coordinator.load({
      contextKey,
      request: async () => failure("索引暂时不可用"),
      dispatch: harness.dispatch
    });

    expect(harness.state()).toMatchObject({
      contextKey,
      library: null,
      selectedItem: null,
      isLoading: false,
      error: "索引暂时不可用"
    });
  });

  it("does not dispatch a response after disposal", async () => {
    const coordinator = new LocalLibraryLoadCoordinator();
    const harness = stateHarness();
    const response = deferred<ApiResponse<LocalLibraryResponse>>();
    const request = coordinator.load({
      contextKey: localLibraryContextKey("documents", ""),
      request: () => response.promise,
      dispatch: harness.dispatch
    });

    coordinator.dispose();
    response.resolve(success(library("documents", ["ignored"])));
    await request;

    expect(harness.actions.map((action) => action.type)).toEqual(["start"]);
  });
});

describe("LocalLibraryView interactions", () => {
  it("keeps the submitted empty state while editing and ignores composing Enter", async () => {
    const listLocalLibrary = vi.fn().mockResolvedValue(success(library("documents", [])));
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(LocalLibraryView, {
        api: { listLocalLibrary } as unknown as LengrvisApiClient,
        activeSection: "documents"
      }));
    });

    expect(textContent(renderer)).toContain("授权范围里暂时没有对应内容");
    const input = renderer.root.findByProps({ "aria-label": "搜索本地内容" });
    act(() => {
      input.props.onChange({ currentTarget: { value: "发票" } });
    });

    expect(listLocalLibrary).toHaveBeenCalledTimes(1);
    expect(textContent(renderer)).toContain("授权范围里暂时没有对应内容");
    expect(textContent(renderer)).not.toContain("先选择一个授权范围");

    act(() => {
      renderer.root.findByProps({ "aria-label": "搜索本地内容" }).props.onKeyDown({
        key: "Enter",
        nativeEvent: { isComposing: true },
        currentTarget: { value: "发票" }
      });
    });
    expect(listLocalLibrary).toHaveBeenCalledTimes(1);

    await act(async () => {
      renderer.root.findByProps({ "aria-label": "搜索本地内容" }).props.onKeyDown({
        key: "Enter",
        nativeEvent: { isComposing: false },
        currentTarget: { value: "发票" }
      });
    });

    expect(listLocalLibrary).toHaveBeenLastCalledWith("documents", "发票", 260);
    expect(textContent(renderer)).toContain("这个关键词没有命中");
  });

  it("disables refresh while loading and restores the selected item", async () => {
    const refreshed = deferred<ApiResponse<LocalLibraryResponse>>();
    const listLocalLibrary = vi
      .fn()
      .mockResolvedValueOnce(success(library("documents", ["first", "selected"])))
      .mockImplementationOnce(() => refreshed.promise);
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(LocalLibraryView, {
        api: { listLocalLibrary } as unknown as LengrvisApiClient,
        activeSection: "documents"
      }));
    });
    act(() => {
      renderer.root.findByProps({ title: "selected.txt" }).props.onClick();
    });

    act(() => {
      renderer.root.findByProps({ "aria-label": "刷新本地内容" }).props.onClick();
    });
    expect(renderer.root.findByProps({ "aria-label": "刷新本地内容" }).props.disabled).toBe(true);

    await act(async () => {
      refreshed.resolve(success(library("documents", ["first", "selected"])));
      await refreshed.promise;
    });

    expect(renderer.root.findByProps({ title: "selected.txt" }).props["aria-pressed"]).toBe(true);
  });

  it("submits the visible draft when switching categories", async () => {
    const listLocalLibrary = vi.fn().mockResolvedValue(success(library("documents", [])));
    const api = { listLocalLibrary } as unknown as LengrvisApiClient;
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(LocalLibraryView, { api, activeSection: "documents" }));
    });
    act(() => {
      renderer.root.findByProps({ "aria-label": "搜索本地内容" }).props.onChange({
        currentTarget: { value: "季度报告" }
      });
    });

    await act(async () => {
      renderer.update(createElement(LocalLibraryView, { api, activeSection: "reports" }));
    });

    expect(listLocalLibrary).toHaveBeenLastCalledWith("reports", "季度报告", 260);
    expect(renderer.root.findByProps({ "aria-label": "搜索本地内容" }).props.value).toBe("季度报告");
    expect(textContent(renderer)).toContain("这个关键词没有命中");
  });

  it("loads at most 80 native icons per response and continues after an explicit refresh", async () => {
    const getFileIcon = vi.fn().mockResolvedValue("data:image/png;base64,icon");
    rendererWindow.lengrvis = { shell: { getFileIcon } };
    const items = Array.from({ length: 120 }, (_, index) => ({
      ...libraryItem(`app-${index}`),
      kind: "app",
      extension: ".exe"
    }));
    const response = library("apps", []);
    response.items = items;
    response.count = items.length;
    response.total = items.length;
    const listLocalLibrary = vi.fn().mockResolvedValue(success(response));
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(LocalLibraryView, {
        api: { listLocalLibrary } as unknown as LengrvisApiClient,
        activeSection: "apps"
      }));
    });
    expect(getFileIcon).toHaveBeenCalledTimes(80);

    await act(async () => {
      renderer.root.findByProps({ "aria-label": "刷新本地内容" }).props.onClick();
    });
    expect(getFileIcon).toHaveBeenCalledTimes(120);
  });

  it("retries a transient native icon failure after refresh", async () => {
    const getFileIcon = vi
      .fn()
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce("data:image/png;base64,recovered");
    rendererWindow.lengrvis = { shell: { getFileIcon } };
    const response = library("apps", []);
    response.items = [{ ...libraryItem("retry-app"), kind: "app", extension: ".exe" }];
    response.count = 1;
    response.total = 1;
    const listLocalLibrary = vi.fn().mockResolvedValue(success(response));
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(LocalLibraryView, {
        api: { listLocalLibrary } as unknown as LengrvisApiClient,
        activeSection: "apps"
      }));
    });
    expect(getFileIcon).toHaveBeenCalledTimes(1);

    await act(async () => {
      renderer.root.findByProps({ "aria-label": "刷新本地内容" }).props.onClick();
    });

    expect(getFileIcon).toHaveBeenCalledTimes(2);
    expect(renderer.root.findAllByType("img").some((image) => image.props.src === "data:image/png;base64,recovered")).toBe(true);
  });
});

describe("LocalLibraryView tabs", () => {
  it("marks only the active category as the current page", () => {
    const markup = renderToStaticMarkup(
      createElement(LocalLibraryView, {
        api: {} as LengrvisApiClient,
        activeSection: "documents",
        onSectionChange: () => undefined
      })
    );

    expect(markup.match(/aria-current="page"/g)).toHaveLength(1);
    expect(markup).toMatch(/class="library-tab library-tab--active" aria-current="page"/);
  });
});

function textContent(renderer: ReactTestRenderer): string {
  return renderer.root
    .findAll(() => true)
    .flatMap((node) => node.children)
    .filter((child): child is string => typeof child === "string")
    .join(" ");
}
