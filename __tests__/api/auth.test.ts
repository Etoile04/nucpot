import { describe, it, expect, beforeAll } from 'vitest'

const BASE = 'http://localhost:3000/api/auth'

// These tests need the full Supabase stack + dev server running
// Run manually: npx vitest run __tests__/api/auth.test.ts
describe.skipIf(process.env.CI === 'true')('Auth API', () => {
  const testEmail = `test-${Date.now()}@nucpot.test`
  const testPassword = 'testpassword123'
  const testUsername = `testuser_${Date.now()}`
  let userId: string

  it('registers a new user', async () => {
    const res = await fetch(`${BASE}/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: testEmail, password: testPassword, username: testUsername, fullName: 'Test User' }),
    })
    const data = await res.json()

    expect(res.status).toBe(201)
    expect(data.user).toBeDefined()
    expect(data.user.username).toBe(testUsername)
    userId = data.user.id
  })

  it('rejects duplicate registration', async () => {
    const res = await fetch(`${BASE}/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: testEmail, password: testPassword, username: testUsername }),
    })
    expect(res.status).toBe(400)
  })

  it('logs in with correct credentials', async () => {
    const res = await fetch(`${BASE}/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: testEmail, password: testPassword }),
    })
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data.session).toBeDefined()
    expect(data.profile).toBeDefined()
    expect(data.profile.username).toBe(testUsername)
  })

  it('rejects wrong password', async () => {
    const res = await fetch(`${BASE}/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: testEmail, password: 'wrongpassword' }),
    })
    expect(res.status).toBe(401)
  })
})
