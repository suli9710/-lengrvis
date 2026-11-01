import { createHash } from "node:crypto";
import { basename, resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig(({ mode }) => {
  const isProduction = mode === "production";

  return {
    base: "./",
    plugins: [
      react(),
      {
        name: "lengrvis-renderer-budget-provenance",
        apply: "build",
        generateBundle(_options, bundle) {
          const chunks: Record<string, string[]> = {};
          const hashes: Record<string, string> = {};
          for (const output of Object.values(bundle)) {
            if (output.type !== "chunk") continue;
            chunks[output.fileName] = Object.keys(output.modules).map((id) => {
              const normalized = id.replace(/\\/g, "/");
              // Never publish machine-specific absolute paths. Unknown/virtual
              // modules remain visible and cannot qualify for the React allowance.
              if (normalized.startsWith("\0")) return normalized;
              const dependency = normalized.lastIndexOf("/node_modules/");
              if (dependency >= 0) return normalized.slice(dependency + 1);
              const source = normalized.indexOf("/src/");
              if (source >= 0) return normalized.slice(source + 1);
              return `external:${basename(normalized)}`;
            });
            hashes[output.fileName] = createHash("sha256").update(output.code).digest("hex");
          }
          this.emitFile({
            type: "asset",
            fileName: "renderer-budget-modules.json",
            source: JSON.stringify({ schemaVersion: 1, chunks, hashes })
          });
        }
      },
      {
        name: "lengrvis-dev-style-csp",
        apply: "serve",
        // Vite injects renderer CSS through <style> tags during dev:web. Keep
        // production CSP strict while allowing only the local dev server path.
        transformIndexHtml(html) {
          return html.replace("style-src-elem 'self'", "style-src-elem 'self' 'unsafe-inline'");
        }
      }
    ],
    resolve: {
      alias: {
        "@renderer": resolve(__dirname, "src/renderer"),
        "@shared": resolve(__dirname, "src/shared")
      }
    },
    // Never embed the desktop API token in production renderer bundles. dev:web
    // reads VITE_LENGRVIS_DESKTOP_API_TOKEN only while `vite` is running locally.
    define: isProduction
      ? {
          "import.meta.env.VITE_LENGRVIS_DESKTOP_API_TOKEN": '""',
          "import.meta.env.VITE_LENGRVIS_DEV_SKIP_CONSENT_GATE": '""'
        }
      : undefined,
    build: {
      outDir: "dist/renderer",
      emptyOutDir: true,
      sourcemap: false,
      minify: "terser",
      // Output chunks are ES modules targeting the bundled modern Chromium.
      // Extra compression passes remove redundancies exposed by prior passes;
      // do not enable unsafe transforms or property mangling.
      terserOptions: {
        ecma: 2022,
        module: true,
        toplevel: true,
        compress: { passes: 3 }
      },
      // The renderer only runs in the bundled Electron Chromium (and modern
      // browsers in dev:web), so target a modern baseline instead of Vite's
      // broad default. This also avoids an esbuild>=0.28 regression that errors
      // when lowering object-rest destructuring for the wide default target.
      target: "es2022",
      rollupOptions: {
        output: {
          manualChunks(id) {
            const modulePath = id.replace(/\\/g, "/");
            if (/\/node_modules\/(react|react-dom|scheduler)\//.test(modulePath)) return "react";
            if (modulePath.includes("/node_modules/lucide-react/")) return "icons";
            if (modulePath.includes("/node_modules/zustand/")) return "state";
          }
        }
      }
    },
    optimizeDeps: {
      esbuildOptions: {
        target: "es2022"
      }
    },
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true
    }
  };
});
