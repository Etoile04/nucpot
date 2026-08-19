import { describe, it, expect } from 'vitest'
import { detailToMessage } from '../admin-api-utils'

describe('detailToMessage', () => {
  it('returns string detail as-is', () => {
    expect(detailToMessage('Email already registered')).toBe('Email already registered')
  })

  it('extracts msg from FastAPI 422 array', () => {
    const detail = [
      { loc: ['body', 'email'], msg: 'value is not a valid email address', type: 'value_error.email' },
    ]
    expect(detailToMessage(detail)).toBe('value is not a valid email address')
  })

  it('joins multiple 422 validation messages', () => {
    const detail = [
      { loc: ['body', 'email'], msg: 'invalid email', type: 'value_error' },
      { loc: ['body', 'password'], msg: 'too short', type: 'value_error' },
    ]
    expect(detailToMessage(detail)).toBe('invalid email; too short')
  })

  it('skips items without msg field', () => {
    const detail = [
      { loc: ['body', 'email'], msg: 'bad email', type: 'value_error' },
      { loc: ['body', 'x'] },
    ]
    expect(detailToMessage(detail)).toBe('bad email')
  })

  it('returns null when all items lack msg', () => {
    const detail = [{ loc: ['body', 'x'] }]
    expect(detailToMessage(detail)).toBeNull()
  })

  it('returns null for empty array', () => {
    expect(detailToMessage([])).toBeNull()
  })

  it('returns null for non-string/non-array (e.g. object)', () => {
    expect(detailToMessage({ error: 'something' })).toBeNull()
  })

  it('returns null for null/undefined', () => {
    expect(detailToMessage(null)).toBeNull()
    expect(detailToMessage(undefined)).toBeNull()
  })
})
