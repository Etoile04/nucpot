import { proxyFetch } from "@/lib/verify-proxy"
import { NextRequest } from "next/server"

// GET /api/verifications — admin verification history (read-only).
// Proxies AutoVC GET /api/verifications (Supabase-backed, newest first).
// Query params limit/offset are forwarded with the same [1,200] clamp as the
// upstream endpoint. The BFF injects the shared AUTOCV_API_KEY, so callers
// don't need it.
export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams
  let limit = parseInt(sp.get("limit") ?? "50", 10)
  let offset = parseInt(sp.get("offset") ?? "0", 10)
  if (Number.isNaN(limit) || limit < 1) limit = 50
  if (limit > 200) limit = 200
  if (Number.isNaN(offset) || offset < 0) offset = 0
  return proxyFetch(`/api/verifications?limit=${limit}&offset=${offset}`, {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  })
}
