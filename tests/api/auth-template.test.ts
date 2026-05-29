import { describe, it, expect, vi, beforeEach } from 'vitest'
import { GET } from '@/app/api/auth/template/route'
import { supabase } from '@/lib/supabase'
import { mockSupabaseChain } from '../setup'

vi.mock('@/lib/supabase', () => ({
  supabase: { from: vi.fn() },
  supabaseAdmin: null,
}))

describe('GET /api/auth/template', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('generates Chinese HTML by default', async () => {
    vi.mocked(supabase.from).mockImplementation(() =>
      mockSupabaseChain({ data: null, error: null })
    )

    const req = new Request('http://localhost/api/auth/template?name=TestPot&type=EAM&elements=U,Zr&systemName=U-Zr')
    const res = await GET(req as any)
    const json = await res.json()

    expect(json.html).toContain('势函数分发授权书')
    expect(json.html).toContain('TestPot')
    expect(json.html).toContain('EAM')
    expect(json.html).toContain('U,Zr')
    expect(json.html).toContain('zh-CN')
  })

  it('generates English HTML when lang=en', async () => {
    vi.mocked(supabase.from).mockImplementation(() =>
      mockSupabaseChain({ data: null, error: null })
    )

    const req = new Request('http://localhost/api/auth/template?lang=en&name=TestPot&type=EAM&elements=U,Zr&systemName=U-Zr')
    const res = await GET(req as any)
    const json = await res.json()

    expect(json.html).toContain('Interatomic Potential Distribution Authorization')
    expect(json.html).toContain('TestPot')
    expect(json.html).toContain('EAM')
    expect(json.html).toContain('lang="en"')
  })

  it('includes auto-filled user fields from profile when userId provided', async () => {
    const mockProfile = {
      full_name: 'Zhang San',
      email: 'zhang@example.com',
      affiliation: 'USTB',
      title: 'Professor',
      phone: '123456',
    }
    vi.mocked(supabase.from).mockImplementation(() =>
      mockSupabaseChain({ data: mockProfile, error: null })
    )

    const req = new Request('http://localhost/api/auth/template?userId=user-1&name=Test&type=EAM&elements=U&systemName=U&userName= fallback&userEmail=fallback@test.com')
    const res = await GET(req as any)
    const json = await res.json()

    expect(json.html).toContain('Zhang San')
    expect(json.html).toContain('USTB')
    expect(json.html).toContain('Professor')
    expect(json.html).toContain('zhang@example.com')
    expect(json.html).toContain('123456')
  })

  it('includes auto-print onload when print=1', async () => {
    vi.mocked(supabase.from).mockImplementation(() =>
      mockSupabaseChain({ data: null, error: null })
    )

    const req = new Request('http://localhost/api/auth/template?print=1&name=Test&type=EAM&elements=U&systemName=U')
    const res = await GET(req as any)
    const json = await res.json()

    expect(json.html).toContain('onload="window.print()"')
  })

  it('escapes HTML special characters in user input', async () => {
    vi.mocked(supabase.from).mockImplementation(() =>
      mockSupabaseChain({ data: null, error: null })
    )

    const req = new Request('http://localhost/api/auth/template?name=<script>alert(1)</script>&type=EAM&elements=U&systemName=U')
    const res = await GET(req as any)
    const json = await res.json()

    expect(json.html).not.toContain('<script>')
    expect(json.html).toContain('&lt;script&gt;')
  })
})
