import { proxyFetch } from '@/lib/verify-proxy'
import { NextRequest } from 'next/server'

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params
  return proxyFetch(`/api/admin/reference-values/${id}`)
}

export async function PATCH(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params
  const body = await _req.text()
  return proxyFetch(`/api/admin/reference-values/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body,
  })
}

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params
  return proxyFetch(`/api/admin/reference-values/${id}`, { method: 'DELETE' })
}
