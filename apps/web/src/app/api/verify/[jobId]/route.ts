import { proxyFetch } from "@/lib/verify-proxy"
import { NextRequest } from "next/server"

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await params
  return proxyFetch(`/api/verify/${jobId}`)
}
