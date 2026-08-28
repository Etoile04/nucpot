/**
 * API client for bulk gap decisions.
 *
 * POST /api/gap/decisions/bulk — all-or-nothing transaction.
 * If any item fails, the entire batch rolls back.
 */

import type {
  BulkDecisionRequest,
  BulkDecisionResponse,
  GapDecision,
  GapCandidate,
} from './types'

// ─── Internal ─────────────────────────────────────────────────────────

interface ApiEnvelope<T> {
  readonly success: boolean
  readonly data?: T
  readonly error?: string
}

// ─── Public API ───────────────────────────────────────────────────────

/**
 * Submit a batch of gap decisions. All-or-nothing: if any item fails,
 * the backend rolls back the entire transaction.
 */
export async function submitBulkDecisions(
  payload: BulkDecisionRequest,
): Promise<BulkDecisionResponse> {
  const response = await fetch('/api/gap/decisions/bulk', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    throw new Error(`Bulk decision failed: ${response.statusText}`)
  }

  const envelope: ApiEnvelope<BulkDecisionResponse> = await response.json()

  if (!envelope.success || !envelope.data) {
    throw new Error(envelope.error ?? 'Bulk decision failed')
  }

  // Check for partial failures — backend should rollback, but verify
  const failed = envelope.data.results.filter((r) => r.error)
  if (failed.length > 0) {
    throw new Error(
      `${failed.length} item(s) failed: ${failed.map((f) => f.error).join('; ')}`,
    )
  }

  return envelope.data
}

// ─── Helpers ───────────────────────────────────────────────────────────

/**
 * Build a BulkDecisionRequest from selected candidates and a decision type.
 */
export function buildDecisionPayload(
  candidates: ReadonlyArray<GapCandidate>,
  decision: GapDecision,
): BulkDecisionRequest {
  return {
    decisions: candidates.map((c) => ({
      candidate_id: c.candidate_id,
      decision,
    })),
  }
}

/**
 * Filter candidates whose confidence meets or exceeds the threshold.
 */
export function filterByConfidence(
  candidates: ReadonlyArray<GapCandidate>,
  threshold: number,
): ReadonlyArray<GapCandidate> {
  return candidates.filter((c) => c.confidence >= threshold)
}
