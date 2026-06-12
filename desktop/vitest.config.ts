import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["src/renderer/**/*.test.ts", "src/shared/**/*.test.ts"],
    setupFiles: ["src/renderer/lib/api/__tests__/setup.ts"]
  }
});
