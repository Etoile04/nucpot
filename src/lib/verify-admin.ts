import { NextRequest } from 'next/server'
import { supabase } from './supabase'

export interface AdminIdentity {
  id: string
  username: string
}

/**
 * Verify that the request comes from an authenticated admin user.
 * Supports both Authorization: Bearer <token> header and ?token=<token> query param.
 * Returns AdminIdentity on success, or null if unauthorized.
 */
export async function verifyAdmin(request: NextRequest): Promise<AdminIdentity | null> {
  const authHeader = request.headers.get('authorization')
  const token = authHeader?.startsWith('Bearer ')
    ? authHeader.slice(7)
    : new URL(request.url).searchParams.get('token')

  if (!token) return null

  const { data: { user }, error } = await supabase.auth.getUser(token)
  if (error || !user) return null

  const { data: profile } = await supabase
    .from('profiles')
    .select('role, username')
    .eq('id', user.id)
    .single()

  if (!profile || profile.role !== 'admin') return null
  return { id: user.id, username: profile.username || 'admin' }
}
