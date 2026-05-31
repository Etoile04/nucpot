import { proxyFetch } from '@/lib/verify-proxy'
import { NextRequest } from 'next/server'

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params
  return proxyFetch(`/api/admin/reference-values/${id}/approve`, {
    method: 'POST',
  })
}
