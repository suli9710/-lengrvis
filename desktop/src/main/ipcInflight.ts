const apiInflightGroups = new Map<string, AbortController>();

export function abortInflightApiGroup(abortGroup: string): void {
  apiInflightGroups.get(abortGroup)?.abort();
  apiInflightGroups.delete(abortGroup);
}

export function resolveInflightGroupSignal(abortGroup: string | undefined): AbortSignal | undefined {
  if (!abortGroup) {
    return undefined;
  }
  let controller = apiInflightGroups.get(abortGroup);
  if (!controller || controller.signal.aborted) {
    controller = new AbortController();
    apiInflightGroups.set(abortGroup, controller);
  }
  return controller.signal;
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
