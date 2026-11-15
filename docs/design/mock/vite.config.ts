import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// Design workspace for step 14a part 0 (mocks only, not the real panel).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // "motion/react" is the new name of framer-motion; the 21st.dev picks import it, the mock has framer-motion
  resolve: { alias: { "@": path.resolve(__dirname, "src"), "motion/react": "framer-motion" } },
  server: { host: "127.0.0.1", port: 5179, strictPort: true },
  preview: { host: "127.0.0.1", port: 5179, strictPort: true },
});
