import type { NextConfig } from "next"
import path from "path"

// API_SERVER_URL configures the Next.js rewrite proxy for /api/* requests.
// In Docker production, nginx already proxies /api/* to the backend, so the
// rewrite is only needed for local development and preview builds.
// DO NOT set this to the public domain (nucpot.dpdns.org) — that creates an
// infinite loop since nginx routes /api/* back to Next.js.
//
// DISABLE_API_REWRITE is an explicit escape hatch for production deployments
// where an upstream proxy (nginx, cloudflared) already routes /api/*. Set to
// "true" (or "1") to force-disable the rewrite even when API_SERVER_URL is set
// to a Docker internal hostname (which does NOT match NEXT_PUBLIC_APP_URL host
// and therefore bypasses the loop-detection below).
const API_SERVER_URL = process.env.API_SERVER_URL
const DISABLE_API_REWRITE =
  process.env.DISABLE_API_REWRITE === "true" ||
  process.env.DISABLE_API_REWRITE === "1"

const API_SERVER_FALLBACK = API_SERVER_URL ?? "http://localhost:8100"

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, "../../"),
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-DNS-Prefetch-Control", value: "on" },
        ],
      },
    ]
  },
  async rewrites() {
    // Explicit disable: production deployments with nginx (or another
    // upstream proxy) handling /api/* must set DISABLE_API_REWRITE=true.
    // Without this, the rewrite below would proxy /api/* back through
    // Next.js and either hang or fail (NFM-1407).
    if (DISABLE_API_REWRITE) {
      return []
    }

    // Skip rewrite when API_SERVER_URL matches the public domain — nginx
    // already handles /api/* routing in that case.
    const publicUrl = process.env.NEXT_PUBLIC_APP_URL
    const wouldLoop = API_SERVER_URL && publicUrl &&
      new URL(API_SERVER_URL).host === new URL(publicUrl).host

    if (wouldLoop) {
      return []
    }

    return [
      {
        // Proxy /api/* requests to the backend, eliminating CORS for
        // same-origin browser requests in local dev and preview builds.
        source: "/api/:path*",
        destination: `${API_SERVER_FALLBACK}/api/:path*`,
      },
    ]
  },
}

export default nextConfig
