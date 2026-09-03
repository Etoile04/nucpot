/**
 * Feature flag check for DataLossNotice.
 *
 * Spec §6.1 — default OFF. The frontend reads the flag at render time;
 * when OFF the component returns `null` so the existing source citation
 * renders unchanged. The flag is the iteration lever for §4.1 copy
 * ratification.
 *
 * NFM-4146-FU2 / NFM-4180: the source of truth is now the backend
 * `feature_flags` table (migration 081), evaluated per-request at
 * `/api/v1/feature-flags/{key}/evaluate` with deterministic per-browser
 * cohort bucketing (`rollout_percentage`). This replaces the build-time
 * env-var gate shipped by NFM-4146 and patched by NFM-4207 (the
 * `NEXT_PUBLIC_`-prefixed DataLossNotice var) — that env var is no
 * longer read anywhere: an
 * operator flips the flag via `PUT /api/v1/feature-flags/DATA_LOSS_NOTICE`
 * and every client picks the change up within one refresh interval
 * (60s, see `<DataLossNoticeGate>`) with no redeploy.
 *
 * Resolution order:
 *
 *   1. Runtime override (set via `setRuntimeOverride` — admin debug
 *      switch + integration tests).
 *   2. Flag-service cache: the last successful evaluation fetched by
 *      `<DataLossNoticeGate>` (mount + 60s re-check).
 *   3. Default = OFF (fail closed — a down backend can never widen a
 *      rollout).
 */

import { evaluateFlag, getCachedEvaluation } from "@/lib/flag-service"

const FLAG_NAME = "DATA_LOSS_NOTICE"

export interface FeatureFlagSnapshot {
  /** True iff the notice should render at all. */
  readonly enabled: boolean
  /** Source of the resolved value, for observability. */
  readonly source: "provider" | "flag-service" | "default-off"
}

let runtimeOverride: boolean | null = null

/**
 * Set the runtime override. Called by `<DataLossNoticeProvider>` so a
 * parent (e.g. an admin debug switch, a route-level config) can flip
 * the flag for the current session.
 */
export function setRuntimeOverride(value: boolean | null): void {
  runtimeOverride = value
}

/**
 * Resolve the current flag value. Synchronous by design: it reads the
 * flag-service cache filled by `<DataLossNoticeGate>`, so the
 * authoritative (re)fetch lives in exactly one place.
 */
export function resolveFeatureFlag(): FeatureFlagSnapshot {
  if (runtimeOverride !== null) {
    return { enabled: runtimeOverride, source: "provider" }
  }
  const evaluation = getCachedEvaluation(FLAG_NAME)
  if (evaluation !== undefined) {
    return { enabled: evaluation.value, source: "flag-service" }
  }
  return { enabled: false, source: "default-off" }
}

/**
 * Fetch the current evaluation from the flag service and update the
 * cache read by `resolveFeatureFlag()`. Called by
 * `<DataLossNoticeGate>` on mount and on its 60s refresh interval;
 * resolves to the refreshed value (fail-closed `false` on any error —
 * see `lib/flag-service.ts`).
 */
export async function refreshFeatureFlag(): Promise<boolean> {
  const evaluation = await evaluateFlag(FLAG_NAME)
  return evaluation.value
}

export const FEATURE_FLAG_NAME = FLAG_NAME
