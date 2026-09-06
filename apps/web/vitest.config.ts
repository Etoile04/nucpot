import { defineConfig } from "vitest/config"
import react from "@vitejs/plugin-react"
import path from "path"

export default defineConfig({
  plugins: [react()],
  // React 18.3's CJS entry selects its dev vs production build from
  // `process.env.NODE_ENV === 'production'`. CI/deploy environments set
  // NODE_ENV=production, which loads react.production.min.js and breaks
  // testing-library's `act()`. Force the development build for tests so the
  // first component-render test (and all future ones) work regardless of the
  // ambient NODE_ENV. React has no `development` export condition, so the
  // `resolve.conditions` below does not cover this.
  define: {
    "process.env.NODE_ENV": JSON.stringify("development"),
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // Default vitest testTimeout (5000ms) is shorter than the multi-waitFor
    // budget of any test that drives a TanStack Query mutation through the
    // UI (each `waitFor` is up to 10s). Bump the per-test budget so the
    // suite can complete the actual work — the inner `waitFor` timeouts
    // still bound each individual assertion.
    testTimeout: 30000,
    // hookTimeout stays at the 10s default unless raised here; suites that
    // dynamically import heavy component modules inside `beforeAll` (e.g.
    // v4-extraction) can exceed it under full-suite parallel transform load
    // and fail before running a single test. Match the per-test budget.
    hookTimeout: 30000,
    include: [
      "src/**/*.test.{ts,tsx}",
      "__tests__/**/*.test.{ts,tsx}",
    ],
    env: {
      BLOG_CONTENT_DIR: path.join(__dirname, "content", "blog", "__test__"),
    },
    // Workaround for CI-only flakes (e.g. PR #679 NodeDetailContent#14 Retry).
    // Reproduced 10/10 PASS locally; the race is suspected to be in React 18
    // microtask ordering between the node fetch and the relations fetch
    // (gated on state.status === 'success'). A retry gives one extra attempt
    // for the race to settle. **Workaround, not a fix** — see issue #685 for
    // root-cause analysis. Keep retry=1 to minimise noise.
    retry: 1,
  },
  resolve: {
    conditions: ["development"],
    alias: {
      "@": path.resolve(__dirname, "./src"),
      // Mirror the tsconfig path so tests resolve the platform package
      // at its TS source, same as the Next.js build does (NFM-4179).
      "@nfm-db/shared": path.resolve(__dirname, "../../packages/shared/src"),
    },
  },
})
