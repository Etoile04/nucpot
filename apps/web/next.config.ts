import type { NextConfig } from "next"
import path from "path"

// API_SERVER_URL is required in production so the rewrite proxy can forward
// /api/* requests to the backend.  Without it the proxy targets localhost:8000
// which does not exist on Vercel, causing ALL API calls to fail.
const API_SERVER_URL = process.env.API_SERVER_URL

// Hard-fail guard: require API_SERVER_URL in deployed production environments.
// Allow fallback for CI builds, local development, and preview builds.
const isProductionDeploy = process.env.NODE_ENV === "production" && !process.env.CI

if (!API_SERVER_URL && isProductionDeploy) {
  throw new Error(
    "[NFM-623] API_SERVER_URL environment variable is required in production. " +
      "Set it in Vercel → Settings → Environment Variables (e.g. https://nucpot.dpdns.org).",
  )
}

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
    return [
      {
        // Proxy /api/* requests to the backend, eliminating CORS for
        // same-origin browser requests.
        source: "/api/:path*",
        destination: `${API_SERVER_FALLBACK}/api/:path*`,
      },
    ]
  },
}

export default nextConfig
