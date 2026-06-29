import { NextRequest, NextResponse } from 'next/server'
import { supabase, supabaseAdmin } from '@/lib/supabase'

export async function GET(request: NextRequest) {
  // Verify admin
  const authHeader = request.headers.get('authorization')
  if (!authHeader?.startsWith('Bearer ')) {
    return NextResponse.json({ error: 'Authentication required' }, { status: 401 })
  }
  const token = authHeader.replace('Bearer ', '')
  const { data: { user }, error } = await supabase.auth.getUser(token)
  if (error || !user) {
    return NextResponse.json({ error: 'Invalid token' }, { status: 401 })
  }

  // Check admin role
  const { data: profile } = await supabase.from('profiles').select('role').eq('id', user.id).single()
  if (profile?.role !== 'admin') {
    return NextResponse.json({ error: 'Admin access required' }, { status: 403 })
  }

  // Gather stats
  const [potentials, contributions, users] = await Promise.all([
    supabase.from('potentials').select('id, type, source', { count: 'exact' }),
    supabase.from('contributions').select('id, status, action', { count: 'exact' }),
    (supabaseAdmin || supabase).from('profiles').select('id, role', { count: 'exact' }),
  ])

  const stats = {
    totalPotentials: potentials.count || 0,
    potentialsByType: groupBy(potentials.data || [], 'type'),
    potentialsBySource: groupBy(potentials.data || [], 'source'),
    totalContributions: contributions.count || 0,
    pendingContributions: contributions.data?.filter(c => c.status === 'pending').length || 0,
    totalUsers: users.count || 0,
    usersByRole: groupBy(users.data || [], 'role'),
  }

  return NextResponse.json(stats)
}

function groupBy(arr: any[], key: string): Record<string, number> {
  return arr.reduce((acc, item) => {
    const k = item[key] || 'unknown'
    acc[k] = (acc[k] || 0) + 1
    return acc
  }, {})
}
