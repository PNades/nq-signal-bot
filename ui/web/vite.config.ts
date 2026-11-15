import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// CreamFlow Control Panel (SPEC §9b). Built into dist/ by `nqbot ui build`; served only by `nqbot ui` (Starlette on
// 127.0.0.1). The server's CSP is `default-src 'self'`, so the build never inlines assets as data: URLs
// (assetsInlineLimit 0) and never emits inline scripts or <style> elements; fonts are bundled woff2 files.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // "motion/react" is the new name of framer-motion; the 21st.dev picks import it
  resolve: { alias: { "@": path.resolve(__dirname, "src"), "motion/react": "framer-motion" } },
  build: { outDir: "dist", emptyOutDir: true, assetsInlineLimit: 0, sourcemap: false, modulePreload: { polyfill: false } },
});
