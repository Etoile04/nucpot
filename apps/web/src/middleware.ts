import { NextResponse } from "next/server"
import type { NextRequest } from "next/server"

/**
 * Edge-level JWT authentication middleware.
 * Protects (dashboard) and admin routes.
 * Spec: NFM-826 §2.3 — middleware guard for authenticated routes.
 *
 * Route groups like (dashboard) don't affect URL paths, so we match
 * the actual URL patterns: /rag/*, /review/*, /extraction/*, /admin/*
 *
 * Note: This is a pre-flight check. The AuthGuard component provides
 * client-side validation with redirect. This middleware provides
 * Edge-level protection before the page is even rendered.
 */

const PROTECTED_PATHS = [
  "/rag",
  "/review",
  "/extraction",
  "/admin/kg",
  "/admin/v4-extraction",
]

function isProtectedPath(pathname: string): boolean {
  return PROTECTED_PATHS.some(
    (p) => pathname === p || pathname.startsWith(p + "/")
  )
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl

  if (!isProtectedPath(pathname)) {
    return NextResponse.next()
  }

  // Check for JWT token in cookies or Authorization header
  // The app uses localStorage for token storage (blog_admin_token),
  // but middleware runs on Edge and can't access localStorage.
  // We check the cookie version that gets set on login.
  const token =
    request.cookies.get("blog_admin_token")?.value ||
    request.cookies.get("auth_token")?.value

  if (!token) {
    const loginUrl = new URL("/admin/login", request.url)
    loginUrl.searchParams.set("redirect", pathname)
    return NextResponse.redirect(loginUrl)
  }

  return NextResponse.next()
}

export const config = {
  matcher: [
    "/rag/:path*",
    "/review/:path*",
    "/extraction/:path*",
    "/admin/kg/:path*",
    "/admin/v4-extraction/:path*",
  ],
}
