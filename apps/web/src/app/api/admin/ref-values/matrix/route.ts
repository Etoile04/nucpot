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

// GET: Matrix view — all reference values grouped by material and property
export async function GET(request: NextRequest) {
  const { error, status } = await verifyAdmin(request)
  if (error) {
    return NextResponse.json({ error }, { status })
    }

  const { data, error: err } = await supabaseAdmin!
      .from('reference_values')
      .select('material, value_type, value, unit, confidence, status, source')
      .eq('status', 'active')
      .order('material')
      .order('value_type')

  if (err) {
    console.error('[ref-values matrix]', err)
    return NextResponse.json({ error: err.message }, { status: 500 })
    }

  // Group by material → value_type → value
  const matrix: Record<string, Record<string, any>> = {}
  for (const row of (data || [])) {
    const mat = row.material || 'unknown'
    const vt = row.value_type || 'unknown'
    if (!matrix[mat]) matrix[mat] = {}
    matrix[mat][vt] = row
    }

  return NextResponse.json({ matrix })
}
