import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev: `npm run dev` on 5173 talks to a backend on 8020 through this proxy,
// so cookies and the X-Requested-With header behave exactly as in production.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8020",
      "/health": "http://127.0.0.1:8020",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  test: {
    environment: "node",
  },
});
