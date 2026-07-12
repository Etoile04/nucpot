import { NextRequest, NextResponse } from 'next/server'
import { supabaseAdmin } from '@/lib/supabase'

async function verifyAdmin(request: NextRequest) {
  const authHeader = request.headers.get('authorization')
  if (!authHeader?.startsWith('Bearer ')) {
    return { error: 'Authentication required', status: 401, user: null }
  }
  const token = authHeader.replace('Bearer ', '')
  const { data: { user }, error } = await supabaseAdmin!.auth.getUser(token)
  if (error || !user) {
    return { error: 'Invalid token', status: 401, user: null }
  }
  const { data: profile } = await supabaseAdmin!
    .from('profiles')
    .select('role')
    .eq('id', user.id)
    .single()
  if (profile?.role !== 'admin') {
    return { error: 'Admin access required', status: 403, user: null }
  }
  return { error: null, status: 200, user }
}

// GET: List reference values (for review queue)
export async function GET(request: NextRequest) {
  const { error, status } = await verifyAdmin(request)
  if (error) {
    return NextResponse.json({ error }, { status })
  }

  const { searchParams } = new URL(request.url)
  const limit = searchParams.get('limit') ?? '50'
  const offset = searchParams.get('offset') ?? '0'
  const status_filter = searchParams.get('status') // active, pending, rejected

  let query = supabaseAdmin!
    .from('reference_values')
    .select('*', { count: 'exact' })

  if (status_filter) {
    query = query.eq('status', status_filter)
  }

  query = query.range(parseInt(offset), parseInt(offset) + parseInt(limit) - 1)

  const { data, error: err, count } = await query

  if (err) {
    console.error('[ref-values list]', err)
    return NextResponse.json({ error: err.message }, { status: 500 })
  }

  return NextResponse.json({ data, count: count ?? 0 })
}
