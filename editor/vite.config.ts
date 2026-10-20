import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const siteSrc = fileURLToPath(new URL("../src/webblock_harness/templates/site/src", import.meta.url));

// The editor renders the *same* component files that are pushed to client repositories.
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  // Shared site components live outside this package: always resolve React from the editor's node_modules.
  resolve: { alias: { "@site": siteSrc }, dedupe: ["react", "react-dom"] },
  server: {
    port: 5173,
    proxy: { "/v1": "http://127.0.0.1:8080" },
    fs: { allow: [".."] },
  },
  build: {
    outDir: fileURLToPath(new URL("../src/webblock_harness/static/app", import.meta.url)),
    emptyOutDir: true,
    chunkSizeWarningLimit: 2000,
  },
});
