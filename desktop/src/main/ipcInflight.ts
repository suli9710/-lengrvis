type InflightGroupEntry = {
  controller: AbortController;
  activeRequests: number;
};

export type InflightGroupLease = {
  signal: AbortSignal;
  release: () => void;
};

const apiInflightGroups = new Map<string, InflightGroupEntry>();

export function abortInflightApiGroup(abortGroup: string): void {
  apiInflightGroups.get(abortGroup)?.controller.abort();
  apiInflightGroups.delete(abortGroup);
}

export function acquireInflightGroupSignal(abortGroup: string | undefined): InflightGroupLease | undefined {
  if (!abortGroup) {
    return undefined;
  }
  let entry = apiInflightGroups.get(abortGroup);
  if (!entry || entry.controller.signal.aborted) {
    entry = { controller: new AbortController(), activeRequests: 0 };
    apiInflightGroups.set(abortGroup, entry);
  }
  entry.activeRequests += 1;
  let released = false;
  return {
    signal: entry.controller.signal,
    release: () => {
      if (released) return;
      released = true;
      entry.activeRequests = Math.max(0, entry.activeRequests - 1);
      if (entry.activeRequests === 0 && apiInflightGroups.get(abortGroup) === entry) {
        apiInflightGroups.delete(abortGroup);
      }
    }
  };
}

export function mergeAbortSignals(signals: AbortSignal[]): AbortSignal {
  const controller = new AbortController();
  for (const signal of signals) {
    if (signal.aborted) {
      controller.abort();
      return controller.signal;
    }
    signal.addEventListener("abort", () => controller.abort(), { once: true });
  }
  return controller.signal;
}
