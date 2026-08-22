import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

// The engine's default port (cli.py --port). Dev serves the SPA on 5173 and proxies /api
// to it, so the client uses the same relative URLs in dev and in production, where the
// engine serves the built bundle itself and there is no second origin at all.
const ENGINE = "http://127.0.0.1:8787";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    proxy: {
      "/api": {
        target: ENGINE,
        changeOrigin: false,
        // /stream is Server-Sent Events: the proxy must forward each frame as it
        // arrives. Buffering here would look exactly like a dead run -- the page
        // connects, nothing renders, no error anywhere.
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            if (proxyRes.headers["content-type"]?.includes("text/event-stream")) {
              proxyRes.headers["cache-control"] = "no-cache, no-transform";
            }
          });
        },
      },
      // Browser Use frames, served by the engine out of the run's shots dir.
      "/shots": { target: ENGINE, changeOrigin: false },
    },
  },
});
