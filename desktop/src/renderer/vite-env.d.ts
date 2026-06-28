/// <reference types="vite/client" />

import type { LengrvisDesktopBridge } from "../shared/types";

interface ImportMetaEnv {
  readonly VITE_LENGRVIS_DESKTOP_API_TOKEN?: string;
  readonly VITE_LENGRVIS_DEV_SKIP_CONSENT_GATE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare global {
  interface Window {
    lengrvis: LengrvisDesktopBridge;
  }
}
