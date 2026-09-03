/**
 * Client for the internal feature-flag service (NFM-4180).
 *
 * Backend: FastAPI `/api/v1/feature-flags/{key}/evaluate?subject=<id>`
 * (migration 071, table `feature_flags`). Values are read at request
 * time, so operators toggle flags via the admin PUT endpoint with no
 * redeploy, and `rollout_percentage` gives deterministic per-browser
 * cohort bucketing (e.g. a 10% canary).
 *
 * Fails closed: any fetch/parse error resolves to `value: false`, so a
 * down backend can never accidentally widen a rollout. The last
 * successful evaluation is cached module-side so sync readers
 * (`isDataLossNoticeEnabled`) keep working between refreshes.
 */

import { request } from "@/lib/api-client"

/** Backend `FeatureFlagEvaluation` schema (snake_case, matches pydantic). */
export interface FlagEvaluation {
  readonly key: string
  readonly enabled: boolean
  readonly rollout_percentage: number
  readonly value: boolean
  readonly bucket: number
}

interface ApiEnvelope {
  readonly success: boolean
  readonly data: FlagEvaluation | null
}

const SUBJECT_STORAGE_KEY = "nfm:flag-subject"
const cache = new Map<string, FlagEvaluation>()

function failedEvaluation(key: string): FlagEvaluation {
  return {
    key,
    enabled: false,
    rollout_percentage: 0,
    value: false,
    bucket: 0,
  }
}

/**
 * Stable anonymous subject id for cohort bucketing.
 *
 * A random UUID persisted in localStorage — stable per browser, carries
 * no identity. Empty string on the server / when storage is unavailable
 * (evaluation is client-only, so this is safe).
 */
export function getFlagSubjectId(): string {
  if (typeof window === "undefined") return ""
  try {
    const existing = window.localStorage.getItem(SUBJECT_STORAGE_KEY)
    if (existing) return existing

    const generated =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `anon-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`
    window.localStorage.setItem(SUBJECT_STORAGE_KEY, generated)
    return generated
  } catch {
    // Private mode / storage disabled — non-stable id degrades bucketing
    // to per-visit, never correctness of the enabled/off master switch.
    return `ephemeral-${Math.random().toString(36).slice(2)}`
  }
}

/** Last known evaluation for a flag; `undefined` before the first fetch. */
export function getCachedEvaluation(key: string): FlagEvaluation | undefined {
  return cache.get(key)
}

/**
 * Fetch and cache the evaluation for one flag. Always resolves —
 * never rejects — with `value: false` on any failure (fail closed).
 */
export async function evaluateFlag(key: string): Promise<FlagEvaluation> {
  try {
    const subject = getFlagSubjectId()
    const envelope = await request<ApiEnvelope>(
      `/api/v1/feature-flags/${encodeURIComponent(key)}/evaluate` +
        `?subject=${encodeURIComponent(subject)}`,
    )
    if (!envelope?.success || !envelope.data) {
      return failedEvaluation(key)
    }
    const frozen = Object.freeze({ ...envelope.data })
    cache.set(key, frozen)
    return frozen
  } catch (error) {
    // Network error / 404 unknown flag / 5xx — log for debugging, stay off.
    console.error(`[flag-service] evaluation failed for ${key}:`, error)
    return failedEvaluation(key)
  }
}

/** Test-only: reset the module cache between cases. */
export function __resetFlagCacheForTests(): void {
  cache.clear()
}
