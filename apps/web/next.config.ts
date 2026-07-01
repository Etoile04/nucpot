import type { NextConfig } from "next"
import path from "path"

const API_SERVER_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

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
        destination: `${API_SERVER_URL}/api/:path*`,
      },
    ]
  },
}

export default nextConfig
