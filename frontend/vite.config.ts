import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

const BOOTSTRAP_SCOPE = ".bs-scope";

function scopedSelector(selector: string): string {
  const value = selector.trim();
  if (value === ":root" || value === "html" || value === "body") return BOOTSTRAP_SCOPE;
  if (value.startsWith("[data-bs-theme")) return `${BOOTSTRAP_SCOPE}${value}`;
  return `${BOOTSTRAP_SCOPE} ${value}`;
}

/** Bootstrap restyles bare elements and common class names, so it only applies inside .bs-scope. */
function scopeBootstrap() {
  return {
    postcssPlugin: "scope-bootstrap",
    Once(root: {
      source?: { input: { file?: string } };
      walkRules: (visit: (rule: never) => void) => void;
    }) {
      if (!root.source?.input.file?.includes("/bootstrap/dist/css/")) return;
      root.walkRules((rule: { parent?: { name?: string }; selectors: string[] }) => {
        if (rule.parent?.name?.endsWith("keyframes")) return;
        rule.selectors = rule.selectors.map(scopedSelector);
      });
    },
  } as Plugin & { postcssPlugin: string };
}

// The API the dev server forwards to. Override to preview against a second backend.
const api = process.env.ALGOVERSE_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  css: { postcss: { plugins: [scopeBootstrap() as never] } },
  server: {
    proxy: {
      "/api": api,
      "/auth": api,
    },
  },
});
