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

// POST: Reject a reference value
export async function POST(
   req: NextRequest,
    { params }: { params: Promise<{ id: string }> },
) {
  const { error, status, user } = await verifyAdmin(req)
  if (error) {
    return NextResponse.json({ error }, { status })
      }

  const { id } = await params

  try {
    const body = await req.json().catch(() => ({}))

    const { data: updated, error: err } = await supabaseAdmin!
        .from('reference_values')
        .update({
          status: 'rejected',
          reviewed_by: user.id,
          reviewed_at: new Date().toISOString(),
          review_notes: body.notes,
        })
        .eq('id', id)
        .select('*')
        .single()

    if (err) {
      console.error('[ref-values reject]', err)
      return NextResponse.json({ error: err.message }, { status: 500 })
       }

      // Audit log
    await supabaseAdmin!
        .from('review_audit_log')
        .insert({
          ref_id: id,
          action: 'reject',
          reviewed_by: user.id,
          notes: body.notes,
          created_at: new Date().toISOString(),
        })

    return NextResponse.json({ data: updated })
     } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    console.error('[ref-values reject]', msg)
    return NextResponse.json({ error: msg }, { status: 500 })
     }
}
