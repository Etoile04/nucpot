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

// POST: Batch approve/reject reference values
export async function POST(req: NextRequest) {
  const { error, status, user } = await verifyAdmin(req)
  if (error) {
    return NextResponse.json({ error }, { status })
  }
  if (!user) {
    return NextResponse.json({ error: 'User not authenticated' }, { status: 401 })
  }

  try {
    const body = await req.json()
    const { ids, action, notes } = body // action: 'approve' | 'reject'

    if (!ids || !Array.isArray(ids) || !action) {
      return NextResponse.json({ error: 'ids (array) and action required' }, { status: 400 })
     }

    const new_status = action === 'approve' ? 'active' : 'rejected'

    // Update reference_values
    const { error: up_err } = await supabaseAdmin!
      .from('reference_values')
      .update({
        status: new_status,
        review_notes: notes,
        reviewed_by: user.id,
        reviewed_at: new Date().toISOString(),
       })
      .in('id', ids)

    if (up_err) {
      console.error('[ref-values batch]', up_err)
      return NextResponse.json({ error: up_err.message }, { status: 500 })
     }

    // Log to audit
    const audit_records = ids.map((id: string) => ({
      ref_id: id,
      action,
      reviewed_by: user.id,
      notes,
      created_at: new Date().toISOString(),
     }))

    const { error: audit_err } = await supabaseAdmin!
      .from('review_audit_log')
      .insert(audit_records)

    if (audit_err) {
      console.error('[ref-values batch audit]', audit_err)
     }

    return NextResponse.json({ success: true, updated: ids.length })
   } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    console.error('[ref-values batch]', msg)
    return NextResponse.json({ error: msg }, { status: 500 })
   }
}
