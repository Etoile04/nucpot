import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mockSupabaseChain } from '../setup'
import { GET, PATCH } from '@/app/api/auth/profile/route'
import { supabase, supabaseAdmin } from '@/lib/supabase'



const mockGetUser = vi.fn()
vi.mock('@/lib/supabase', () => ({
  supabase: {
    from: vi.fn(),
    auth: { getUser: (...args: unknown[]) => mockGetUser(...args) },
  },
  supabaseAdmin: null,
}))

describe('GET /api/auth/profile', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('returns 401 without authorization header', async () => {
    const req = new Request('http://localhost/api/auth/profile')
    const res = await GET(req as any)
    expect(res.status).toBe(401)
  })

  it('returns profile data for authenticated user', async () => {
    const mockUser = { id: 'user-1', email: 'test@test.com' }
    const mockProfile = { id: 'user-1', username: 'test', full_name: 'Test User', email: 'test@test.com' }

    mockGetUser.mockResolvedValue({ data: { user: mockUser }, error: null })
    vi.mocked(supabase.from).mockImplementation(() => mockSupabaseChain({ data: mockProfile, error: null }))

    const req = new Request('http://localhost/api/auth/profile', {
      headers: { authorization: 'Bearer valid-token' },
    })
    const res = await GET(req as any)
    const json = await res.json()

    expect(res.status).toBe(200)
    expect(json.user.id).toBe('user-1')
    expect(json.profile.full_name).toBe('Test User')
  })

  it('returns 401 with invalid token', async () => {
    mockGetUser.mockResolvedValue({ data: { user: null }, error: { message: 'invalid' } })

    const req = new Request('http://localhost/api/auth/profile', {
      headers: { authorization: 'Bearer bad-token' },
    })
    const res = await GET(req as any)

    expect(res.status).toBe(401)
  })
})

describe('PATCH /api/auth/profile', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('returns 401 without auth', async () => {
    const req = new Request('http://localhost/api/auth/profile', {
      method: 'PATCH',
      body: JSON.stringify({ full_name: 'New Name' }),
    })
    const res = await PATCH(req as any)
    expect(res.status).toBe(401)
  })

  it('updates profile fields', async () => {
    const mockUser = { id: 'user-1', email: 'test@test.com' }
    const updatedProfile = { id: 'user-1', full_name: 'New Name', affiliation: 'MIT' }

    mockGetUser.mockResolvedValue({ data: { user: mockUser }, error: null })
    vi.mocked(supabase.from).mockImplementation(() => mockSupabaseChain({ data: updatedProfile, error: null }))

    const req = new Request('http://localhost/api/auth/profile', {
      method: 'PATCH',
      headers: {
        authorization: 'Bearer valid-token',
        'content-type': 'application/json',
      },
      body: JSON.stringify({ full_name: 'New Name', affiliation: 'MIT' }),
    })
    const res = await PATCH(req as any)
    const json = await res.json()

    expect(res.status).toBe(200)
    expect(json.profile).toBeDefined()
  })

  it('returns 400 when no valid fields provided', async () => {
    const mockUser = { id: 'user-1', email: 'test@test.com' }
    mockGetUser.mockResolvedValue({ data: { user: mockUser }, error: null })

    const req = new Request('http://localhost/api/auth/profile', {
      method: 'PATCH',
      headers: {
        authorization: 'Bearer valid-token',
        'content-type': 'application/json',
      },
      body: JSON.stringify({ invalid_field: 'value' }),
    })
    const res = await PATCH(req as any)

    expect(res.status).toBe(400)
  })

  it('auto-creates profile when update returns no rows', async () => {
    const mockUser = { id: 'user-1', email: 'test@test.com' }
    const createdProfile = { id: 'user-1', username: 'test', full_name: 'New User', email: 'test@test.com', role: 'contributor' }

    mockGetUser.mockResolvedValue({ data: { user: mockUser }, error: null })

    // First call (update) returns null, second call (insert) returns created profile
    let callIdx = 0
    vi.mocked(supabase.from).mockImplementation(() => {
      const results = [
        { data: null, error: null },   // update: maybeSingle returns null
        { data: createdProfile, error: null }, // insert: single returns data
      ]
      return mockSupabaseChain(results[callIdx++])
    })

    const req = new Request('http://localhost/api/auth/profile', {
      method: 'PATCH',
      headers: {
        authorization: 'Bearer valid-token',
        'content-type': 'application/json',
      },
      body: JSON.stringify({ full_name: 'New User' }),
    })
    const res = await PATCH(req as any)
    const json = await res.json()

    expect(res.status).toBe(200)
    expect(json.profile).toEqual(createdProfile)
  })

  it('returns 400 for invalid JSON body', async () => {
    const mockUser = { id: 'user-1', email: 'test@test.com' }
    mockGetUser.mockResolvedValue({ data: { user: mockUser }, error: null })

    const req = new Request('http://localhost/api/auth/profile', {
      method: 'PATCH',
      headers: {
        authorization: 'Bearer valid-token',
      },
      body: 'not json',
    })
    // Need to set content-type or make body unparseable
    const res = await PATCH(req as any)
    expect(res.status).toBe(400)
  })
})
