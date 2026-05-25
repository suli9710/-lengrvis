/// <reference types="vite/client" />

import type { MavrisDesktopBridge } from "../shared/types";

declare global {
  interface Window {
    mavris: MavrisDesktopBridge;
  }
}
