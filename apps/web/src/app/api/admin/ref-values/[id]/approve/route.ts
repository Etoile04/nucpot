import { proxyFetch } from '@/lib/verify-proxy'
import { NextRequest } from 'next/server'

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params
  return proxyFetch(`/api/admin/reference-values/${id}/approve`, {
    method: 'POST',
  })
}
