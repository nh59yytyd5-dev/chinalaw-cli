import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "../src/chinalaw/server/static", emptyOutDir: true },
  server: {
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(
      [
        "/api",
        "/mcp",
        "/register",
        "/authorize",
        "/token",
        "/revoke",
        "/.well-known",
      ].map((path) => [
        path,
        { target: "http://127.0.0.1:8765", changeOrigin: false },
      ]),
    ),
  },
});
