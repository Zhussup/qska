import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite dev server on :5173 proxies /api/* to FastAPI on :8000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
