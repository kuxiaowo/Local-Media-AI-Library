import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../localmedia/static",
    emptyOutDir: true,
    rollupOptions: { input: resolve(__dirname, "localmedia.html") },
  },
});
