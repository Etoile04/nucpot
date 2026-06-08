import { NextRequest, NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'

const VERIFY_SERVICE_URL = process.env.VERIFY_SERVICE_URL || 'https://verify.nucpot.dpdns.org'

async function verifyAdmin(request: NextRequest) {
  const authHeader = request.headers.get('authorization')
  if (!authHeader?.startsWith('Bearer ')) {
    return { error: 'Authentication required', status: 401, user: null }
  }
  const token = authHeader.replace('Bearer ', '')
  const { data: { user }, error } = await supabase.auth.getUser(token)
  if (error || !user) {
    return { error: 'Invalid token', status: 401, user: null }
  }

  const { data: profile } = await supabase.from('profiles').select('role').eq('id', user.id).single()
  if (profile?.role !== 'admin') {
    return { error: 'Admin access required', status: 403, user: null }
  }

  return { error: null, status: 200, user }
}

// Proxy handler - forwards requests to verify-service admin API
async function proxyRequest(request: NextRequest, method: string) {
  const auth = await verifyAdmin(request)
  if (auth.error) {
    return NextResponse.json({ error: auth.error }, { status: auth.status })
  }

  // Build target URL: /api/admin/reference-values/* → verify-service
  const { pathname, search } = new URL(request.url)
  // Remove the /api prefix since verify-service routes already have /api
  const apiPath = pathname.replace(/^\/api/, '')
  const targetUrl = `${VERIFY_SERVICE_URL}/api${apiPath}${search}`

  // Forward headers (strip host, keep content-type)
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }

  // Build fetch options
  const fetchOptions: RequestInit = {
    method,
    headers,
  }

  // Forward body for POST/PATCH/PUT
  if (['POST', 'PATCH', 'PUT'].includes(method)) {
    try {
      fetchOptions.body = await request.text()
    } catch {
      // No body
    }
  }

  try {
    const response = await fetch(targetUrl, fetchOptions)

    // Forward the response
    const contentType = response.headers.get('content-type') || 'application/json'
    const body = await response.text()

    return new NextResponse(body, {
      status: response.status,
      headers: { 'Content-Type': contentType },
    })
  } catch (err) {
    console.error('BFF proxy error:', err)
    return NextResponse.json(
      { error: 'Verify service unavailable' },
      { status: 502 }
    )
  }
}

// GET /api/admin/reference-values[/...path]
export async function GET(request: NextRequest) {
  return proxyRequest(request, 'GET')
}

// POST /api/admin/reference-values[/...path]
export async function POST(request: NextRequest) {
  return proxyRequest(request, 'POST')
}

// PATCH /api/admin/reference-values[/...path]
export async function PATCH(request: NextRequest) {
  return proxyRequest(request, 'PATCH')
}

// DELETE /api/admin/reference-values[/...path]
export async function DELETE(request: NextRequest) {
  return proxyRequest(request, 'DELETE')
}
