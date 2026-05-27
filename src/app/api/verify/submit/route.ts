import { proxyFetch } from '@/lib/verify-proxy'
import { NextRequest } from 'next/server'
export async function POST(req: NextRequest) {
  const body = await req.text()
  return proxyFetch('/api/verification/v2', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
  })
}
