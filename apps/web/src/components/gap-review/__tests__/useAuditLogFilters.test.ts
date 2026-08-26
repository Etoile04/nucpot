/**
 * Tests for useAuditLogFilters — URL param sync logic.
 *
 * We test the pure functions (parseFilters, buildParams, parsePage)
 * directly since they contain all the logic. The hook itself
 * just wraps useSearchParams + history.replaceState.
 */

import { describe, it, expect } from 'vitest'
import { parseFilters, buildParams, parsePage } from '../useAuditLogFilters'
import type { AuditLogFilters } from '@/lib/reference-gaps/types'

// ── parsePage ──────────────────────────────────────────────────────

describe('parsePage', () => {
  it('defaults to 1 when no param', () => {
    expect(parsePage(new URLSearchParams())).toBe(1)
  })

  it('parses a valid page number', () => {
    expect(parsePage(new URLSearchParams('page=3'))).toBe(3)
  })

  it('clamps zero to 1', () => {
    expect(parsePage(new URLSearchParams('page=0'))).toBe(1)
  })

  it('clamps negative to 1', () => {
    expect(parsePage(new URLSearchParams('page=-5'))).toBe(1)
  })

  it('treats NaN as 1', () => {
    expect(parsePage(new URLSearchParams('page=abc'))).toBe(1)
  })
})

// ── parseFilters ───────────────────────────────────────────────────

describe('parseFilters', () => {
  it('returns empty filters for empty params', () => {
    const result = parseFilters(new URLSearchParams())
    expect(result).toEqual({})
  })

  it('parses all 5 filter params', () => {
    const params = new URLSearchParams([
      ['reviewer_id', 'user1'],
      ['date_from', '2026-08-01'],
      ['date_to', '2026-08-25'],
      ['decision', 'accepted'],
      ['entity_name', 'Uranium'],
    ])
    const result = parseFilters(params)
    expect(result).toEqual({
      reviewer_id: 'user1',
      date_from: '2026-08-01',
      date_to: '2026-08-25',
      decision: 'accepted',
      entity_name: 'Uranium',
    })
  })

  it('ignores invalid decision values', () => {
    const params = new URLSearchParams('decision=invalid')
    const result = parseFilters(params)
    expect(result.decision).toBeUndefined()
  })

  it('accepts all valid decision values', () => {
    for (const d of ['accepted', 'rejected', 'deferred'] as const) {
      const params = new URLSearchParams(`decision=${d}`)
      expect(parseFilters(params).decision).toBe(d)
    }
  })
})

// ── buildParams ────────────────────────────────────────────────────

describe('buildParams', () => {
  it('includes page only when > 1', () => {
    const base = new URLSearchParams()
    const p1 = buildParams(base, {}, 1)
    expect(p1.has('page')).toBe(false)

    const p2 = buildParams(base, {}, 2)
    expect(p2.get('page')).toBe('2')
  })

  it('sets all filter params', () => {
    const filters: AuditLogFilters = {
      reviewer_id: 'user1',
      date_from: '2026-08-01',
      date_to: '2026-08-25',
      decision: 'accepted',
      entity_name: 'UO2',
    }
    const result = buildParams(new URLSearchParams(), filters, 1)
    expect(result.get('reviewer_id')).toBe('user1')
    expect(result.get('date_from')).toBe('2026-08-01')
    expect(result.get('date_to')).toBe('2026-08-25')
    expect(result.get('decision')).toBe('accepted')
    expect(result.get('entity_name')).toBe('UO2')
  })

  it('removes undefined filter values', () => {
    const prev = new URLSearchParams('reviewer_id=old&decision=rejected')
    const result = buildParams(prev, {}, 1)
    expect(result.has('reviewer_id')).toBe(false)
    expect(result.has('decision')).toBe(false)
  })

  it('round-trips: buildParams → parseFilters preserves data', () => {
    const filters: AuditLogFilters = {
      reviewer_id: 'user1',
      date_from: '2026-08-01',
      decision: 'accepted',
    }
    const built = buildParams(new URLSearchParams(), filters, 3)
    expect(built.get('page')).toBe('3')
    const parsed = parseFilters(built)
    expect(parsed).toEqual(filters)
  })
})
