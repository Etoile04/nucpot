import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { NextRequest } from 'next/server'

// NFM-5898: the ISR warm-up endpoint is the deploy-path escape hatch from the
// build-anchored ISR freshness window. `revalidatePath` is the ONLY sanctioned
// way to invalidate a prerendered entry whose age clock started at `next
// build` (a plain GET would cache-HIT the same fresh entry), so the contract
// here pins exactly which entries the deploy warms: '/' and '/potentials' —
// the latter because /browse is an afterFiles alias rewrite of /potentials
// (NFM-5228) and shares its ISR entry.
const { revalidatePathMock } = vi.hoisted(() => ({ revalidatePathMock: vi.fn() }))

vi.mock('next/cache', () => ({
  revalidatePath: revalidatePathMock,
}))

import { POST } from '../route'

const TOKEN = 'nfm5898-test-warmup-token'

function postRequest(token?: string): NextRequest {
  const headers: Record<string, string> = {}
  if (token !== undefined) {
    headers['x-nfm-warmup-token'] = token
  }
  return new NextRequest('http://127.0.0.1:3000/api/revalidate-landing', {
    method: 'POST',
    headers,
  })
}

describe('POST /api/revalidate-landing (NFM-5898 ISR warm-up)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    process.env.ISR_WARMUP_TOKEN = TOKEN
  })

  afterEach(() => {
    delete process.env.ISR_WARMUP_TOKEN
  })

  it('fails closed with 503 when ISR_WARMUP_TOKEN is not configured', async () => {
    delete process.env.ISR_WARMUP_TOKEN

    const res = await POST(postRequest(TOKEN))

    expect(res.status).toBe(503)
    expect(revalidatePathMock).not.toHaveBeenCalled()
  })

  it('rejects a wrong token with 403 without touching any cache entry', async () => {
    const res = await POST(postRequest('wrong-token'))

    expect(res.status).toBe(403)
    expect(revalidatePathMock).not.toHaveBeenCalled()
  })

  it('rejects a missing token header with 403', async () => {
    const res = await POST(postRequest())

    expect(res.status).toBe(403)
    expect(revalidatePathMock).not.toHaveBeenCalled()
  })

  it('revalidates exactly the landing entries on a valid token', async () => {
    const res = await POST(postRequest(TOKEN))

    expect(res.status).toBe(200)
    // '/' is the homepage entry; '/potentials' IS the '/browse' entry via
    // the NFM-5228 alias rewrite — revalidatePath('/browse') would be a
    // no-op because no such route exists on disk.
    expect(revalidatePathMock).toHaveBeenCalledTimes(2)
    expect(revalidatePathMock).toHaveBeenNthCalledWith(1, '/')
    expect(revalidatePathMock).toHaveBeenNthCalledWith(2, '/potentials')

    const body = (await res.json()) as { revalidated: string[] }
    expect(body.revalidated).toEqual(['/', '/potentials'])
  })

  it('does not accept the token from the request body', async () => {
    const res = await POST(
      new NextRequest('http://127.0.0.1:3000/api/revalidate-landing', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ token: TOKEN }),
      })
    )

    expect(res.status).toBe(403)
    expect(revalidatePathMock).not.toHaveBeenCalled()
  })
})
