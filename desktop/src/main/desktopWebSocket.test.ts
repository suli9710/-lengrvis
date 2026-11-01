import { EventEmitter } from "node:events";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { IPC_CHANNELS } from "../shared/ipc";

const handlers = vi.hoisted(() => new Map<string, (...args: any[]) => any>());
vi.mock("electron", () => ({ ipcMain: { handle: (key: string, fn: (...args: any[]) => any) => handlers.set(key, fn) } }));
vi.mock("./ipc", () => ({ assertTrustedRenderer: vi.fn() }));
import { registerDesktopWebSocketIpcHandlers } from "./desktopWebSocket";
import type { BackendProcessManager } from "./backendProcess";

class FakeSocket extends EventTarget {
  static instances: FakeSocket[] = [];
  close = vi.fn();
  constructor() { super(); FakeSocket.instances.push(this); }
}
function sender(id: number) {
  return Object.assign(new EventEmitter(), { id, isDestroyed: () => false, send: vi.fn() });
}
beforeEach(() => {
  handlers.clear();
  FakeSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeSocket);
  registerDesktopWebSocketIpcHandlers({ getBaseUrl: () => "http://127.0.0.1:8000", getDesktopApiToken: () => "test-token" } as BackendProcessManager);
});
afterEach(() => vi.unstubAllGlobals());

it("isolates socket IDs and close requests between renderer owners", () => {
  const first = sender(1), second = sender(2);
  const open = handlers.get(IPC_CHANNELS.desktopWebSocketOpen)!;
  const close = handlers.get(IPC_CHANNELS.desktopWebSocketClose)!;
  const request = { id: "same_socket", endpoint: "/ws/tasks/task-1" };
  expect(open({ sender: first }, request).ok).toBe(true);
  close({ sender: second }, request.id);
  expect(FakeSocket.instances[0].close).not.toHaveBeenCalled();
  expect(open({ sender: second }, request).ok).toBe(true);
  expect(FakeSocket.instances[0].close).not.toHaveBeenCalled();
  close({ sender: first }, request.id);
  expect(FakeSocket.instances[0].close).toHaveBeenCalledOnce();
  expect(FakeSocket.instances[1].close).not.toHaveBeenCalled();
});

it("ignores late events from a replaced socket instead of closing its replacement subscription", () => {
  const owner = sender(1);
  const open = handlers.get(IPC_CHANNELS.desktopWebSocketOpen)!;
  const request = { id: "same_socket", endpoint: "/ws/tasks/task-1" };
  open({ sender: owner }, request);
  const previous = FakeSocket.instances[0];
  open({ sender: owner }, request);
  for (const type of ["open", "message", "error", "close"]) previous.dispatchEvent(new Event(type));
  expect(owner.send).not.toHaveBeenCalled();
  FakeSocket.instances[1].dispatchEvent(new Event("open"));
  expect(owner.send).toHaveBeenCalledWith(IPC_CHANNELS.desktopWebSocketEvent, { id: request.id, type: "open" });
  expect(owner.listenerCount("destroyed")).toBe(1);
});
