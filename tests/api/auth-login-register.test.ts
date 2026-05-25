import { describe, it, expect, vi, beforeEach } from 'vitest'
import { POST as loginPOST } from '@/app/api/auth/login/route'
import { POST as registerPOST } from '@/app/api/auth/register/route'
import { supabase, supabaseAdmin } from '@/lib/supabase'

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

const mockSignInWithPassword = vi.fn()
vi.mock('@/lib/supabase', () => ({
  supabase: {
    from: vi.fn(),
    auth: { signInWithPassword: (...args: unknown[]) => mockSignInWithPassword(...args) },
  },
  supabaseAdmin: {
    auth: {
      admin: {
        createUser: vi.fn(),
        deleteUser: vi.fn(),
      },
    },
    from: vi.fn(),
  },
}))

// Need to also mock the module-level supabaseAdmin import
// The vi.mock above provides it

describe('POST /api/auth/login', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('returns 400 when email is missing', async () => {
    const req = new Request('http://localhost/api/auth/login', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ password: '123456' }),
    })
    const res = await loginPOST(req as any)
    expect(res.status).toBe(400)
    const json = await res.json()
    expect(json.error).toContain('email')
  })

  it('returns 400 when password is missing', async () => {
    const req = new Request('http://localhost/api/auth/login', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: 'test@test.com' }),
    })
    const res = await loginPOST(req as any)
    expect(res.status).toBe(400)
  })

  it('returns 401 on invalid credentials', async () => {
    mockSignInWithPassword.mockResolvedValue({
      data: { user: null, session: null },
      error: { message: 'Invalid login credentials' },
    })

    const req = new Request('http://localhost/api/auth/login', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: 'test@test.com', password: 'wrong' }),
    })
    const res = await loginPOST(req as any)

    expect(res.status).toBe(401)
    const json = await res.json()
    expect(json.error).toBe('Invalid login credentials')
  })

  it('returns session and profile on successful login', async () => {
    const mockUser = { id: 'user-1', email: 'test@test.com' }
    const mockSession = { access_token: 'token-123' }
    const mockProfile = { id: 'user-1', username: 'test', role: 'contributor' }

    mockSignInWithPassword.mockResolvedValue({
      data: { user: mockUser, session: mockSession },
      error: null,
    })
    vi.mocked(supabase.from).mockImplementation(() => mockChain({ data: mockProfile, error: null }))

    const req = new Request('http://localhost/api/auth/login', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: 'test@test.com', password: 'correct' }),
    })
    const res = await loginPOST(req as any)
    const json = await res.json()

    expect(res.status).toBe(200)
    expect(json.session).toEqual(mockSession)
    expect(json.user).toEqual(mockUser)
    expect(json.profile).toEqual(mockProfile)
  })
})

describe('POST /api/auth/register', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('returns 400 when required fields are missing', async () => {
    const req = new Request('http://localhost/api/auth/register', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: 'test@test.com', password: '123456' }), // missing username
    })
    const res = await registerPOST(req as any)

    expect(res.status).toBe(400)
    const json = await res.json()
    expect(json.error).toContain('username')
  })

  it('returns 201 on successful registration', async () => {
    const mockAuthUser = { id: 'new-user-1', email: 'new@test.com' }

    // Get supabaseAdmin from the mock
    const { supabaseAdmin } = await import('@/lib/supabase')
    vi.mocked(supabaseAdmin!.auth.admin.createUser).mockResolvedValue({
      data: { user: mockAuthUser },
      error: null,
    } as any)
    vi.mocked(supabaseAdmin!.from).mockImplementation(() => mockChain({ error: null }))

    const req = new Request('http://localhost/api/auth/register', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: 'new@test.com', password: '123456', username: 'newuser', fullName: 'New User' }),
    })
    const res = await registerPOST(req as any)
    const json = await res.json()

    expect(res.status).toBe(201)
    expect(json.message).toBe('Registration successful')
    expect(json.user.email).toBe('new@test.com')
    expect(json.user.username).toBe('newuser')
  })

  it('returns 400 when auth user creation fails', async () => {
    const { supabaseAdmin } = await import('@/lib/supabase')
    vi.mocked(supabaseAdmin!.auth.admin.createUser).mockResolvedValue({
      data: { user: null },
      error: { message: 'User already exists' },
    } as any)

    const req = new Request('http://localhost/api/auth/register', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: 'dup@test.com', password: '123456', username: 'dupuser' }),
    })
    const res = await registerPOST(req as any)

    expect(res.status).toBe(400)
    const json = await res.json()
    expect(json.error).toBe('User already exists')
  })
})
