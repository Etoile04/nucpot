const API_BASE = process.env.NEXT_PUBLIC_AUTOCV_API_URL || 'https://verify.nucpot.dpdns.org'

// Server-side only API key injected into proxied requests. The AutoVC API
// accepts any non-empty Bearer token at its auth layer (placeholder Sprint-2
// implementation); a shared secret keeps anonymous web traffic out while the
// BFF stays the only caller that needs to know it.
const AUTOCV_API_KEY = process.env.AUTOCV_API_KEY || 'nucpot-bff-internal'

export async function proxyFetch(path: string, init?: RequestInit) {
  const url = `${API_BASE}${path}`
  const headers = new Headers(init?.headers || {})
  if (!headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${AUTOCV_API_KEY}`)
  }
  try {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 25000)
    const upstream = await fetch(url, { ...init, headers, signal: controller.signal })
    clearTimeout(timeout)
    const body = await upstream.text()
    return new Response(body, {
      status: upstream.status,
      headers: { 'Content-Type': upstream.headers.get('content-type') || 'application/json' },
    })
  } catch (error: unknown) {
    const msg = error instanceof Error ? error.message : String(error)
    console.error(`[verify-proxy] ${path}:`, msg)
    return Response.json(
      { error: 'Verification service unavailable', detail: msg },
      { status: 502 }
    )
  }
}
