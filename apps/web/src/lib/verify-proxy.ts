const API_BASE = process.env.NEXT_PUBLIC_AUTOCV_API_URL || 'https://verify.nucpot.dpdns.org'

export async function proxyFetch(path: string, init?: RequestInit) {
  const url = `${API_BASE}${path}`
  try {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 25000)
    const upstream = await fetch(url, { ...init, signal: controller.signal })
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
