import { describe, it, expect, vi, beforeEach } from 'vitest'
import { GET } from '@/app/api/stats/route'
import { supabase } from '@/lib/supabase'

/**
 * Creates a chainable mock that resolves to `result` when awaited.
 * Supports: .from().select().eq().order().limit().range().overlaps().textSearch().contains().single().maybeSingle()
 */
function mockChain(result: unknown) {
  const chain: Record<string, ReturnType<typeof vi.fn>> = {}
  const self: Record<string, ReturnType<typeof vi.fn>> = {}

  const method = () => vi.fn((..._args: unknown[]) => new Proxy(self, {
    get(target, prop) {
      if (prop === 'then') {
        // Make thenable
        return (resolve: (v: unknown) => unknown, reject: (v: unknown) => unknown) =>
          Promise.resolve(result).then(resolve, reject)
      }
      if (!target[prop as string]) {
        target[prop as string] = method()
      }
      return target[prop as string]
    }
  }))

  return new Proxy(self, {
    get(target, prop) {
      if (prop === 'then') {
        return (resolve: (v: unknown) => unknown, reject: (v: unknown) => unknown) =>
          Promise.resolve(result).then(resolve, reject)
      }
      if (!target[prop as string]) {
        target[prop as string] = method()
      }
      return target[prop as string]
    }
  })
}

vi.mock('@/lib/supabase', () => ({
  supabase: {
    from: vi.fn(),
  },
  supabaseAdmin: null,
}))

describe('GET /api/stats', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('returns correct stats structure', async () => {
    const mockRecentData = [
      { id: '1', name: 'test', display_name: 'Test', type: 'EAM', elements: ['U'], system_name: 'U', updated_at: '2026-01-01' },
    ]

    const responses = [
      { count: 42, error: null },                                    // head count
      { data: [{ type: 'EAM' }, { type: 'MEAM' }, { type: 'EAM' }], error: null },  // types
      { data: [{ elements: ['U', 'Zr'] }, { elements: ['U', 'Mo'] }], error: null }, // elements
      { data: mockRecentData, error: null },                          // recent
    ]
    let idx = 0
    vi.mocked(supabase.from).mockImplementation(() => mockChain(responses[idx++]))

    const res = await GET()
    const json = await res.json()

    expect(json.totalPotentials).toBe(42)
    expect(json.totalTypes).toBe(2)
    expect(json.totalElements).toBe(3)
    expect(json.types).toEqual(['EAM', 'MEAM'])
    expect(json.elements).toEqual(['Mo', 'U', 'Zr'])
    expect(json.recent).toEqual(mockRecentData)
  })

  it('handles empty database', async () => {
    const responses = [
      { count: 0, error: null },
      { data: null, error: null },
      { data: null, error: null },
      { data: null, error: null },
    ]
    let idx = 0
    vi.mocked(supabase.from).mockImplementation(() => mockChain(responses[idx++]))

    const res = await GET()
    const json = await res.json()

    expect(json.totalPotentials).toBe(0)
    expect(json.totalTypes).toBe(0)
    expect(json.totalElements).toBe(0)
    expect(json.types).toEqual([])
    expect(json.elements).toEqual([])
    expect(json.recent).toEqual([])
  })
})
