/**
 * Feature flag check for DataLossNotice.
 *
 * Spec §6.1 — default OFF in dev, ON in staging/prod. The frontend reads
 * the flag at render time; when OFF the component returns `null` so the
 * existing source citation renders unchanged. The flag is the iteration
 * lever for §4.1 copy ratification.
 *
 * This module does NOT introduce a feature-flag dependency. The project
 * does not yet have one (NFM-4177 ships SRE's flag-flip plan separately
 * for staging/prod). Until then we resolve from a runtime-only override
 * set by `<DataLossNoticeProvider>` and a build-time env override:
 *
 *   NEXT_PUBLIC_DATA_LOSS_NOTICE=on   → enable in dev
 *
 * Default = OFF. The SRE owner flips the actual rollout behind
 * `feature_flags.DATA_LOSS_NOTICE` once the backend's `attribution`
 * field ships (NFM-4159).
 */

const FLAG_NAME = "DATA_LOSS_NOTICE"

export interface FeatureFlagSnapshot {
  /** True iff the notice should render at all. */
  readonly enabled: boolean
  /** Source of the resolved value, for observability. */
  readonly source: "env" | "provider" | "default-off"
}

let runtimeOverride: boolean | null = null

/**
 * Set the runtime override. Called by `<DataLossNoticeProvider>` so a
 * parent (e.g. an admin debug switch, a route-level config) can flip
 * the flag without redeploying.
 */
export function setRuntimeOverride(value: boolean | null): void {
  runtimeOverride = value
}

/**
 * Resolve the current flag value. Resolution order:
 *
 *   1. Runtime override (set via `setRuntimeOverride`).
 *   2. Build-time env: `NEXT_PUBLIC_DATA_LOSS_NOTICE=on`.
 *   3. Default = OFF.
 */
export function resolveFeatureFlag(): FeatureFlagSnapshot {
  if (runtimeOverride !== null) {
    return { enabled: runtimeOverride, source: "provider" }
  }
  const envValue = readBuildTimeEnv()
  if (envValue === true) {
    return { enabled: true, source: "env" }
  }
  return { enabled: false, source: "default-off" }
}

function readBuildTimeEnv(): boolean | null {
  // Next.js exposes `NEXT_PUBLIC_*` vars to the browser by inlining the
  // STATIC expression `process.env.NEXT_PUBLIC_*` at compile time
  // (DefinePlugin). The previous dynamic `(globalThis as any)?.process
  // ?.env?.NEXT_PUBLIC_...` lookup escaped that inlining AND no
  // `process` global exists in the browser bundle, so the env path was
  // dead code in-browser — the flag could only ever be enabled via the
  // runtime override (NFM-4204). The static reference below is
  // load-bearing for the NFM-4177 rollout: without it, flipping
  // NEXT_PUBLIC_DATA_LOSS_NOTICE at build time would do nothing in prod.
  // The absence of the var still means OFF: when undefined at compile
  // time the expression inlines to `undefined` and we return null.
  const env = process.env.NEXT_PUBLIC_DATA_LOSS_NOTICE
  if (typeof env !== "string") return null
  const normalized = env.trim().toLowerCase()
  if (normalized === "on" || normalized === "true" || normalized === "1") {
    return true
  }
  if (normalized === "off" || normalized === "false" || normalized === "0") {
    return false
  }
  return null
}

export const FEATURE_FLAG_NAME = FLAG_NAME