import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState
} from "react";

export type InterfaceDetailMode = "standard" | "expert";
export type MotionPreference = "system" | "full" | "reduced";
export type EffectiveMotion = "full" | "reduced";

export interface UiPreferences {
  version: 1;
  detailMode: InterfaceDetailMode;
  motionPreference: MotionPreference;
}

export const UI_PREFERENCES_STORAGE_KEY = "lengrvis.ui-preferences.v1";
export const defaultUiPreferences: UiPreferences = {
  version: 1,
  detailMode: "standard",
  motionPreference: "system"
};

interface UiPreferencesContextValue {
  preferences: UiPreferences;
  effectiveMotion: EffectiveMotion;
  setDetailMode: (mode: InterfaceDetailMode) => void;
  setMotionPreference: (preference: MotionPreference) => void;
}

const UiPreferencesContext = createContext<UiPreferencesContextValue | null>(null);

interface MotionMediaQuery {
  matches: boolean;
  addEventListener?: (type: "change", listener: (event: { matches: boolean }) => void) => void;
  removeEventListener?: (type: "change", listener: (event: { matches: boolean }) => void) => void;
  addListener?: (listener: (event: { matches: boolean }) => void) => void;
  removeListener?: (listener: (event: { matches: boolean }) => void) => void;
}

export function parseUiPreferences(value: string | null | undefined): UiPreferences {
  if (!value) return defaultUiPreferences;
  try {
    const parsed = JSON.parse(value) as Partial<UiPreferences> | null;
    if (!parsed || parsed.version !== 1) return defaultUiPreferences;
    return {
      version: 1,
      detailMode: parsed.detailMode === "expert" ? "expert" : "standard",
      motionPreference:
        parsed.motionPreference === "full" || parsed.motionPreference === "reduced"
          ? parsed.motionPreference
          : "system"
    };
  } catch {
    return defaultUiPreferences;
  }
}

export function readUiPreferences(storage?: Pick<Storage, "getItem"> | null): UiPreferences {
  if (!storage) return defaultUiPreferences;
  try {
    return parseUiPreferences(storage.getItem(UI_PREFERENCES_STORAGE_KEY));
  } catch {
    return defaultUiPreferences;
  }
}

export function persistUiPreferences(
  preferences: UiPreferences,
  storage?: Pick<Storage, "setItem"> | null
): void {
  if (!storage) return;
  try {
    storage.setItem(UI_PREFERENCES_STORAGE_KEY, JSON.stringify(preferences));
  } catch {
    // UI preferences are best-effort and must never prevent the app from rendering.
  }
}

export function resolveEffectiveMotion(
  preference: MotionPreference,
  systemPrefersReducedMotion: boolean
): EffectiveMotion {
  if (preference === "reduced") return "reduced";
  if (preference === "full") return "full";
  return systemPrefersReducedMotion ? "reduced" : "full";
}

export function applyUiPreferenceAttributes(
  root: { dataset: Record<string, string | undefined> },
  preferences: UiPreferences,
  effectiveMotion: EffectiveMotion
): void {
  root.dataset.detailMode = preferences.detailMode;
  root.dataset.motionPreference = preferences.motionPreference;
  root.dataset.motion = effectiveMotion;
}

function getSystemMotionPreference(): boolean {
  return Boolean(
    typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
  );
}

export function getRendererLocalStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function subscribeToSystemMotionPreference(
  mediaQuery: MotionMediaQuery,
  onChange: (matches: boolean) => void
): () => void {
  const handleChange = (event: { matches: boolean }) => onChange(event.matches);
  onChange(mediaQuery.matches);
  if (mediaQuery.addEventListener) {
    mediaQuery.addEventListener("change", handleChange);
    return () => mediaQuery.removeEventListener?.("change", handleChange);
  }
  mediaQuery.addListener?.(handleChange);
  return () => mediaQuery.removeListener?.(handleChange);
}

export function UiPreferencesProvider({ children }: { children: ReactNode }) {
  const [preferences, setPreferences] = useState<UiPreferences>(() =>
    readUiPreferences(getRendererLocalStorage())
  );
  const [systemPrefersReducedMotion, setSystemPrefersReducedMotion] = useState(getSystemMotionPreference);

  const effectiveMotion = resolveEffectiveMotion(
    preferences.motionPreference,
    systemPrefersReducedMotion
  );

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return undefined;
    const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    return subscribeToSystemMotionPreference(mediaQuery, setSystemPrefersReducedMotion);
  }, []);

  useEffect(() => {
    if (typeof document !== "undefined") {
      applyUiPreferenceAttributes(document.documentElement, preferences, effectiveMotion);
    }
    persistUiPreferences(preferences, getRendererLocalStorage());
  }, [effectiveMotion, preferences]);

  const setDetailMode = useCallback((detailMode: InterfaceDetailMode) => {
    setPreferences((current) => ({ ...current, detailMode }));
  }, []);

  const setMotionPreference = useCallback((motionPreference: MotionPreference) => {
    setPreferences((current) => ({ ...current, motionPreference }));
  }, []);

  const value = useMemo<UiPreferencesContextValue>(
    () => ({ preferences, effectiveMotion, setDetailMode, setMotionPreference }),
    [effectiveMotion, preferences, setDetailMode, setMotionPreference]
  );

  return <UiPreferencesContext.Provider value={value}>{children}</UiPreferencesContext.Provider>;
}

export function useUiPreferences(): UiPreferencesContextValue {
  const context = useContext(UiPreferencesContext);
  if (!context) {
    throw new Error("useUiPreferences must be used within UiPreferencesProvider");
  }
  return context;
}
