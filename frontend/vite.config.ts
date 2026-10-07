/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
  },
  server: {
    proxy: {
      "/v1": "http://localhost:8001",
    },
  },
});
