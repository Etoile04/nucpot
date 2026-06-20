import { proxyFetch } from '@/lib/verify-proxy'

export async function GET() {
  return proxyFetch('/api/admin/reference-values')
}
