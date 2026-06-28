import { NextRequest, NextResponse } from 'next/server'
import { supabase, supabaseAdmin } from '@/lib/supabase'

export async function GET(request: NextRequest) {
  // 1. Verify auth
  const authHeader = request.headers.get('authorization')
  if (!authHeader?.startsWith('Bearer ')) {
    return NextResponse.json({ error: 'Authentication required' }, { status: 401 })
  }

  const token = authHeader.replace('Bearer ', '')
  const { data: { user }, error } = await supabase.auth.getUser(token)

  if (error || !user) {
    return NextResponse.json({ error: 'Invalid token' }, { status: 401 })
  }

  // 2. Query potentials uploaded by this user
  const client = supabaseAdmin || supabase

  const { data, error: queryError } = await client
    .from('potentials')
    .select('id, name, display_name, type, status, created_at, extra')
    .eq('extra->>uploaded_by', user.id)
    .order('created_at', { ascending: false })

  if (queryError) {
    return NextResponse.json({ error: queryError.message }, { status: 500 })
  }

  // 3. Map to response format — derive review status from extra.status
  const contributions = (data || []).map((row: Record<string, unknown>) => {
    const extra = (row.extra || {}) as Record<string, string>
    const reviewStatus = extra.status || row.status || 'pending'
    return {
      id: row.id,
      name: row.name,
      display_name: row.display_name,
      type: row.type,
      status: reviewStatus === 'approved' ? 'published' : reviewStatus,
      created_at: row.created_at,
    }
  })

  return NextResponse.json({ contributions })
}
