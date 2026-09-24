/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs on :8080; the app calls it under /api, exactly
// as it does in the container (nginx proxies /api there too).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/api": { target: "http://localhost:8080", changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
  test: { environment: "node" },
});
