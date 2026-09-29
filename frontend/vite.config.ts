import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// dev: `ssh -L 8080:127.0.0.1:8080 bng01`, then `npm run dev`. No changeOrigin: bng-api
// only accepts WebSockets whose Origin matches the Host header.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { "/api": { target: "http://localhost:8080", ws: true } } },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 800 },
});
