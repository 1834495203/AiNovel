import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  base: "/ui/",
  publicDir: false,
  server: { proxy: { "/api": "http://127.0.0.1:8000", "/health": "http://127.0.0.1:8000" } },
  build: {
    outDir: "../web/ui",
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: { output: { manualChunks: { vue: ["vue"], "naive-ui": ["naive-ui"], icons: ["@lucide/vue"] } } },
  },
});
