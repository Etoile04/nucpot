import type { NextConfig } from "next"
import path from "path"

// API_SERVER_URL configures the Next.js rewrite proxy for /api/* requests.
// In Docker production, nginx already proxies /api/* to the backend, so the
// rewrite is only needed for local development and preview builds.
// DO NOT set this to the public domain (nucpot.dpdns.org) — that creates an
// infinite loop since nginx routes /api/* back to Next.js.
const API_SERVER_URL = process.env.API_SERVER_URL

const API_SERVER_FALLBACK = API_SERVER_URL ?? "http://localhost:8000"

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, "../../"),
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-DNS-Prefetch-Control", value: "on" },
        ],
      },
    ]
  },
  async rewrites() {
    // Skip rewrite when API_SERVER_URL matches the public domain — nginx
    // already handles /api/* routing in production.
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
