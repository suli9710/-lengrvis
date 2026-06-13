import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  plugins: [react()],
  resolve: {
    alias: {
      "@renderer": resolve(__dirname, "src/renderer"),
      "@shared": resolve(__dirname, "src/shared")
    }
  },
  build: {
    outDir: "dist/renderer",
    emptyOutDir: true,
    sourcemap: false,
    // The renderer only runs in the bundled Electron Chromium (and modern
    // browsers in dev:web), so target a modern baseline instead of Vite's
    // broad default. This also avoids an esbuild>=0.28 regression that errors
    // when lowering object-rest destructuring for the wide default target.
    target: "es2022",
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom"],
          icons: ["lucide-react"],
          state: ["zustand"]
        }
      }
    }
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true
  }
});
