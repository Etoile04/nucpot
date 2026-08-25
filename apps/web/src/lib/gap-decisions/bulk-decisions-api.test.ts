import { describe, it, expect, vi, beforeEach } from 'vitest'
import { buildDecisionPayload, filterByConfidence } from './bulk-decisions-api'
import { submitBulkDecisions } from './bulk-decisions-api'
import type { GapCandidate } from './types'

// ── Fixtures ──────────────────────────────────────────────────────────

const CANDIDATE_A: GapCandidate = { candidate_id: 'c-1', confidence: 0.92, label: 'UO2 density' }
const CANDIDATE_B: GapCandidate = { candidate_id: 'c-2', confidence: 0.65, label: 'UO2 thermal' }
const CANDIDATE_C: GapCandidate = { candidate_id: 'c-3', confidence: 0.45, label: 'UO2 melting' }
const ALL_CANDIDATES: ReadonlyArray<GapCandidate> = [CANDIDATE_A, CANDIDATE_B, CANDIDATE_C]

// ── buildDecisionPayload ──────────────────────────────────────────────

describe('buildDecisionPayload', () => {
  it('builds correct payload from candidates', () => {
    const result = buildDecisionPayload([CANDIDATE_A, CANDIDATE_B], 'accepted')
    expect(result).toEqual({
      decisions: [
        { candidate_id: 'c-1', decision: 'accepted' },
        { candidate_id: 'c-2', decision: 'accepted' },
      ],
    })
  })

  it('returns empty decisions for empty input', () => {
    const result = buildDecisionPayload([], 'rejected')
    expect(result).toEqual({ decisions: [] })
  })

  it('preserves decision type', () => {
    expect(buildDecisionPayload([CANDIDATE_A], 'deferred').decisions[0]!.decision).toBe('deferred')
    expect(buildDecisionPayload([CANDIDATE_A], 'rejected').decisions[0]!.decision).toBe('rejected')
  })
})

// ── filterByConfidence ───────────────────────────────────────────────

describe('filterByConfidence', () => {
  it('filters candidates above threshold', () => {
    const result = filterByConfidence(ALL_CANDIDATES, 0.7)
    expect(result).toEqual([CANDIDATE_A])
    expect(result).toHaveLength(1)
  })

  it('includes candidates at exact threshold', () => {
    const result = filterByConfidence(ALL_CANDIDATES, 0.65)
    expect(result).toHaveLength(2)
  })

  it('returns all when threshold is 0', () => {
    const result = filterByConfidence(ALL_CANDIDATES, 0)
    expect(result).toHaveLength(3)
  })

  it('returns none when threshold is 1', () => {
    const result = filterByConfidence(ALL_CANDIDATES, 1.0)
    expect(result).toHaveLength(0)
  })
})

// ── submitBulkDecisions ───────────────────────────────────────────────

describe('submitBulkDecisions', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('posts to /api/gap/decisions/bulk and returns response', async () => {
    const mockResponse = {
      results: [
        { candidate_id: 'c-1', decision: 'accepted', decided_at: '2024-06-15T10:30:00Z', reviewer_id: 'user-1' },
      ],
    }
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ success: true, data: mockResponse }),
    } as Response)

    const payload = buildDecisionPayload([CANDIDATE_A], 'accepted')
    const result = await submitBulkDecisions(payload)

    expect(fetch).toHaveBeenCalledWith('/api/gap/decisions/bulk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    expect(result.results).toHaveLength(1)
  })

  it('throws on non-ok response', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      statusText: 'Internal Server Error',
    } as Response)

    await expect(submitBulkDecisions(buildDecisionPayload([CANDIDATE_A], 'accepted'))).rejects.toThrow('Bulk decision failed')
  })

  it('throws when envelope success is false', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ success: false, error: 'Validation error' }),
    } as Response)

    await expect(submitBulkDecisions(buildDecisionPayload([CANDIDATE_A], 'accepted'))).rejects.toThrow('Validation error')
  })

  it('throws when any result item has an error (rollback check)', async () => {
    const mockResponse = {
      results: [
        { candidate_id: 'c-1', decision: 'accepted', decided_at: '2024-06-15T10:30:00Z', reviewer_id: 'user-1' },
        { candidate_id: 'c-2', decision: 'accepted', decided_at: '2024-06-15T10:30:00Z', reviewer_id: 'user-1', error: 'Constraint violation' },
      ],
    }
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ success: true, data: mockResponse }),
    } as Response)

    await expect(submitBulkDecisions(buildDecisionPayload([CANDIDATE_A, CANDIDATE_B], 'accepted'))).rejects.toThrow('1 item(s) failed')
  })
})
