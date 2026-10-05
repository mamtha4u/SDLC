import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { "/api": "http://localhost:8080" } },
  build: {
    // 4000: the lazily-loaded Code view (Monaco) is a big chunk by nature; everything on the first load stays small
    outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 4000,
    rollupOptions: {
      output: {
        // stable vendor chunks → cached across deploys; app code changes don't re-download React/motion
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom", "@tanstack/react-query"],
          motion: ["framer-motion"],
          markdown: ["react-markdown", "remark-gfm"],
        },
      },
    },
  },
});
