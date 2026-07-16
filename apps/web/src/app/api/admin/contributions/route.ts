import { NextRequest, NextResponse } from 'next/server'
import { verifyAdmin } from '@/lib/verify-admin'
import { supabase, supabaseAdmin } from '@/lib/supabase'

// GET: List all contributions with user info and potential details
export async function GET(request: NextRequest) {
  const { error, status } = await verifyAdmin(request)
  if (error) {
    return NextResponse.json({ error }, { status })
  }

  const { searchParams } = new URL(request.url)
  const statusFilter = searchParams.get('status') // optional filter: 'pending', 'approved', 'rejected'
  const page = parseInt(searchParams.get('page') || '1')
  const limit = parseInt(searchParams.get('limit') || '20')
  const offset = (page - 1) * limit

  // Use supabaseAdmin to bypass RLS and access all contributions
  const client = supabaseAdmin || supabase

  let query = client
    .from('contributions')
    .select(`
      id,
      potential_id,
      user_id,
      action,
      status,
      notes,
      created_at,
      profiles:user_id (
        id,
        username,
        full_name,
        email,
        role
      ),
      potentials:potential_id (
        id,
        name,
        display_name,
        type,
        elements,
        status
      )
    `, { count: 'exact' })
    .order('created_at', { ascending: false })
    .range(offset, offset + limit - 1)

  if (statusFilter) {
    query = query.eq('status', statusFilter)
  }

  const { data, count, error: queryError } = await query

  if (queryError) {
    return NextResponse.json({ error: queryError.message }, { status: 500 })
  }

  return NextResponse.json({
    contributions: data || [],
    total: count || 0,
    page,
    limit,
    totalPages: Math.ceil((count || 0) / limit),
  })
}

// PATCH: Approve or reject a contribution
export async function PATCH(request: NextRequest) {
  const { error, status, user } = await verifyAdmin(request)
  if (error) {
    return NextResponse.json({ error }, { status })
  }

  let body: { contributionId: string; action: 'approve' | 'reject' }
  try {
    body = await request.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 })
  }

  const { contributionId, action } = body
  if (!contributionId || !['approve', 'reject'].includes(action)) {
    return NextResponse.json(
      { error: 'contributionId and action (approve|reject) are required' },
      { status: 400 }
    )
  }

  const newStatus = action === 'approve' ? 'approved' : 'rejected'
  const client = supabaseAdmin || supabase

  // Fetch the contribution first
  const { data: contribution, error: fetchError } = await client
    .from('contributions')
    .select('id, potential_id, action, status')
    .eq('id', contributionId)
    .single()

  if (fetchError || !contribution) {
    return NextResponse.json({ error: 'Contribution not found' }, { status: 404 })
  }

  if (contribution.status !== 'pending') {
    return NextResponse.json(
      { error: `Contribution is already ${contribution.status}` },
      { status: 409 }
    )
  }

  // Update contribution status
  const { error: updateError } = await client
    .from('contributions')
    .update({ status: newStatus })
    .eq('id', contributionId)

  if (updateError) {
    return NextResponse.json({ error: updateError.message }, { status: 500 })
  }

  // If approved and there's a linked potential, update potential extra.status to 'approved'
  if (action === 'approve' && contribution.potential_id) {
    const { data: potential } = await client
      .from('potentials')
      .select('extra')
      .eq('id', contribution.potential_id)
      .single()

    const updatedExtra = {
      ...(potential?.extra || {}),
      status: 'approved',
    }

    await client
      .from('potentials')
      .update({ extra: updatedExtra, status: 'published' })
      .eq('id', contribution.potential_id)
  }

  return NextResponse.json({ success: true, contributionId, status: newStatus })
}
