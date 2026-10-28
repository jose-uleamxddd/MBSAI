import { defineConfig } from "vitest/config";

// Vitest para la lógica PURA del renderer extraída a `lib/` (sin three.js ni DOM
// de Electron). Los módulos de `lib/` no importan three ni tocan `window`, así que
// corren headless en jsdom. Los tests de integración con DOM viven en `tests/`.
export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["lib/**/*.test.js", "tests/**/*.test.js"],
    coverage: {
      provider: "v8",
      include: ["lib/**/*.js"],
      exclude: ["lib/**/*.test.js"],
      reporter: ["text", "text-summary"],
    },
  },
});
