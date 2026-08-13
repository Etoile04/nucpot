/**
 * Server-side admin authentication helper.
 *
 * After auth unification (S1/S2), authentication uses HttpOnly cookies
 * set by the FastAPI backend.  This helper reads the cookie from the
 * incoming request, forwards it to the backend /api/v1/auth/me to
 * validate the session, and checks that the user has the "admin" blog_role.
 *
 * Usage in Next.js API routes:
 *   import { verifyAdmin } from '@/lib/verify-admin'
 *   const { error, status, user } = await verifyAdmin(request)
 *   if (error) return NextResponse.json({ error }, { status })
 */

import { NextRequest } from 'next/server'
import type { AppUser } from '@/components/AuthProvider'

const BACKEND_INTERNAL_URL =
  process.env.BACKEND_INTERNAL_URL ?? 'http://127.0.0.1:8001'

interface VerifyResult {
  error: string | null
  status: number
  user: AppUser | null
}

/**
 * Verify admin身份 by forwarding the HttpOnly cookie to the FastAPI backend.
 *
 * In Docker production, nginx proxies /api/* to the backend, so we must call
 * the backend directly (via internal URL) to avoid nginx routing loops.
 */
export async function verifyAdmin(request: NextRequest): Promise<VerifyResult> {
  // Extract the access_token cookie from the incoming request
  const token =
    request.cookies.get('access_token')?.value ??
    request.cookies.get('blog_admin_token')?.value ??
    request.cookies.get('auth_token')?.value

  if (!token) {
    return { error: 'Authentication required', status: 401, user: null }
  }

  try {
    // Forward the token as a Bearer header to the backend /api/v1/auth/me
    const meUrl = `${BACKEND_INTERNAL_URL}/api/v1/auth/me`
    const res = await fetch(meUrl, {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
    })

    if (!res.ok) {
      return { error: 'Invalid token', status: 401, user: null }
    }

    const body = await res.json()
    const data = body.data ?? body

    // Check admin role
    if (data.blog_role !== 'admin') {
      return { error: 'Admin access required', status: 403, user: null }
    }

    return { error: null, status: 200, user: data as AppUser }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    console.error('[verifyAdmin] backend call failed:', msg)
    return { error: 'Authentication service unavailable', status: 503, user: null }
  }
}
