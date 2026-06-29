import type { NextConfig } from "next"
import path from "path"

const API_SERVER_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, "../../"),
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
