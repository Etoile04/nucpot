import { dirname } from "path"
import { fileURLToPath } from "url"
import coreWebVitals from "eslint-config-next/core-web-vitals"
import typescript from "eslint-config-next/typescript"

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

// eslint-config-next 16 ships flat-config arrays natively (no FlatCompat —
// the legacy converter crashes on the flat entries with eslint 10's
// config validation, "Converting circular structure to JSON" on the
// plugins object).
const eslintConfig = [
  {
    ignores: [
      ".next/**",
      "next-env.d.ts",
      "public/**",
    ],
  },
  ...coreWebVitals,
  ...typescript,
  {
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      // eslint-config-next 16 ships the React Compiler-powered react-hooks
      // rules (set-state-in-effect, refs, immutability,
      // preserve-manual-memoization, use-memo) at "error" by default.
      // The existing codebase predates the Compiler's strictness: 61
      // violations across 43 source files, none auto-fixable. Downgrade to
      // "warn" so the upgrade lands without a 43-file refactor; re-enable
      // per-rule as the patterns are migrated.
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/refs": "warn",
      "react-hooks/preserve-manual-memoization": "warn",
      "react-hooks/immutability": "warn",
      "react-hooks/use-memo": "warn",
    },
  },
]

export default eslintConfig
