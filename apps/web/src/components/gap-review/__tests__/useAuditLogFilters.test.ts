/**
 * Tests for useAuditLogFilters — URL param sync logic.
 *
 * We test the pure functions (parseFilters, buildParams, parseCursor)
 * directly since they contain all the logic. The hook itself
 * just wraps useSearchParams + history.replaceState.
 */

import { describe, it, expect } from 'vitest'
import { parseFilters, buildParams, parseCursor } from '../useAuditLogFilters'
import type { AuditLogFilters } from '@/lib/reference-gaps/types'

// ── parseCursor ────────────────────────────────────────────────

describe('parseCursor', () => {
  it('returns undefined when no param', () => {
    expect(parseCursor(new URLSearchParams())).toBeUndefined()
  })

  it('parses a valid cursor', () => {
    expect(parseCursor(new URLSearchParams('cursor=abc123'))).toBe('abc123')
  })

  it('returns undefined for empty cursor', () => {
    expect(parseCursor(new URLSearchParams('cursor='))).toBeUndefined()
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
  it('omits cursor when undefined (first page)', () => {
    const result = buildParams({}, undefined)
    expect(result.has('cursor')).toBe(false)
  })

  it('includes cursor when provided', () => {
    const result = buildParams({}, 'abc123')
    expect(result.get('cursor')).toBe('abc123')
  })

  it('sets all filter params', () => {
    const filters: AuditLogFilters = {
      reviewer_id: 'user1',
      date_from: '2026-08-01',
      date_to: '2026-08-25',
      decision: 'accepted',
      entity_name: 'UO2',
    }
    const result = buildParams(filters, undefined)
    expect(result.get('reviewer_id')).toBe('user1')
    expect(result.get('date_from')).toBe('2026-08-01')
    expect(result.get('date_to')).toBe('2026-08-25')
    expect(result.get('decision')).toBe('accepted')
    expect(result.get('entity_name')).toBe('UO2')
  })

  it('removes undefined filter values', () => {
    const result = buildParams({}, undefined)
    expect(result.has('reviewer_id')).toBe(false)
    expect(result.has('decision')).toBe(false)
  })

  it('round-trips: buildParams → parseFilters preserves data', () => {
    const filters: AuditLogFilters = {
      reviewer_id: 'user1',
      date_from: '2026-08-01',
      decision: 'accepted',
    }
    const built = buildParams(filters, 'cur-xyz')
    expect(built.get('cursor')).toBe('cur-xyz')
    const parsed = parseFilters(built)
    expect(parsed).toEqual(filters)
  })

  it('round-trips: buildParams → parseCursor preserves cursor', () => {
    const built = buildParams({}, 'my-cursor')
    expect(parseCursor(built)).toBe('my-cursor')
  })

  it('round-trips without cursor: parseCursor returns undefined', () => {
    const built = buildParams({}, undefined)
    expect(parseCursor(built)).toBeUndefined()
  })
})
