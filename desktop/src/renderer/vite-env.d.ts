/// <reference types="vite/client" />

import type { LengrvisDesktopBridge } from "../shared/types";

declare global {
  interface Window {
    lengrvis: LengrvisDesktopBridge;
  }
}
