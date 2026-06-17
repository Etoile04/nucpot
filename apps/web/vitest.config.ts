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
    include: ["src/**/*.test.{ts,tsx}"],
    env: {
      BLOG_CONTENT_DIR: path.join(__dirname, "content", "blog", "__test__"),
    },
  },
  resolve: {
    conditions: ["development"],
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
})
