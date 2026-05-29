import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mockSupabaseChain } from '../setup'
import { GET } from '@/app/api/potentials/route'
import { supabase } from '@/lib/supabase'



vi.mock('@/lib/supabase', () => ({
  supabase: { from: vi.fn() },
  supabaseAdmin: null,
}))

describe('GET /api/potentials', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('returns paginated results with default params', async () => {
    const mockData = [{ id: '1', name: 'Test Potential', type: 'EAM', elements: ['U'] }]
    const result = { data: mockData, count: 1, error: null }
    vi.mocked(supabase.from).mockImplementation(() => mockSupabaseChain(result))

    const req = new Request('http://localhost/api/potentials')
    const res = await GET(req)
    const json = await res.json()

    expect(json.potentials).toEqual(mockData)
    expect(json.total).toBe(1)
    expect(json.page).toBe(1)
    expect(json.limit).toBe(20)
    expect(json.totalPages).toBe(1)
  })

  it('handles pagination with page and limit params', async () => {
    const result = { data: [], count: 50, error: null }
    vi.mocked(supabase.from).mockImplementation(() => mockSupabaseChain(result))

    const req = new Request('http://localhost/api/potentials?page=3&limit=10')
    const res = await GET(req)
    const json = await res.json()

    expect(json.page).toBe(3)
    expect(json.limit).toBe(10)
    expect(json.totalPages).toBe(5)
  })

  it('filters by type', async () => {
    const result = { data: [], count: 0, error: null }
    vi.mocked(supabase.from).mockImplementation(() => mockSupabaseChain(result))

    const req = new Request('http://localhost/api/potentials?type=EAM')
    const res = await GET(req)
    expect(res.status).toBe(200)
  })

  it('filters by elements', async () => {
    const result = { data: [], count: 0, error: null }
    vi.mocked(supabase.from).mockImplementation(() => mockSupabaseChain(result))

    const req = new Request('http://localhost/api/potentials?elements=U,Zr')
    const res = await GET(req)
    expect(res.status).toBe(200)
  })

  it('handles search query', async () => {
    const result = { data: [], count: 0, error: null }
    vi.mocked(supabase.from).mockImplementation(() => mockSupabaseChain(result))

    const req = new Request('http://localhost/api/potentials?q=uranium')
    const res = await GET(req)
    expect(res.status).toBe(200)
  })

  it('returns 500 on database error', async () => {
    const result = { data: null, count: 0, error: { message: 'DB error' } }
    vi.mocked(supabase.from).mockImplementation(() => mockSupabaseChain(result))

    const req = new Request('http://localhost/api/potentials')
    const res = await GET(req)
    const json = await res.json()

    expect(res.status).toBe(500)
    expect(json.error).toBe('DB error')
  })
})
