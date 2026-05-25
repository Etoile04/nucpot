import { describe, it, expect, vi, beforeEach } from 'vitest'
import { GET } from '@/app/api/potentials/[id]/route'
import { supabase } from '@/lib/supabase'

function mockChain(result: unknown) {
  const store: Record<string, ReturnType<typeof vi.fn>> = {}
  const mk = () => vi.fn(() => new Proxy(store, {
    get(t, p) {
      if (p === 'then') return (res: (v: unknown) => unknown, rej: (v: unknown) => unknown) => Promise.resolve(result).then(res, rej)
      if (!t[p as string]) t[p as string] = mk()
      return t[p as string]
    }
  }))
  return new Proxy(store, {
    get(t, p) {
      if (p === 'then') return (res: (v: unknown) => unknown, rej: (v: unknown) => unknown) => Promise.resolve(result).then(res, rej)
      if (!t[p as string]) t[p as string] = mk()
      return t[p as string]
    }
  })
}

vi.mock('@/lib/supabase', () => ({
  supabase: { from: vi.fn() },
  supabaseAdmin: null,
}))

describe('GET /api/potentials/[id]', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('returns a single potential by ID', async () => {
    const mockPotential = { id: 'abc-123', name: 'Test EAM', type: 'EAM', elements: ['U', 'Zr'], status: 'published' }
    vi.mocked(supabase.from).mockImplementation(() => mockChain({ data: mockPotential, error: null }))

    const req = new Request('http://localhost/api/potentials/abc-123')
    const res = await GET(req, { params: Promise.resolve({ id: 'abc-123' }) })
    const json = await res.json()

    expect(res.status).toBe(200)
    expect(json.id).toBe('abc-123')
    expect(json.name).toBe('Test EAM')
  })

  it('returns 404 for missing potential', async () => {
    vi.mocked(supabase.from).mockImplementation(() => mockChain({ data: null, error: { message: 'not found' } }))

    const req = new Request('http://localhost/api/potentials/nonexistent')
    const res = await GET(req, { params: Promise.resolve({ id: 'nonexistent' }) })
    const json = await res.json()

    expect(res.status).toBe(404)
    expect(json.error).toBe('Potential not found')
  })

  it('returns 404 when data is null without error', async () => {
    vi.mocked(supabase.from).mockImplementation(() => mockChain({ data: null, error: null }))

    const req = new Request('http://localhost/api/potentials/missing')
    const res = await GET(req, { params: Promise.resolve({ id: 'missing' }) })

    expect(res.status).toBe(404)
  })
})
