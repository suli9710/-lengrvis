import { describe, expect, it } from "vitest";

import {
  applyUiPreferenceAttributes,
  defaultUiPreferences,
  parseUiPreferences,
  persistUiPreferences,
  readUiPreferences,
  resolveEffectiveMotion,
  subscribeToSystemMotionPreference,
  UI_PREFERENCES_STORAGE_KEY
} from "./uiPreferences";

describe("ui preferences", () => {
  it("falls back for missing, invalid, and unsupported stored values", () => {
    expect(parseUiPreferences(null)).toEqual(defaultUiPreferences);
    expect(parseUiPreferences("not-json")).toEqual(defaultUiPreferences);
    expect(parseUiPreferences(JSON.stringify({ version: 2, detailMode: "expert" }))).toEqual(defaultUiPreferences);
  });

  it("normalizes version 1 preferences", () => {
    expect(parseUiPreferences(JSON.stringify({
      version: 1,
      detailMode: "expert",
      motionPreference: "reduced"
    }))).toEqual({
      version: 1,
      detailMode: "expert",
      motionPreference: "reduced"
    });

    expect(parseUiPreferences(JSON.stringify({
      version: 1,
      detailMode: "unexpected",
      motionPreference: "unexpected"
    }))).toEqual(defaultUiPreferences);
  });

  it("persists and reads from renderer-local storage", () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value)
    };
    const next = { version: 1, detailMode: "expert", motionPreference: "full" } as const;

    persistUiPreferences(next, storage);

    expect(values.has(UI_PREFERENCES_STORAGE_KEY)).toBe(true);
    expect(readUiPreferences(storage)).toEqual(next);
  });

  it("resolves explicit motion preferences before the system preference", () => {
    expect(resolveEffectiveMotion("system", true)).toBe("reduced");
    expect(resolveEffectiveMotion("system", false)).toBe("full");
    expect(resolveEffectiveMotion("full", true)).toBe("full");
    expect(resolveEffectiveMotion("reduced", false)).toBe("reduced");
  });

  it("applies stable root attributes", () => {
    const root = { dataset: {} as Record<string, string | undefined> };
    applyUiPreferenceAttributes(root, {
      version: 1,
      detailMode: "expert",
      motionPreference: "system"
    }, "reduced");

    expect(root.dataset).toEqual({
      detailMode: "expert",
      motionPreference: "system",
      motion: "reduced"
    });
  });
  it("reacts to system motion changes and removes the listener", () => {
    let listener: ((event: { matches: boolean }) => void) | undefined;
    const values: boolean[] = [];
    const mediaQuery = {
      matches: false,
      addEventListener: (_type: "change", next: (event: { matches: boolean }) => void) => {
        listener = next;
      },
      removeEventListener: (_type: "change", next: (event: { matches: boolean }) => void) => {
        if (listener === next) listener = undefined;
      }
    };

    const unsubscribe = subscribeToSystemMotionPreference(mediaQuery, (matches) => values.push(matches));
    listener?.({ matches: true });

    expect(values).toEqual([false, true]);
    unsubscribe();
    expect(listener).toBeUndefined();
  });

});
