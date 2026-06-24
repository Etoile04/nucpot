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

// GET: Single reference value by ID
export async function GET(
   _req: NextRequest,
   { params }: { params: Promise<{ id: string }> },
) {
  const { error, status } = await verifyAdmin(_req)
  if (error) {
    return NextResponse.json({ error }, { status })
     }

  const { id } = await params

  const { data, error: err } = await supabaseAdmin!
       .from('reference_values')
       .select('*')
       .eq('id', id)
       .single()

  if (err) {
    console.error('[ref-values get]', err)
    return NextResponse.json({ error: err.message }, { status: err.code === 'PGRST300' ? 404 : 500 })
     }

  return NextResponse.json({ data })
}

// PATCH: Update a reference value
export async function PATCH(
   req: NextRequest,
   { params }: { params: Promise<{ id: string }> },
) {
  const { error, status, user } = await verifyAdmin(req)
  if (error) {
    return NextResponse.json({ error }, { status })
     }

  const { id } = await params

  try {
    const body = await req.json()

     // Store old data for audit
    const { data: old } = await supabaseAdmin!
        .from('reference_values')
        .select('*')
        .eq('id', id)
        .single()

    const { data: updated, error: err } = await supabaseAdmin!
        .from('reference_values')
        .update(body)
        .eq('id', id)
        .select('*')
        .single()

    if (err) {
      console.error('[ref-values patch]', err)
      return NextResponse.json({ error: err.message }, { status: 500 })
      }

     // Audit log
    await supabaseAdmin!
        .from('review_audit_log')
        .insert({
          ref_id: id,
          action: 'update',
          reviewed_by: user.id,
          old_data: old,
          new_data: updated,
          notes: body.review_notes || body.notes,
          created_at: new Date().toISOString(),
          })

    return NextResponse.json({ data: updated })
    } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    console.error('[ref-values patch]', msg)
    return NextResponse.json({ error: msg }, { status: 500 })
    }
}

// DELETE: Soft delete a reference value
export async function DELETE(
   req: NextRequest,
   { params }: { params: Promise<{ id: string }> },
) {
  const { error, status, user } = await verifyAdmin(req)
  if (error) {
    return NextResponse.json({ error }, { status })
     }

  const { id } = await params

  const { error: err } = await supabaseAdmin!
       .from('reference_values')
       .update({ status: 'deleted', reviewed_by: user.id, reviewed_at: new Date().toISOString() })
       .eq('id', id)

  if (err) {
    console.error('[ref-values delete]', err)
    return NextResponse.json({ error: err.message }, { status: 500 })
     }

  return NextResponse.json({ success: true })
}
