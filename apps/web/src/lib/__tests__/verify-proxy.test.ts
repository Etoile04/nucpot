import { describe, it, expect, vi, beforeEach } from 'vitest'

// NFM-933/#935 regression: BFF must inject Authorization into proxied AutoVC
// requests — the AutoVC auth layer 401s on anonymous calls, which broke the
// admin verify page after the AutoVC service was restored.

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

const { proxyFetch } = await import('@/lib/verify-proxy')

function lastInit() {
  return mockFetch.mock.calls[mockFetch.mock.calls.length - 1][1]
}

describe('verify-proxy auth injection (#933/#935)', () => {
  beforeEach(() => {
    mockFetch.mockReset()
    mockFetch.mockResolvedValue(
      new Response('{"ok":true}', { status: 200, headers: { 'Content-Type': 'application/json' } })
    )
  })

  it('injects a Bearer token when caller provides none', async () => {
    await proxyFetch('/api/verify', { method: 'POST' })
    const headers = lastInit().headers as Headers
    expect(headers.get('Authorization')).toMatch(/^Bearer .+/)
  })

  it('does not overwrite an explicit Authorization header', async () => {
    await proxyFetch('/api/verify', {
      method: 'POST',
      headers: { Authorization: 'Bearer caller-token' },
    })
    const headers = lastInit().headers as Headers
    expect(headers.get('Authorization')).toBe('Bearer caller-token')
  })

  it('preserves Content-Type from caller headers', async () => {
    await proxyFetch('/api/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{"potential_id":"x"}',
    })
    const headers = lastInit().headers as Headers
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(headers.get('Authorization')).toMatch(/^Bearer /)
  })
})
