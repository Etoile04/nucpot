import { proxyFetch } from '@/lib/verify-proxy'
import { NextRequest } from 'next/server'
export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  return proxyFetch(`/api/verification/${id}/report`)
}
