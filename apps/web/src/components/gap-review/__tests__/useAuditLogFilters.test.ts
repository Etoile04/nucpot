/**
 * Tests for useAuditLogFilters — URL param sync logic.
 *
 * We test the pure functions (parseFilters, buildParams, parsePage)
 * directly since they contain all the logic. The hook itself
 * just wraps useSearchParams + history.replaceState.
 */

import { describe, it, expect } from 'vitest'
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

// ── parseCursor ───────────────────────────────────────────────────

describe('parseCursor', () => {
  it('returns empty for no params', () => {
    expect(parseCursor(new URLSearchParams())).toEqual({})
  })

  it('parses after_cursor', () => {
    const params = new URLSearchParams('after_cursor=abc123')
    expect(parseCursor(params)).toEqual({ after: 'abc123' })
  })

  it('parses before_cursor', () => {
    const params = new URLSearchParams('before_cursor=xyz789')
    expect(parseCursor(params)).toEqual({ before: 'xyz789' })
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
  it('sets after_cursor in params', () => {
    const result = buildParams(new URLSearchParams(), {}, { after: 'abc' })
    expect(result.get('after_cursor')).toBe('abc')
    expect(result.has('before_cursor')).toBe(false)
  })

  it('sets before_cursor in params', () => {
    const result = buildParams(new URLSearchParams(), {}, { before: 'xyz' })
    expect(result.get('before_cursor')).toBe('xyz')
    expect(result.has('after_cursor')).toBe(false)
  })

  it('omits cursor params for first page', () => {
    const result = buildParams(new URLSearchParams(), {}, {})
    expect(result.has('after_cursor')).toBe(false)
    expect(result.has('before_cursor')).toBe(false)
  })

  it('strips legacy page param', () => {
    const prev = new URLSearchParams('page=3')
    const result = buildParams(prev, {}, { after: 'abc' })
    expect(result.has('page')).toBe(false)
    expect(result.get('after_cursor')).toBe('abc')
  })

  it('sets all filter params', () => {
    const filters: AuditLogFilters = {
      reviewer_id: 'user1',
      date_from: '2026-08-01',
      date_to: '2026-08-25',
      decision: 'accepted',
      entity_name: 'UO2',
    }
    const result = buildParams(new URLSearchParams(), filters, {})
    expect(result.get('reviewer_id')).toBe('user1')
    expect(result.get('date_from')).toBe('2026-08-01')
    expect(result.get('date_to')).toBe('2026-08-25')
    expect(result.get('decision')).toBe('accepted')
    expect(result.get('entity_name')).toBe('UO2')
  })

  it('removes undefined filter values', () => {
    const prev = new URLSearchParams('reviewer_id=old&decision=rejected')
    const result = buildParams(prev, {}, {})
    expect(result.has('reviewer_id')).toBe(false)
    expect(result.has('decision')).toBe(false)
  })

  it('round-trips: buildParams → parseFilters + parseCursor', () => {
    const filters: AuditLogFilters = {
      reviewer_id: 'user1',
      date_from: '2026-08-01',
      decision: 'accepted',
    }
    const cursor = { after: 'cursorABC' }
    const built = buildParams(new URLSearchParams(), filters, cursor)
    expect(built.get('after_cursor')).toBe('cursorABC')
    expect(built.has('page')).toBe(false)
    const parsed = parseFilters(built)
    expect(parsed).toEqual(filters)
    const parsedCursor = parseCursor(built)
    expect(parsedCursor).toEqual(cursor)
  })
})
