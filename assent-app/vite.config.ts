import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

// Builds the SPA bundle into ../site/assent/ so Caddy can serve it from /assent/*.
// The app is served under /assent, and the verify route mirrors at /verify/*.
export default defineConfig({
  plugins: [react()],
  base: "/assent/",
  build: {
    outDir: resolve(__dirname, "../site/assent"),
    emptyOutDir: true,
    assetsDir: "assets",
    sourcemap: true,
    rollupOptions: {
      output: {
        // Vendor chunking: keep rarely-changing libraries in their own files
        // so browsers can cache them across app-code deploys. PDF libs are
        // split out separately because they only load inside the Sign route.
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          pdf: ["pdf-lib", "pdfjs-dist"],
          qr: ["qrcode"],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/v1": {
        target: "http://localhost:8100",
        changeOrigin: true,
      },
    },
  },
  worker: {
    format: "es",
  },
});
